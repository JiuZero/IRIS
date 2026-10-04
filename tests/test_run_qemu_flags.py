"""The QEMU command line, guarded where it is assembled.

``run_qemu.sh`` builds the device model in shell, so there is nothing in Python
to import and no way to unit-test the resulting argument list. That has already
cost one debugging session: the ARMEL guest came up with its disk mounted and no
network interface at all, which reads exactly like a kernel without a NIC driver —
and the actual cause was the transport the virtio-mmio bus negotiated. Nothing in
the log distinguishes the two, so the argument that fixes it is asserted here,
where a removal is a failing test rather than a mystery.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

RUN_QEMU = Path(__file__).resolve().parents[1] / "scripts" / "emulate" / "run_qemu.sh"
TEXT = RUN_QEMU.read_text(encoding="utf-8")


def _shell() -> str:
    for candidate in (shutil.which("bash"), "C:/Program Files/Git/usr/bin/bash.exe"):
        if candidate and Path(candidate).exists():
            probe = subprocess.run([candidate, "-c", "echo ok"], capture_output=True, text=True, check=False)
            if probe.returncode == 0 and probe.stdout.strip() == "ok":
                return candidate
    pytest.skip("no working POSIX shell available")


@pytest.fixture(scope="module")
def bash() -> str:
    return _shell()


def _qemu_invocations() -> list[str]:
    """Every complete qemu command line, with the backslash continuations joined."""
    return [block.replace("\\\n", " ") for block in re.findall(r"^\$\{QEMU\} .*?&$", TEXT, re.MULTILINE | re.DOTALL)]


class TestVirtioTransport:
    def test_the_bus_is_forced_onto_the_modern_transport(self):
        """Without this the ARMEL guest gets a disk and no NIC, silently."""
        assert "-global virtio-mmio.force-legacy=false" in TEXT

    def test_the_flag_is_on_the_qemu_command_line_not_just_mentioned(self):
        assert any("-global virtio-mmio.force-legacy=false" in inv
                   for inv in _qemu_invocations()), _qemu_invocations()

    def test_it_is_a_global_and_not_a_device_property(self):
        """Per-device, it would fail to start: the property is bus-level."""
        assert "force-legacy=false,id=" not in TEXT
        assert "virtio-mmio.force-legacy=false," not in TEXT

    def test_the_reason_is_recorded_next_to_the_flag(self):
        """A future reader must not delete it as noise for the mips machines."""
        assert "force-legacy" in TEXT
        assert "virtio_net built in" in TEXT


class TestSerialChardev:
    """The console is a socket now, and the log file stays where it was.

    Both halves are load-bearing and neither is visible from the other. Drop the
    ``logfile=`` and every reader of ``qemu.serial.log`` -- the orchestrator's
    three reads, the reboot counter, the copy back to the host -- finds nothing,
    while the console keeps working perfectly, so the run still reports a normal
    failure. Drop ``server=on``/``wait=off`` and QEMU either refuses the port or
    blocks the whole boot waiting for a browser that has not connected yet, which
    is a hang rather than a boot. So each option is asserted on its own.
    """

    def _chardev(self) -> str:
        found = [inv for inv in _qemu_invocations() if "-chardev" in inv]
        assert found, _qemu_invocations()
        return found[0]

    def test_the_console_is_the_chardev_not_a_file_sink(self):
        assert "-serial chardev:iris_serial" in self._chardev()

    def test_the_one_way_file_sink_is_gone(self):
        """`-serial file:` accepts bytes but has no way to send any, so a console
        built on it can never be typed into."""
        assert "-serial file:" not in TEXT

    def test_the_same_log_path_is_still_written(self):
        assert "logfile=${WORK_DIR}/qemu.serial.log" in self._chardev()

    def test_the_chardev_serves_without_waiting_for_a_client(self):
        """`wait=on` holds the boot until something connects; nothing connects
        until the boot has produced something to look at."""
        chardev = self._chardev()
        assert "server=on" in chardev
        assert "wait=off" in chardev

    def test_it_binds_every_interface_inside_the_container(self):
        """A chardev on 127.0.0.1 in the container is unreachable through the
        published port, so the console would connect and then see nothing."""
        assert "host=0.0.0.0" in self._chardev()

    def test_the_port_is_taken_from_the_environment(self):
        """A fourth positional argument would make every caller that only wants the
        web forward invent a console port it never uses."""
        assert 'SERIAL_PORT=${IRIS_SERIAL_PORT:-0}' in TEXT
        assert "port=${SERIAL_PORT}" in self._chardev()


class TestTheSerialPortIsRefusedWhenAbsent:
    """No port means no console, and a run without a console is not a degraded
    run: it looks identical to a working one. Booting anyway would report a
    normal-looking emulation whose terminal cannot be typed into, so an absent or
    nonsensical port has to stop the launch instead.
    """

    def test_an_unset_port_is_rejected(self):
        assert 'if [ "${SERIAL_PORT}" -lt 1024 ] || [ "${SERIAL_PORT}" -gt 65535 ]; then' in TEXT

    def test_the_rejection_exits_before_qemu(self):
        guard = TEXT.split('if [ "${SERIAL_PORT}" -lt 1024 ]')[1]
        assert "exit 1" in guard.split("${QEMU} -m")[0]

    def test_the_message_says_which_variable_to_set(self):
        assert "IRIS_SERIAL_PORT must be a published TCP port" in TEXT


class TestNetworkDevice:
    def test_armel_gets_a_net_device(self):
        block = TEXT.split("armel", 1)
        assert len(block) > 1
        assert "virtio-net-device" in block[1].split("mips", 1)[0]

    def test_the_firmae_net_kernel_flag_is_set(self):
        assert "FIRMAE_NET=true" in TEXT

    def test_no_stale_disable_legacy_property_survives(self):
        """QEMU 6.2 rejects it on a modern-only device: the process dies at once."""
        assert "disable-legacy" not in TEXT


class TestShellSyntax:
    def test_the_script_parses(self, bash):
        proc = subprocess.run([bash, "-n", str(RUN_QEMU)], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr

class TestTheStateDisk:
    """The disk QEMU writes is kept across launches; the baked one is not.

    The copy used to be made fresh on every launch and deleted on exit, so a guest's
    runtime writes -- and anything a repair injected into them -- were gone before the
    next boot could act on them. Both halves of that are asserted here because the
    failure is silent: a boot that starts normally off a pristine disk looks exactly
    like a boot that is behaving correctly.
    """

    def test_the_disk_given_to_qemu_is_the_state_disk(self):
        assert 'IMAGE="${STATE_IMAGE}"' in TEXT

    def test_the_state_disk_is_created_only_when_it_is_missing(self):
        """Re-copying unconditionally is the old behaviour wearing a new filename:
        it would overwrite whatever the previous boot wrote."""
        assert 'if [ ! -f "${STATE_IMAGE}" ]; then' in TEXT
        assert 'cp "${IMAGE}" "${STATE_IMAGE}"' in TEXT

    def test_it_is_not_deleted_on_exit(self):
        """Nothing may remove the disk on the way out, by any spelling -- that
        deletion is what made every relaunch start from the baked image again."""
        cleanup = TEXT.split("# Cleanup")[-1]
        for stale in ('rm -f "${STATE_IMAGE}"', 'rm -f "${IMAGE}"',
                      'rm -f "${TMP_IMAGE}"'):
            assert stale not in cleanup, stale

    def test_the_state_disk_lives_next_to_the_image_not_in_tmp(self):
        """It has to outlive the process, and `/tmp` is what the old copy used."""
        assert 'STATE_IMAGE=${WORK_DIR}/state.raw' in TEXT
        assert "/tmp/qemu-" not in TEXT

    def test_a_dirty_filesystem_is_checked_after_the_guest_goes_away(self):
        """`docker restart -t 10` SIGKILLs QEMU, and ext2 has no journal to replay a
        kill on the next mount -- the next boot would fail on a filesystem the
        firmware is not responsible for."""
        cleanup = TEXT.split("# Cleanup")[-1]
        assert "e2fsck -p" in cleanup

    def test_the_check_cannot_abort_the_rest_of_the_cleanup(self):
        """`set -e` plus a non-zero e2fsck would skip the TAP teardown and leave the
        next launch unable to create its bridge."""
        assert 'e2fsck -p "${IMAGE}" || echo' in TEXT


class TestTheBakedImageIsRebuiltClean:
    MAKE_IMAGE = Path(__file__).resolve().parents[1] / "scripts" / "emulate" / "make_image.sh"

    def test_a_rebake_drops_the_previous_state_disk(self):
        """`run_qemu.sh` only creates a state disk when it is missing, so a leftover
        from the previous image would boot the new image with the old one's writes."""
        assert 'rm -f "${WORK_DIR}/state.raw"' in self.MAKE_IMAGE.read_text(encoding="utf-8")
