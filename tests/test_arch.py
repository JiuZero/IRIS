"""Two vocabularies for architecture, and the guards that keep them apart.

`iris.arch` owns the *names* -- what an architecture is called, which spellings
mean the same one, and what its byte order is. `iris.extract.arch` reads ELF
headers and produces a *census* label. Both are tested here because the boundary
between them is where the mistakes were: the same firmware once answered to three
names (`aarch64`, `arm64`, `arm64le`) depending on which of four copies of the
mapping looked at it, and two of them reported a big-endian ARM binary as `armel`,
which boots and then misexecs.

The file is deliberately both halves in one place. Splitting them would make each
look self-contained, and the self-containment is exactly the illusion that let the
copies drift.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import ClassVar

import pytest

from iris.arch import (
    LITTLE_ENDIAN_ARCHS,
    RunnableArch,
    arch_endianness,
    census_label,
    census_to_runnable,
    class_bits,
    endianness_of,
    is_little_endian,
    normalize_arch,
)
from iris.extract.arch import (
    EM_AARCH64,
    EM_ARM,
    EM_I386,
    EM_MIPS,
    EM_X86_64,
    identify_elf,
)

PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src" / "iris"


def elf_header(bits: int, endian: int, e_machine: int) -> bytes:
    h = bytearray(20)
    h[0:4] = b"\x7fELF"
    h[4] = bits
    h[5] = endian
    fmt = "<" if endian == 1 else ">"
    h[18:20] = struct.pack(fmt + "H", e_machine)
    return bytes(h)


def test_mipsel():
    info = identify_elf(elf_header(1, 1, EM_MIPS))
    assert info is not None
    assert info.arch == "mipsel"
    assert info.runnable


def test_mipseb():
    info = identify_elf(elf_header(1, 2, EM_MIPS))
    assert info is not None
    assert info.arch == "mipseb"
    assert info.runnable


def test_armel():
    info = identify_elf(elf_header(1, 1, EM_ARM))
    assert info is not None
    assert info.arch == "armel"
    assert info.runnable


def test_armeb_is_named_as_such_and_is_not_runnable():
    """Big-endian ARM is a distinct architecture that no kernel here can boot.

    Reporting it as `armel` -- which two of the three census implementations used
    to do -- means the caller starts it on the little-endian `zImage.armel`, where
    it runs the init script and then misexecs. The name has to say which one it is
    so the caller can refuse it, and the byte order stays available separately for
    anyone who needs it.
    """
    info = identify_elf(elf_header(1, 2, EM_ARM))
    assert info is not None
    assert info.arch == "armeb"
    assert not info.runnable
    assert info.endianness == "eb"


def test_x86_and_x64():
    assert identify_elf(elf_header(1, 1, EM_I386)).arch == "x86"
    assert identify_elf(elf_header(2, 1, EM_X86_64)).arch == "x64"


def test_arm64_is_labelled_with_the_elf_name():
    """`aarch64` is the ELF standard name and what the corpus records. The
    emulator's spelling (`arm64`) is one translate() away, and inventing a third
    one here (`arm64le`) is what made the same firmware answer to three names."""
    info = identify_elf(elf_header(2, 1, EM_AARCH64))
    assert info is not None
    assert info.arch == "aarch64"
    assert info.runnable


def test_mips64_is_distinguished_from_mips32():
    """Both share e_machine 8. Labelling the 64-bit one `mipsel` would hand it
    vmlinux.mipsel.4 and a guest that cannot be executed at all."""
    assert identify_elf(elf_header(2, 1, EM_MIPS)).arch == "mips64le"
    assert identify_elf(elf_header(2, 2, EM_MIPS)).arch == "mips64eb"


def test_not_elf():
    assert identify_elf(b"not an elf file at all!!") is None
    assert identify_elf(b"") is None
    assert identify_elf(b"\x7fEL") is None


def test_bad_class():
    h = elf_header(1, 1, EM_MIPS)
    h = bytearray(h)
    h[4] = 3  # invalid EI_CLASS
    assert identify_elf(bytes(h)) is None


def _sources() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py"))


class TestNormalize:
    @pytest.mark.parametrize("spelling", ["aarch64", "arm64", "arm64le", "ARM64", " arm64 "])
    def test_every_aarch64_spelling_resolves_to_the_kernel_name(self, spelling):
        assert normalize_arch(spelling) == "arm64"

    @pytest.mark.parametrize("name", ["mipsel", "mipseb", "armel"])
    def test_names_that_are_already_normal_are_unchanged(self, name):
        assert normalize_arch(name) == name

    @pytest.mark.parametrize("unknown", ["x64", "ppc", "sparc64", "", "   "])
    def test_an_unknown_name_is_returned_unchanged(self, unknown):
        """Callers use the result to pick a kernel, so guessing would start the
        wrong one. Returning the input means the rejection message can name what
        the caller actually asked for."""
        assert normalize_arch(unknown) == unknown.strip()

    def test_the_result_is_a_plain_string(self):
        """RunnableArch is a StrEnum, so a member would compare equal to its value
        everywhere -- except in a JSON body or a column default, where the enum
        object is what gets serialised."""
        assert type(normalize_arch("aarch64")) is str


class TestCensusToRunnable:
    @pytest.mark.parametrize("label,expected", [
        ("mipsel", "mipsel"),
        ("mipseb", "mipseb"),
        ("armel", "armel"),
        ("aarch64", "arm64"),
    ])
    def test_the_architectures_with_kernels(self, label, expected):
        assert census_to_runnable(label) == expected

    @pytest.mark.parametrize("label", ["x64", "x86", "mips64le", "ppcle", "", "sparc"])
    def test_architectures_without_kernels_answer_empty(self, label):
        """Empty is the caller's signal to keep looking at other signals, so it
        must be distinguishable from "unknown label" -- both must be empty, and
        neither may fall back to a nearby architecture."""
        assert census_to_runnable(label) == ""

    def test_big_endian_arm_is_refused_even_though_armel_is_allowed(self):
        """The byte order is not part of the label any more, so it has to be
        passed in -- and it has to change the answer. Booting armeb on the
        little-endian zImage.armel runs the init script and then misexecs."""
        assert census_to_runnable("armel", "le") == "armel"
        assert census_to_runnable("armel", "eb") == ""

    def test_big_endian_mips_is_fine_because_a_big_endian_kernel_ships(self):
        """The byte-order guard must be specific. mipseb is big-endian and boots."""
        assert census_to_runnable("mipseb", "eb") == "mipseb"

    def test_an_unset_byte_order_is_not_treated_as_big_endian(self):
        """Callers that have no endianness to pass must not be refused: the
        preflight path calls this without one."""
        assert census_to_runnable("armel", "") == "armel"


class TestEndianness:
    def test_mipseb_is_the_only_big_endian_member(self):
        """A wrong answer here decodes a guest address into a plausible-looking
        wrong address rather than an obvious failure."""
        little = {a for a in RunnableArch if is_little_endian(a)}
        assert little == set(LITTLE_ENDIAN_ARCHS)
        assert "mipseb" not in little

    def test_the_set_and_the_function_agree(self):
        for arch in RunnableArch:
            assert is_little_endian(arch) == (arch.value in LITTLE_ENDIAN_ARCHS)
            assert arch_endianness(arch) == ("le" if is_little_endian(arch) else "eb")

    def test_an_unknown_architecture_is_not_assumed_little_endian(self):
        assert not is_little_endian("x64")
        assert arch_endianness("x64") == ""


class TestCensusLabel:
    @pytest.mark.parametrize("machine,endian,bits,expected", [
        (8, "le", 32, "mipsel"),
        (8, "eb", 32, "mipseb"),
        (8, "le", 64, "mips64le"),
        (8, "eb", 64, "mips64eb"),
        (40, "le", 32, "armel"),
        (40, "eb", 32, "armeb"),
        (183, "le", 64, "aarch64"),
        (62, "le", 64, "x64"),
        (3, "le", 32, "x86"),
    ])
    def test_every_elf_machine_maps_to_one_label(self, machine, endian, bits, expected):
        assert census_label(machine, endian, bits) == expected

    def test_an_unknown_machine_stays_visible_in_the_census(self):
        """`unk(999)` is what makes "this firmware is mostly something we cannot
        parse" readable off a histogram; a silent None would drop the samples."""
        assert census_label(999, "le") == "unk(999)"

    def test_every_emulated_architecture_is_reachable_through_a_census_label(self):
        """The two vocabularies have to meet: an architecture that no ELF header
        can name would be listed as supported and never selected."""
        produced = {census_label(m, e, b)
                    for m in (3, 8, 20, 40, 62, 183)
                    for e in ("le", "eb")
                    for b in (32, 64)}
        for arch in RunnableArch:
            resolved = [label for label in produced if census_to_runnable(label) == arch]
            assert resolved, f"{arch} is emulatable but no ELF census label resolves to it"

    def test_aarch64_is_reachable_because_that_is_what_an_elf_header_says(self):
        """The runtime name is `arm64` and the census name is `aarch64`. They only
        meet if the alias table carries the translation, which is the single entry
        whose absence once made every aarch64 firmware "unknown architecture"."""
        assert census_to_runnable("aarch64") == "arm64"


class TestElfByteDecoders:
    @pytest.mark.parametrize("value,expected", [(1, "le"), (2, "eb"), (0, ""), (9, "")])
    def test_ei_data(self, value, expected):
        assert endianness_of(value) == expected

    @pytest.mark.parametrize("value,expected", [(1, 32), (2, 64), (0, 0), (7, 0)])
    def test_ei_class(self, value, expected):
        assert class_bits(value) == expected


class TestNoSecondVocabulary:
    """The consolidation is only real if the old copies cannot come back."""

    #: Files that legitimately hold architecture strings. Everything else in the
    #: package reaching for one is re-implementing the mapping. `api/server.py` and
    #: `cli.py` were on this list while they still carried inline maps; they left it
    #: when the maps went, because an exemption nobody needs is a hole the test can
    #: no longer see through.
    ALLOWED: ClassVar[frozenset[str]] = frozenset({"arch.py", "qemu_config.py"})

    def test_no_module_defines_its_own_architecture_mapping(self):
        """A dict or comprehension that pairs a census name with a kernel name is
        the shape all four old copies took."""
        pattern = re.compile(r'"(aarch64|arm64|arm64le)"\s*:\s*"arm64"')
        offenders = []
        for path in _sources():
            if path.name in self.ALLOWED:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(PROJECT)}:{lineno}")
        assert not offenders, (
            "an architecture mapping is defined outside iris/arch.py: "
            + ", ".join(offenders)
        )

    def test_every_module_that_maps_an_arch_uses_the_authority(self):
        """Reading the name off an ELF header and then starting the guest is a
        two-step process, and the middle step has to go through the authority --
        otherwise the translation silently does not happen.

        Consuming `iris.extract.arch.identify_elf` counts: that is the census
        entry point, which itself defers to `iris.arch` for the mapping. What
        this rejects is a module that opens an ELF header and decides for itself.
        """
        consumers = {
            "extract/firmware.py": "from iris.arch import",
            "extract/arch.py": "from iris.arch import",
            "emulate/auto.py": "from iris.arch import",
            "emulate/orchestrator.py": "from iris.arch import",
            "api/server.py": "from iris.arch import",
            "cli.py": "from iris.arch import",
            "extract/rootfs_extract.py": "from iris.extract.arch import",
        }
        for rel, expected in consumers.items():
            text = (SRC / rel).read_text(encoding="utf-8")
            assert expected in text, f"{rel} does not import {expected.strip()} iris"

    def test_only_the_authority_decides_what_is_emulatable(self):
        """A second answer to "which architectures can we run" is exactly what the
        old FirmAE support set was, and it disagreed with the emulator. Matched
        against code only: a comment explaining the removal is the opposite of a
        violation."""
        for path in _sources():
            code = "\n".join(
                line for line in path.read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("#")
            )
            assert "FIRMAE_SUPPORTED_ARCHS" not in code, (
                f"{path.relative_to(PROJECT)} still refers to the FirmAE support set"
            )

    def test_the_emulator_configuration_and_the_vocabulary_agree(self):
        """Two lists of runnable architectures that can drift is how aarch64
        ended up supported in one and absent from the other."""
        from iris.emulate.qemu_config import all_configs

        assert set(all_configs()) == {a.value for a in RunnableArch}