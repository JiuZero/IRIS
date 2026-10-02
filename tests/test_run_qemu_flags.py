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