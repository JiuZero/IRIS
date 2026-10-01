import struct

from iris.extract.arch import (
    EM_AARCH64,
    EM_ARM,
    EM_I386,
    EM_MIPS,
    EM_X86_64,
    identify_elf,
)


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
    assert info.firmae_supported


def test_mipseb():
    info = identify_elf(elf_header(1, 2, EM_MIPS))
    assert info is not None
    assert info.arch == "mipseb"
    assert info.firmae_supported


def test_armel():
    info = identify_elf(elf_header(1, 1, EM_ARM))
    assert info is not None
    assert info.arch == "armel"
    assert info.firmae_supported


def test_armeb_not_firmae_supported():
    info = identify_elf(elf_header(1, 2, EM_ARM))
    assert info is not None
    assert info.arch == "armeb"
    assert not info.firmae_supported


def test_x86_and_x64():
    assert identify_elf(elf_header(1, 1, EM_I386)).arch == "x86"
    assert identify_elf(elf_header(2, 1, EM_X86_64)).arch == "x64"


def test_arm64():
    info = identify_elf(elf_header(2, 1, EM_AARCH64))
    assert info is not None
    assert info.arch == "arm64le"


def test_not_elf():
    assert identify_elf(b"not an elf file at all!!") is None
    assert identify_elf(b"") is None
    assert identify_elf(b"\x7fEL") is None


def test_bad_class():
    h = elf_header(1, 1, EM_MIPS)
    h = bytearray(h)
    h[4] = 3  # invalid EI_CLASS
    assert identify_elf(bytes(h)) is None
