"""Tests for the emulate arch preflight: ELF census vs requested QEMU arch."""

import struct

from iris.emulate.orchestrator import preflight_arch

# ei_data: 1=LE 2=BE; e_machine: 8=MIPS 40=ARM 183=AArch64
def _elf(machine: int, ei_data: int) -> bytes:
    hdr = bytearray(b"\x7fELF" + bytes([2, ei_data, 1]) + b"\x00" * 9)
    hdr += b"\x02\x00"  # e_type = EXEC
    hdr += struct.pack("<H" if ei_data == 1 else ">H", machine)
    return bytes(hdr)


def _rootfs(tmp_path, n_elf: int, machine: int, ei_data: int):
    d = tmp_path / "rootfs"
    d.mkdir()
    bindir = d / "bin"
    bindir.mkdir()
    for i in range(n_elf):
        (bindir / f"prog{i}").write_bytes(_elf(machine, ei_data))
    return d


class TestPreflightArch:
    def test_matching_arch_passes(self, tmp_path):
        root = _rootfs(tmp_path, 3, 8, 1)  # mipsel
        assert preflight_arch(root, "mipsel") == ""

    def test_aarch64_under_mipsel_rejected(self, tmp_path):
        root = _rootfs(tmp_path, 4, 183, 1)  # aarch64
        msg = preflight_arch(root, "mipsel")
        assert msg.startswith("arch-mismatch")
        assert "aarch64" in msg and "arm64" in msg

    def test_unsupported_requested_arch(self, tmp_path):
        root = _rootfs(tmp_path, 1, 8, 1)
        msg = preflight_arch(root, "ppc")
        assert msg.startswith("unsupported-arch")

    def test_no_elf_evidence_passes(self, tmp_path):
        d = tmp_path / "rootfs"
        (d / "etc").mkdir(parents=True)
        (d / "etc" / "rcS").write_text("#!/bin/sh\n", newline="\n", encoding="utf-8")
        assert preflight_arch(d, "mipsel") == ""

    def test_armel_rootfs_passes(self, tmp_path):
        root = _rootfs(tmp_path, 2, 40, 1)
        assert preflight_arch(root, "armel") == ""

    def test_mipseb_rootfs_under_mipsel_flagged(self, tmp_path):
        root = _rootfs(tmp_path, 2, 8, 2)  # big-endian MIPS
        msg = preflight_arch(root, "mipsel")
        assert msg.startswith("arch-mismatch") and "mipseb" in msg
