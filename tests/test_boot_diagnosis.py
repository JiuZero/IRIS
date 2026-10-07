"""Tests for the boot-failure diagnosis in iris.emulate.orchestrator."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from iris.emulate.orchestrator import (
    _FORWARDED_PORT,
    _HAS_NON_LO_IP,
    _NIC_ABSENT,
    _NIC_PRESENT,
    REBOOT_LOOP_THRESHOLD,
    _count_guest_reboots,
    _failure_diagnosis,
    _forward_target,
    _kernel_crash_findings,
    _read_binds,
    diagnose_boot_failure,
)
from iris.failures import FailureKind

_HEALTHY = """
IRIS-NETFIX: no interface has an IP, assigning fallback 192.168.1.1 to eth0
IRIS-NETFIX: final: eth0 up with IP
virtio_net: module registered
inet addr:192.168.1.1  Bcast:192.168.1.255  Mask:255.255.255.0
inet_bind[PID: 96 (nginx)]: proto:SOCK_STREAM, port:80
"""

# The real AC15 serial log, abridged to the parts each probe reads. Note it has
# no IRIS-NETFIX line at all: that is the whole point of this fixture.
_AC15 = """
Could not open mtd device 2
envram_init: read flash error
###nvram partition is destory, restore by default and reboot##
/usr/sbin/cfmd: segfault at 0x00000000
**cfmd recv segv signals and reboot the system**
recv segv signals
The system is going down NOW!
Sent SIGTERM to all processes
Requesting system reboot
firmadyne: sys_reboot[PID: 172 (init)]: magic1:fee1dead
init server success
inet_bind[PID: 96 (nginx)]: proto:SOCK_STREAM, port:8180
"""


class TestDiagnoseBootFailure:
    def test_prefix_names_the_failure(self):
        assert diagnose_boot_failure("").startswith("emulation failed: ")

    def test_reboot_loop_is_reported_first(self):
        out = diagnose_boot_failure(_AC15, reboots=REBOOT_LOOP_THRESHOLD)
        assert out.index("requested a kernel reboot") < out.index("no non-loopback")

    def test_reboot_loop_is_quiet_below_the_threshold(self):
        out = diagnose_boot_failure(_AC15, reboots=REBOOT_LOOP_THRESHOLD - 1)
        assert "requested a kernel reboot" not in out

    def test_missing_netfix_hooks_are_named(self):
        out = diagnose_boot_failure(_AC15)
        assert "boot hooks did not run" in out

    def test_netfix_presence_is_counted(self):
        out = diagnose_boot_failure(_HEALTHY)
        assert "IRIS network fallback ran (2 log lines)" in out

    def test_fallback_ip_report_counts_as_an_address(self):
        """The guest-side line is the signal that actually appears in real logs."""
        assert "no non-loopback address" not in diagnose_boot_failure(_HEALTHY)

    def test_ifconfig_output_counts_as_an_address(self):
        log = "eth0      Link encap:Ethernet\n          inet addr:10.0.0.1  Bcast:10.0.0.255\n"
        assert "no non-loopback address" not in diagnose_boot_failure(log)

    def test_loopback_only_address_is_not_counted(self):
        log = "lo        Link encap:Local Loopback\n          inet addr:127.0.0.1  Mask:255.0.0.0\n"
        assert "no non-loopback address" in diagnose_boot_failure(log)

    def test_fallback_report_on_lo_alone_is_not_counted(self):
        log = "IRIS-NETFIX: final: lo up with IP\n"
        assert "no non-loopback address" in diagnose_boot_failure(log)

    def test_missing_nic_driver_is_named(self):
        out = diagnose_boot_failure("IRIS-NETFIX: starting\n")
        assert "no network driver registered" in out

    def test_eth_interface_alone_counts_as_a_nic(self):
        out = diagnose_boot_failure("eth0: link becomes ready\n")
        assert "no network driver registered" not in out

    def test_a_vendor_config_string_is_not_a_nic(self):
        """AC15 prints nvram_set: wan_ifname = "eth0" with no interface behind it."""
        out = diagnose_boot_failure('nvram_set: wan_ifname = "eth0"\nnvram_set: lan_ifnames = "eth1 eth2 eth3 eth4"\n')
        assert "no network driver registered" in out

    def test_virtio_net_registration_counts_as_a_nic(self):
        out = diagnose_boot_failure("virtio_net: module registered\n")
        assert "no network driver registered" not in out

    def test_eighty_bind_is_not_called_out_as_the_reason(self):
        """A guest that bound the forwarded port cannot have failed for the port.

        iid 6715 reported "a web server did start (uhttpd) but bound
        dropbear:22, uhttpd:80, uhttpd:443 — anything other than :80 is
        unreachable through the forward", naming :80 in the very detail that
        blamed it. The finding had to be able to read its own evidence.
        """
        out = diagnose_boot_failure(_HEALTHY)
        assert "nginx" in out and "bound nginx:80" in out
        assert "the port is not why :80 never answered" in out
        assert "unreachable through the forward" not in out

    def test_the_forwarded_port_among_several_keeps_the_verdict_off_the_port(self):
        """dropbear:22 alongside uhttpd:80 and uhttpd:443 -- the real 6715 binds."""
        out = diagnose_boot_failure(
            "inet_bind[PID: 90 (dropbear)]: proto:SOCK_STREAM, port:22\n"
            "inet_bind[PID: 91 (uhttpd)]: proto:SOCK_STREAM, port:80\n"
            "inet_bind[PID: 91 (uhttpd)]: proto:SOCK_STREAM, port:443\n")
        assert "which includes the :80 the forward targets" in out
        assert "unreachable through the forward" not in out

    def test_non_eighty_bind_is_called_out(self):
        assert "bound nginx:8180" in diagnose_boot_failure(_AC15)

    def test_a_non_eighty_bind_says_which_port_is_missing(self):
        out = diagnose_boot_failure(_AC15)
        assert "none of which is :80" in out
        assert "the forward only reaches :80" in out

    def test_duplicate_binds_are_reported_once(self):
        log = _HEALTHY + ("inet_bind[PID: 97 (nginx)]: proto:SOCK_STREAM, port:8180\n" * 3)
        assert "8180" in diagnose_boot_failure(log)

    def test_binds_are_listed_in_the_order_they_booted(self):
        out = diagnose_boot_failure(
            "inet_bind[PID: 91 (uhttpd)]: proto:SOCK_STREAM, port:8180\n"
            "inet_bind[PID: 90 (dropbear)]: proto:SOCK_STREAM, port:22\n")
        assert out.index("uhttpd:8180") < out.index("dropbear:22")

    def test_busybox_bind_shapes_are_read_too(self):
        """Not every kernel prints FirmAE's rewording of inet_bind."""
        for line in ("net_bind: bind 0.0.0.0:80", "Listen on 0.0.0.0:8180"):
            assert _read_binds(f"uhttpd started\n{line}\n") == [("", line.rsplit(":", 1)[1])]

    def test_web_server_without_a_bind_line(self):
        """No port in the log means no port may be blamed."""
        out = diagnose_boot_failure("nginx: master process\n")
        assert "names no port for it" in out
        assert "cannot be confirmed or ruled out" in out
        assert "none of which is :80" not in out

    def test_no_web_server_at_all(self):
        assert "no known web server process" in diagnose_boot_failure("init server success\n")

    def test_ac15_log_names_every_broken_link(self):
        out = diagnose_boot_failure(_AC15, reboots=4)
        assert "requested a kernel reboot 4 times" in out
        assert "boot hooks did not run" in out
        assert "no non-loopback address" in out
        assert "no network driver registered" in out
        assert "bound nginx:8180" in out

    def test_diagnosis_reads_as_one_sentence(self):
        body = diagnose_boot_failure(_AC15, reboots=4)
        assert body.startswith("emulation failed: ") and body.endswith(".")
        assert "; ;" not in body and body.count("..") == 0


class TestRebootCauseIsNamed:
    """A reboot loop has more than one cause and they need different fixes."""

    def _with(self, marker: str) -> str:
        return diagnose_boot_failure(f"{marker}\nfirmadyne: sys_reboot\n", reboots=3)

    def test_a_destroyed_nvram_partition_is_named(self):
        out = self._with("###nvram partition is destory, restore by default and reboot##")
        assert "nvram partition unreadable" in out
        assert "flash partitions are not being emulated" in out

    def test_a_flash_read_failure_is_named(self):
        out = self._with("envram_init: read flash error")
        assert "reading the emulated flash failed" in out

    def test_a_missing_mtd_device_is_named(self):
        out = self._with("Could not open mtd device 2")
        assert "mtd device" in out

    def test_a_daemon_segfault_is_named_as_a_vendor_bug(self):
        """Removing the reboot binary cannot help here, and the report must say so."""
        out = self._with("**cfmd recv segv signals and reboot the system**")
        assert "segfaulted into a reboot call" in out
        assert "no amount of removing the reboot binary" in out

    def test_the_first_matching_trigger_wins(self):
        out = self._with("Could not open mtd device 2\nenvram_init: read flash error")
        assert "emulated flash failed" in out

    def test_an_unnamed_trigger_says_so_rather_than_guessing(self):
        out = self._with("something nobody has seen before")
        assert "for a reason the log does not name" in out

    def test_the_real_ac15_log_blames_the_nvram_partition(self):
        text = (
            Path(__file__).resolve().parents[1] / "iris-home/scratch/emulate-4218/qemu.serial.log"
        )
        try:
            log = text.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pytest.skip("AC15 serial log not present in this checkout")
        assert "nvram partition unreadable" in diagnose_boot_failure(log, reboots=3)


#: The panic that never found an init. iid 9591 logged exactly this at 1.03s and
#: has no inet_bind line anywhere in 300 lines of boot output.
_NO_INIT_PANIC = """
[    0.000000] Linux version 4.1.17+ (root@build) #18
[    1.034926] Kernel panic - not syncing: No working init found.  Try passing init= option to kernel.
[    1.035319] ---[ end Kernel panic - not syncing: No working init found.
"""

#: iid 6715, abridged. uhttpd bound :80 on line 536 and netifd took this Oops on
#: line 678 -- the guest served its web plane with a dead network daemon in it,
#: and the run was recorded as a clean success.
_OOPS_AFTER_BIND = """
IRIS-NETFIX: no interface has an IP, assigning fallback 192.168.1.1 to eth0
IRIS-NETFIX: final: eth0 up with IP
virtio_net: module registered
inet_bind[PID: 21271 (uhttpd)]: proto:SOCK_STREAM, port:80
[  119.837486] Internal error: Oops - BUG: 0 [#1] ARM
[  119.837854] CPU: 0 PID: 1053 Comm: netifd Tainted: G        W       4.1.17+ #18
[  119.838001] PC is at validate_nla+0x3c/0x1a0
[  119.838438] Process netifd (pid: 1053, stack limit = 0xcef80208)
"""


class TestGuestKernelCrashIsNotAnInvisibleVerdict:
    """A guest can serve its web plane and still have a crashed kernel behind it.

    iid 6715 answered HTTP 200 with netifd already dead, and the record said only
    "success: True" -- the backtrace sat in the serial log underneath the verdict,
    so nobody reading the history could tell the two apart from a healthy run.
    """

    def test_an_oops_is_found_and_quoted(self):
        out = diagnose_boot_failure(_OOPS_AFTER_BIND)
        assert "the guest kernel took an exception and kept running" in out
        assert "Internal error: Oops - BUG: 0 [#1] ARM" in out

    def test_an_oops_names_the_process_it_killed(self):
        out = diagnose_boot_failure(_OOPS_AFTER_BIND)
        assert "the process it killed was Process netifd" in out

    def test_an_oops_does_not_turn_a_working_guest_into_a_failure(self):
        """The mirror of the bug: calling a served guest failed is the same mistake."""
        findings = diagnose_boot_failure(_OOPS_AFTER_BIND, web_reachable=True).findings
        kinds = {f.kind for f in findings}
        assert FailureKind.GUEST_KERNEL_OOPS in kinds
        assert not [f for f in findings if f.is_failure], "the probe invented a failure"

    def test_a_guest_that_answered_is_never_told_it_never_did(self):
        """A boot log cannot see an HTTP response; the caller's curl can."""
        out = diagnose_boot_failure(_OOPS_AFTER_BIND, web_reachable=True)
        assert "web-unreachable" not in str(out)
        assert "never answered" not in out

    def test_the_web_probes_still_run_when_the_caller_did_not_measure(self):
        """A log-only diagnosis has nothing else to work from."""
        out = diagnose_boot_failure(_HEALTHY)
        assert "which includes the :80 the forward targets" in out

    def test_an_oops_is_still_reported_next_to_the_working_web_plane(self):
        out = diagnose_boot_failure(_OOPS_AFTER_BIND)
        assert "bound uhttpd:80" in out, "the web plane is real and must still be named"
        assert "the guest kernel took an exception" in out

    def test_a_panic_the_kernel_could_not_pass_is_a_failure(self):
        kinds = {f.kind for f in diagnose_boot_failure(_NO_INIT_PANIC).findings}
        assert FailureKind.GUEST_KERNEL_PANIC in kinds

    def test_a_panic_quotes_the_reason_rather_than_saying_the_kernel_stopped(self):
        out = diagnose_boot_failure(_NO_INIT_PANIC)
        assert "No working init found" in out
        assert "nothing in userspace was ever going to come up" in out

    def test_the_kernel_crash_leads_the_diagnosis(self):
        """A kernel that is down explains every link after it."""
        out = diagnose_boot_failure(_NO_INIT_PANIC)
        assert out.index("the guest kernel stopped") < out.index("no network driver")

    def test_a_healthy_log_reports_no_kernel_crash(self):
        out = diagnose_boot_failure(_HEALTHY)
        assert "guest kernel" not in out

    @pytest.mark.parametrize("line", [
        "Kernel panic - not syncing: No working init found.",
        "---[ end Kernel panic - not syncing: VFS: Cannot open root device \"hda\"",
        "VFS: Unable to mount root fs via unknown device",
    ])
    def test_each_panic_spelling_is_recognised(self, line: str):
        assert _kernel_crash_findings(line), line

    @pytest.mark.parametrize("line", [
        "IRIS-NETFIX: no interface has an IP, assigning fallback 192.168.1.1",
        "could not open /dev/mtd2 (No such file or directory)",
        "nvram_set: wan_ifname = \"eth0\"",
    ])
    def test_an_ordinary_boot_message_is_not_read_as_a_kernel_crash(self, line: str):
        assert _kernel_crash_findings(line) == []

    def test_the_real_6715_log_does_crash_inside_the_kernel(self):
        text = (
            Path(__file__).resolve().parents[1] / "iris-home/scratch/emulate-6715/qemu.serial.log"
        )
        try:
            log = text.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pytest.skip("6715 serial log not present in this checkout")
        kinds = {f.kind for f in diagnose_boot_failure(log).findings}
        assert FailureKind.GUEST_KERNEL_OOPS in kinds

    def test_the_real_9591_log_panics_for_real(self):
        text = (
            Path(__file__).resolve().parents[1] / "iris-home/scratch/emulate-9591/qemu.serial.log"
        )
        try:
            log = text.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pytest.skip("9591 serial log not present in this checkout")
        kinds = {f.kind for f in diagnose_boot_failure(log).findings}
        assert FailureKind.GUEST_KERNEL_PANIC in kinds

    def test_the_9591_panic_names_where_to_look(self):
        """"No working init found" stops the guest exactly as dead as a hardware
        panic, but the work it calls for is different: the rootfs mount and the
        injected init, not a driver. The wording must say which."""
        out = diagnose_boot_failure(_NO_INIT_PANIC)
        assert "found no init to run" in out
        assert "the rootfs mount, root= and the injected init are where to look" in out

    def test_a_rootfs_volume_panic_points_at_the_block_device(self):
        """The opposite work: the kernel never attached the volume, so the reader
        goes after root='s block device and its driver, not after an init."""
        line = '[    1.034926] Kernel panic - not syncing: VFS: Cannot open root device "hda"'
        out = diagnose_boot_failure(line)
        assert "never attaching the rootfs volume" in out
        assert "the block device behind root= and its driver are where to look" in out

    def test_an_unclassified_panic_invents_no_cause(self):
        """A panic that matches none of the real lines keeps the generic wording:
        inventing a cause is how a wrong hint gets recorded with the same
        confidence as a measured one."""
        finding = _kernel_crash_findings(
            "[    1.0] Kernel panic - not syncing: Attempted to kill init!"
        )[0]
        assert "Attempted to kill init" in finding.message
        assert "where to look" not in finding.message
        assert finding.evidence["panic_class"] == "unclassified"

    def test_the_panic_class_lands_in_the_evidence(self):
        finding = _kernel_crash_findings(_NO_INIT_PANIC)[0]
        assert finding.evidence["panic_class"] == "init-lookup"
        assert finding.evidence["log_line"].startswith("[")


class TestForwardTarget:
    """Whether a guest with no forward can ever answer, decided in one place.

    iid 6715 spent its whole 240s timeout answering HTTP 000 and was blamed on the
    wrong port. It was not a port problem: its guest address was 192.168.1.1, the
    same address the boot loop starts from, so the "did the address change" gate
    never opened, no socat forward was ever created, and nothing was listening on
    the host port to curl.
    """

    def test_the_default_assumption_still_gets_a_forward(self):
        """The regression itself: a guest on the assumed address must be forwarded."""
        assert _forward_target("192.168.1.1", None) == "192.168.1.1"

    def test_a_different_address_gets_a_forward_too(self):
        assert _forward_target("192.168.0.1", None) == "192.168.0.1"

    def test_an_already_forwarded_address_is_left_alone(self):
        """Rebuilding the forward every poll would restart a working listener."""
        assert _forward_target("192.168.0.1", "192.168.0.1") is None

    def test_a_changed_address_re_forwards(self):
        assert _forward_target("10.0.0.1", "192.168.0.1") == "10.0.0.1"

    def test_loopback_is_never_a_forward_target(self):
        assert _forward_target("127.0.0.1", None) is None

    def test_no_detection_means_no_forward(self):
        assert _forward_target(None, None) is None

    def test_the_forwarded_port_is_the_one_the_socat_forward_targets(self):
        """Guards the coupling between the socat command and the diagnosis."""
        assert _FORWARDED_PORT == "80"


class TestCountGuestReboots:
    def test_reads_the_count_off_stdout(self, monkeypatch):
        seen: dict = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="7\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _count_guest_reboots("iris-qemu-4218", 4218) == 7
        assert seen["cmd"][:3] == ["docker", "exec", "iris-qemu-4218"]
        assert "firmadyne: sys_reboot" in seen["cmd"]

    def test_zero_matches_exits_one_but_still_reports_zero(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 1, stdout="0\n", stderr=""))
        assert _count_guest_reboots("c", 1) == 0

    def test_unparsable_output_is_not_mistaken_for_a_loop(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="docker: no such container"))
        assert _count_guest_reboots("c", 1) == 0


class TestFailureDiagnosis:
    def test_serial_log_is_piped_into_the_diagnosis(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, stdout=_AC15, stderr=""))
        out = _failure_diagnosis("c", 1, reboots=3)
        assert out == diagnose_boot_failure(_AC15, reboots=3)

    def test_missing_container_still_reports_the_reboot_count(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="Error: No such container: c"))
        out = _failure_diagnosis("c", 1, reboots=5)
        assert "serial log unavailable" in out
        assert "No such container" in out
        assert "guest reboots: 5" in out

    def test_silence_does_not_crash(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, stdout="", stderr=""))
        assert _failure_diagnosis("c", 1).startswith("emulation failed: ")


#: Verbatim lines from the Tenda DIR-868L serial log (iid 6630). This guest had a
#: working network the whole time -- eth0 enslaved into br0, br0 holding
#: 192.168.0.1, httpd bound to :80 -- and was reported as having neither a NIC nor
#: an address. Both probes missed the FirmAE wording and the bridge wording below.
_DIR868L = """
[    6.051382] 8021q: adding VLAN 0 to HW filter on device eth0
[    6.237574] firmadyne: br_add_if[PID: 492 (brctl)]: br:br0 dev:eth0.1
[    6.238771] device eth0 entered promiscuous mode
[    6.180539] firmadyne: __inet_insert_ifa[PID: 483 (ip)]: device:lo ifa:0x0100007f
[   16.885524] firmadyne: __inet_insert_ifa[PID: 10045 (ip)]: device:br0 ifa:0x0100a8c0
nvram_set: wan_ifname = "eth0"
[   40.802430] firmadyne: inet_bind[PID: 21271 (httpd)]: proto:SOCK_STREAM, port:80
"""


class TestGuestNicAndAddressProbes:
    """Two probes that once agreed with each other on a wrong answer.

    Both were "is there a NIC" and "did anything get an address", both concluded
    no, and both conclusions were false for a guest whose log says br0 holds
    192.168.0.1 and httpd is on :80. The two failures reinforced each other, which
    is what makes them worth a fixture each.
    """

    def test_bridge_and_promuous_lines_count_as_a_present_nic(self):
        for line in _DIR868L.splitlines():
            if "8021q" in line or "br_add_if" in line or "promiscuous" in line:
                assert _NIC_PRESENT.search(line), line

    def test_the_firmadyne_address_printk_counts_as_an_assigned_address(self):
        assert _HAS_NON_LO_IP.search(
            "[   16.885524] firmadyne: __inet_insert_ifa[PID: 10045 (ip)]: "
            "device:br0 ifa:0x0100a8c0")

    def test_loopback_still_does_not_count(self):
        assert not _HAS_NON_LO_IP.search(
            "firmadyne: __inet_insert_ifa[PID: 483 (ip)]: device:lo ifa:0x0100007f")

    def test_config_naming_eth0_still_does_not_count_as_a_nic(self):
        """The false positive this whole probe exists to avoid."""
        assert not _NIC_PRESENT.search('nvram_set: wan_ifname = "eth0"')

    def test_a_missing_interface_is_not_read_as_a_present_one(self):
        line = "net: device eth0 not found"
        assert _NIC_PRESENT.search(line)
        assert _NIC_ABSENT.search(line), "the exclusion is what keeps this honest"

    def test_dir868l_is_neither_nicless_nor_addressless(self):
        out = diagnose_boot_failure(_DIR868L)
        assert "no network driver registered" not in out
        assert "no non-loopback address" not in out

    def test_dir868l_is_still_told_its_netfix_never_ran(self):
        """One guest, one line of IRIS-NETFIX, one hook failure -- and the fix was
        still worth nothing here, so this must not be papered over by the above."""
        out = diagnose_boot_failure("IRIS-NETFIX: bg launcher starting\n" + _DIR868L)
        assert "IRIS network fallback ran" in out


class TestRealSerialLogIsDiagnosable:
    """The function must survive real multi-thousand-line logs, not tidy fixtures."""

    def _read(self, rel: str) -> str:
        try:
            return (Path(__file__).resolve().parents[1] / rel).read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            pytest.skip(f"{rel} not present in this checkout")

    def test_ac15_scratch_log(self):
        text = self._read("iris-home/scratch/emulate-4218/qemu.serial.log")
        out = diagnose_boot_failure(text, reboots=text.count("firmadyne: sys_reboot"))
        assert re.search(r"bound \w+:\d+", out), "AC15 binds nginx:8180 and must be named"
        assert "requested a kernel reboot" in out
        assert "no network driver registered" in out

    def test_a_guest_that_did_get_an_address_is_not_accused(self):
        """Regression: inet_insert_ifa never appears on busybox firmwares, so a
        probe keyed on it alone reported a healthy guest as unconfigured."""
        text = self._read("iris-home/scratch/emulate-5255/qemu.serial.log")
        assert "IRIS-NETFIX: final: eth0 up with IP" in text, "sample log changed"
        assert "no non-loopback address" not in diagnose_boot_failure(text)
        assert "boot hooks did not run" not in diagnose_boot_failure(text)