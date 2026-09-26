"""ELF header based architecture identification (L1).

Maps e_machine + endianness to firmadyne/FirmAE style arch labels:
mipseb / mipsel / armel (+ informational x86 / x64 / ppc / arm64 ...).
Only the first 20 bytes of the file are required.
"""

import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ELF_MAGIC = b"\x7fELF"

EM_MIPS = 8
EM_PPC = 20
EM_I386 = 3
EM_ARM = 40
EM_X86_64 = 62
EM_AARCH64 = 183

FIRMAE_SUPPORTED_ARCHS = frozenset({"mipseb", "mipsel", "armel"})


@dataclass(frozen=True)
class ArchInfo:
    arch: str
    bits: int
    endianness: str  # "le" / "eb"
    firmae_supported: bool


def _map_arch(e_machine: int, bits: int, endianness: str) -> Optional[str]:
    if e_machine == EM_MIPS and bits == 32:
        return "mipseb" if endianness == "eb" else "mipsel"
    if e_machine == EM_MIPS and bits == 64:
        return "mips64" + endianness
    if e_machine == EM_ARM and bits == 32:
        return "armeb" if endianness == "eb" else "armel"
    if e_machine == EM_AARCH64:
        return "arm64" + endianness
    if e_machine == EM_I386:
        return "x86"
    if e_machine == EM_X86_64:
        return "x64"
    if e_machine == EM_PPC:
        return "ppc" + endianness
    return None


def identify_elf(data: bytes) -> Optional[ArchInfo]:
    if len(data) < 20 or data[:4] != ELF_MAGIC:
        return None
    ei_class = data[4]
    ei_data = data[5]
    if ei_class == 1:
        bits = 32
    elif ei_class == 2:
        bits = 64
    else:
        return None
    if ei_data == 1:
        endianness, fmt = "le", "<"
    elif ei_data == 2:
        endianness, fmt = "eb", ">"
    else:
        return None
    (e_machine,) = struct.unpack(fmt + "H", data[18:20])
    arch = _map_arch(e_machine, bits, endianness)
    if arch is None:
        return None
    return ArchInfo(
        arch=arch,
        bits=bits,
        endianness=endianness,
        firmae_supported=arch in FIRMAE_SUPPORTED_ARCHS,
    )


def identify_file(path: Path) -> Optional[ArchInfo]:
    with open(path, "rb") as fh:
        return identify_elf(fh.read(20))


def identify_tar_members(archive: Path, max_samples: int = 80) -> Counter:
    """Scan regular files inside a tar/tar.gz archive and count arch labels."""
    import tarfile

    counter: Counter = Counter()
    with tarfile.open(archive, "r:*") as tf:
        for member in tf:
            if not member.isfile():
                continue
            if counter.total() >= max_samples:
                break
            fh = tf.extractfile(member)
            if fh is None:
                continue
            info = identify_elf(fh.read(20))
            if info is not None:
                counter[info.arch] += 1
    return counter