"""Tests for the emulate arch preflight: ELF census vs requested QEMU arch."""

import struct

from iris.emulate.orchestrator import preflight_arch
from iris.failures import FailureKind, Stage


# ei_data: 1=LE 2=BE; e_machine: 8=MIPS 40=ARM 183=AArch64
def _elf(machine: int, ei_data: int, ei_class: int = 1) -> bytes:
    # ei_class defaults to 1 (32-bit). It used to be hardcoded to 2, which made
    # every fixture in this file a 64-bit binary: the census ignored EI_CLASS, so
    # a mips64 guest was reported as `mipsel` and these tests passed against a
    # lie. Reading the class is what turned them red.
    hdr = bytearray(b"\x7fELF" + bytes([ei_class, ei_data, 1]) + b"\x00" * 9)
    hdr += b"\x02\x00"  # e_type = EXEC
    hdr += struct.pack("<H" if ei_data == 1 else ">H", machine)
    return bytes(hdr)


def _rootfs(tmp_path, n_elf: int, machine: int, ei_data: int, ei_class: int = 1):
    d = tmp_path / "rootfs"
    d.mkdir()
    bindir = d / "bin"
    bindir.mkdir()
    for i in range(n_elf):
        (bindir / f"prog{i}").write_bytes(_elf(machine, ei_data, ei_class))
    return d


class TestPreflightArch:
    def test_matching_arch_passes(self, tmp_path):
        root = _rootfs(tmp_path, 3, 8, 1)  # mipsel
        assert preflight_arch(root, "mipsel") is None

    def test_aarch64_under_mipsel_rejected(self, tmp_path):
        root = _rootfs(tmp_path, 4, 183, 1)  # aarch64
        problem = preflight_arch(root, "mipsel")
        assert problem.kind is FailureKind.ARCH_MISMATCH
        assert "aarch64" in problem.detail and "arm64" in problem.detail
        # The recorded evidence is what makes the suggestion checkable rather
        # than a guess the caller has to re-derive.
        assert problem.evidence["suggested"] == "arm64"

    def test_unsupported_requested_arch(self, tmp_path):
        root = _rootfs(tmp_path, 1, 8, 1)
        problem = preflight_arch(root, "ppc")
        assert problem.kind is FailureKind.UNSUPPORTED_ARCH
        assert problem.evidence["supported"]

    def test_no_elf_evidence_passes(self, tmp_path):
        d = tmp_path / "rootfs"
        (d / "etc").mkdir(parents=True)
        (d / "etc" / "rcS").write_text("#!/bin/sh\n", newline="\n", encoding="utf-8")
        assert preflight_arch(d, "mipsel") is None

    def test_armel_rootfs_passes(self, tmp_path):
        root = _rootfs(tmp_path, 2, 40, 1)
        assert preflight_arch(root, "armel") is None

    def test_mipseb_rootfs_under_mipsel_flagged(self, tmp_path):
        root = _rootfs(tmp_path, 2, 8, 2)  # big-endian MIPS
        problem = preflight_arch(root, "mipsel")
        assert problem.kind is FailureKind.ARCH_MISMATCH
        assert "mipseb" in problem.detail

    def test_the_message_still_carries_the_kind_prefix(self, tmp_path):
        """Every log line and CLI message reads `error`, not the Failure object.
        Losing the prefix would quietly downgrade the taxonomy back to prose."""
        root = _rootfs(tmp_path, 2, 8, 2)
        assert preflight_arch(root, "mipsel").message.startswith("arch-mismatch: ")

    def test_arch_failures_land_in_the_arch_stage(self, tmp_path):
        root = _rootfs(tmp_path, 1, 8, 1)
        assert preflight_arch(root, "ppc").stage is Stage.ARCH

class TestAnArchitectureWithNoKernel:
    """A real architecture the census names but nothing can boot.

    Silently starting it under the nearest kernel fails as "the emulator is
    broken", which is indistinguishable from a bug in IRIS -- the guest runs the
    init script and then misexecs, or never boots at all. Saying "there is no
    kernel for this" is the answer that lets the user do something about it.
    """

    def test_mips64_is_refused_rather_than_started_on_a_32_bit_kernel(self, tmp_path):
        root = _rootfs(tmp_path, 2, 8, 1, ei_class=2)  # 64-bit MIPS
        problem = preflight_arch(root, "mipsel")
        assert problem.kind is FailureKind.UNSUPPORTED_ARCH
        assert "mips64le" in problem.detail

    def test_big_endian_arm_is_refused_rather_than_started_on_the_little_endian_kernel(self, tmp_path):
        root = _rootfs(tmp_path, 2, 40, 2)  # armeb
        problem = preflight_arch(root, "armel")
        assert problem.kind is FailureKind.UNSUPPORTED_ARCH
        assert "armeb" in problem.detail

    def test_the_32_bit_counterpart_still_passes(self, tmp_path):
        """The guard must not fire on the architecture it was written for: the
        two cases differ only in EI_DATA, so a check that ignored the byte order
        would reject mipsel too."""
        root = _rootfs(tmp_path, 2, 8, 2)  # mipseb
        assert preflight_arch(root, "mipseb") is None


class TestArchSpellingsAreNormalised:
    def test_the_elf_name_of_a_supported_arch_is_accepted(self, tmp_path):
        """`aarch64` is what an ELF header says; `arm64` is what the kernel asset
        is called. A caller that read the architecture off the header and passed
        it through was told "unsupported arch" for an architecture that works."""
        root = _rootfs(tmp_path, 2, 183, 1)
        assert preflight_arch(root, "aarch64") is None

    def test_the_old_endianness_suffixed_spelling_still_works(self, tmp_path):
        """Older databases and log lines carry `arm64le`. Accepting them costs
        one dict entry and saves a silent unsupported-arch."""
        root = _rootfs(tmp_path, 2, 183, 1)
        assert preflight_arch(root, "arm64le") is None