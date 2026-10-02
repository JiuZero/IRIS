"""ELF header based architecture identification (L1).

Maps e_machine + endianness to firmadyne/FirmAE style arch labels:
mipseb / mipsel / armel (+ informational x86 / x64 / ppc / arm64le ...).
Only the first 20 bytes of the file are required.

The labels produced here are the *census* vocabulary, deliberately including
architectures nothing can boot (``armeb``, ``x64``, ``ppcle``) because the
question this module answers is "what does the firmware contain", not "what can
we start". :mod:`iris.arch` translates a label to a runnable architecture and is
the only place that mapping exists.
"""

import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from iris.arch import (
    EM_AARCH64,
    EM_ARM,
    EM_I386,
    EM_MIPS,
    EM_PPC,
    EM_X86_64,
    census_label,
    census_to_runnable,
)

ELF_MAGIC = b"\x7fELF"

# Re-exported, not redefined: these name the same ELF numbers the census labels in
# iris.arch are built from, and two copies of a number is one of the ways this
# vocabulary drifted in the first place.
__all__ = [
    "ELF_MAGIC",
    "EM_AARCH64",
    "EM_ARM",
    "EM_I386",
    "EM_MIPS",
    "EM_PPC",
    "EM_X86_64",
    "ArchInfo",
    "identify_elf",
    "identify_tar_members",
]


@dataclass(frozen=True)
class ArchInfo:
    arch: str
    bits: int
    endianness: str  # "le" / "eb"
    #: Whether IRIS has a kernel that can boot this architecture.
    #:
    #: This used to be read off a hardcoded `FIRMAE_SUPPORTED_ARCHS` of
    #: {mipseb, mipsel, armel}, which answered a different question: whether
    #: *FirmAE* could. The two disagreed -- arm64 boots here (Image.arm64 plus
    #: its own initramfs) while that set said it could not -- and the set was the
    #: one a caller would reach for. The emulator's own configuration is now the
    #: answer, so "supported" cannot drift away from what actually starts.
    runnable: bool


def _map_arch(e_machine: int, bits: int, endianness: str) -> str | None:
    if e_machine in (EM_I386, EM_X86_64) and bits != (32 if e_machine == EM_I386 else 64):
        # A 64-bit e_machine in a 32-bit class, or the reverse, means the header
        # is inconsistent rather than unusual -- there is no such architecture to
        # name, and reporting one would attribute the failure to the emulator.
        return None
    label = census_label(e_machine, endianness, bits)
    return None if label.startswith("unk(") else label


def identify_elf(data: bytes) -> ArchInfo | None:
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
        runnable=bool(census_to_runnable(arch, endianness)),
    )



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
