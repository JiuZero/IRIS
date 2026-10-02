"""Tests for the boot-failure diagnosis in iris.emulate.orchestrator."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from iris.emulate.orchestrator import (
    REBOOT_LOOP_THRESHOLD,
    _count_guest_reboots,
    _failure_diagnosis,
    diagnose_boot_failure,
)

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

    def test_wrong_web_port_is_the_whole_story(self):
        out = diagnose_boot_failure(_HEALTHY)
        assert "nginx" in out and "bound nginx:80" in out
        assert "unreachable through the forward" in out

    def test_non_eighty_bind_is_called_out(self):
        assert "bound nginx:8180" in diagnose_boot_failure(_AC15)

    def test_duplicate_binds_are_reported_once(self):
        log = _HEALTHY + ("inet_bind[PID: 97 (nginx)]: proto:SOCK_STREAM, port:8180\n" * 3)
        assert "8180" in diagnose_boot_failure(log)

    def test_web_server_without_a_bind_line(self):
        out = diagnose_boot_failure("nginx: master process\n")
        assert "port unknown" in out

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