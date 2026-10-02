"""One vocabulary for architecture names.

Adding an architecture used to mean editing eight places, and they had already
drifted apart: ``_ARCH_MAPPINGS`` in ``emulate/auto.py`` and ``_CENSUS_TO_RUNNABLE``
in ``emulate/orchestrator.py`` were the same four-entry dict written twice, ``cli.py``
carried two more inline copies, ``api/server.py`` had its own list with no ``aarch64``
in it at all, and ``extract/arch.py`` invented a third spelling (``arm64le``) that
nothing downstream understood. A guest identified as ``aarch64`` was therefore
"an unknown architecture" to the code that had to start it.

This module is the authority for *names*: what an architecture is called, which
spellings mean the same one, and what its byte order is. It deliberately does not
know which architectures can be emulated or how -- that is
:mod:`iris.emulate.qemu_config`, which owns the device model. The two are kept
honest against each other by ``tests/test_arch.py`` rather than by an import, so
that neither module has to know about the other's callers.

Two vocabularies exist on purpose, and conflating them is what caused the drift:

``census``
    What the ELF header says: ``aarch64``, ``arm64le``, ``mipseb``, ``x64``,
    ``ppcle``. Descriptive, and includes architectures nothing can boot.
``runnable``
    What the emulator accepts: ``arm64``, ``mipsel``, ``mipseb``, ``armel``. A
    closed set, because it is bounded by the kernels in ``binaries/``.

:func:`normalize_arch` is the only way between them.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "EM_AARCH64",
    "EM_ARM",
    "EM_I386",
    "EM_MIPS",
    "EM_PPC",
    "EM_X86_64",
    "LITTLE_ENDIAN_ARCHS",
    "RunnableArch",
    "arch_endianness",
    "census_label",
    "census_to_runnable",
    "class_bits",
    "endianness_of",
    "is_little_endian",
    "normalize_arch",
]

#: ELF ``e_machine`` values, by name. Defined here rather than in
#: ``extract/arch.py`` because this module is the authority for the labels those
#: numbers produce, and a number spelled two ways in two places is one of the ways
#: the drift started: the mapping lived in three census modules while the constants
#: naming the same numbers lived in a fourth.
EM_I386 = 3
EM_MIPS = 8
EM_PPC = 20
EM_ARM = 40
EM_X86_64 = 62
EM_AARCH64 = 183


class RunnableArch(StrEnum):
    """The architectures the emulator can actually boot.

    Named after the kernel assets in ``binaries/``, not after the ELF standard:
    ``aarch64`` the ELF name is spelled ``arm64`` here because that is what
    ``run_qemu.sh`` and ``Image.arm64`` call it.
    """

    MIPSEL = "mipsel"
    MIPSEB = "mipseb"
    ARMEL = "armel"
    ARM64 = "arm64"


#: Every spelling that resolves to one runnable architecture.
#:
#: Three of these come from ``extract/arch.py``'s older ELF-level vocabulary,
#: which appended the byte order to disambiguate (``arm64le``). Those spellings
#: are still accepted -- they appear in older databases and in log lines -- but
#: nothing produces them any more: a label now names the architecture and the byte
#: order travels separately, because "which machine" and "which byte order" are
#: two questions and one string can only answer one of them at a time.
_ALIASES: dict[str, RunnableArch] = {
    # ELF standard names
    "aarch64": RunnableArch.ARM64,
    "arm64": RunnableArch.ARM64,
    "arm64le": RunnableArch.ARM64,
    "armel": RunnableArch.ARMEL,
    "mipsel": RunnableArch.MIPSEL,
    "mipseb": RunnableArch.MIPSEB,
    # Deliberately absent: armeb, arm64eb, mips64*, ppc*, x86, x64.
    # They are real architectures the census reports, and each one fails in a
    # way that looks like a broken simulator rather than a wrong kernel: booting
    # an armeb guest on the little-endian zImage.armel runs the init script and
    # then misexecs, and mips64 on vmlinux.mipsel.4 fails the same way. Answering
    # "" makes auto-detection report ARCH_UNDETERMINED and ask for an explicit
    # --arch, which is recoverable; guessing here is not.
}

#: ELF ``e_machine`` values, mapped to the census label each one gets.
#:
#: The single answer to "what architecture does this ELF header name". It used to
#: be written three times -- in ``extract/arch.py``, ``extract/firmware.py`` and
#: ``extract/rootfs_extract.py`` -- and the copies had drifted: two reported ``armel``
#: for every ARM binary regardless of byte order, the third reported ``armeb`` for
#: the big-endian ones, so the same firmware was named two different things
#: depending on which of the two census paths looked at it.
_EM_LABELS: dict[int, str] = {
    EM_I386: "x86",
    EM_MIPS: "mips",  # endianness decides mipsel / mipseb
    EM_PPC: "ppc",
    EM_ARM: "arm",  # endianness decides armel / armeb
    EM_X86_64: "x64",
    EM_AARCH64: "aarch64",
}

#: MIPS is one ``e_machine`` with two byte orders, so its final label needs both.
_MIPS_LABELS = {"le": "mipsel", "eb": "mipseb"}
_ARM_LABELS = {"le": "armel", "eb": "armeb"}


#: Architectures whose in-memory integers are little-endian.
#:
#: Consumed when decoding a guest address out of a serial log line: the same
#: 32-bit word is assembled byte-first on a little-endian guest and byte-last on a
#: big-endian one, and the mistake produces a plausible-looking wrong address
#: rather than an obvious failure. ``mipseb`` is absent on purpose -- it is the
#: big-endian member, and reading it as little-endian is the bug this set exists
#: to prevent.
LITTLE_ENDIAN_ARCHS = frozenset({
    RunnableArch.MIPSEL.value,
    RunnableArch.ARMEL.value,
    RunnableArch.ARM64.value,
})

#: Byte order per runnable architecture. Derived from the set above plus the one
#: big-endian architecture that ships, so the two cannot disagree.
_ENDIANNESS: dict[str, str] = {
    arch.value: ("le" if arch in {RunnableArch.MIPSEL, RunnableArch.ARMEL, RunnableArch.ARM64} else "eb")
    for arch in RunnableArch
}


def normalize_arch(name: str) -> str:
    """Resolve any known spelling of an architecture to its runnable name.

    An unrecognised name is returned unchanged rather than guessed at: callers
    use the result to look up a QEMU configuration, and a wrong guess would start
    the wrong emulator on the wrong kernel. Rejecting later, with the original
    string in the message, tells the caller what they actually asked for.

    >>> normalize_arch("aarch64")
    'arm64'
    >>> normalize_arch("arm64le")
    'arm64'
    >>> normalize_arch("sparc64")
    'sparc64'
    """
    return str(_ALIASES.get(name.strip().lower(), name.strip().lower()))


def census_to_runnable(name: str, endianness: str = "") -> str:
    """The runnable name for a census label, or ``""`` when none can boot it.

    The empty string is the caller's signal to keep looking: auto-detection walks
    several signals and only the last one is authoritative, so "this label names
    nothing emulatable" has to be distinguishable from "this label is unknown".

    ``endianness`` is a second line of defence, not the primary mechanism. The
    census label normally already carries the distinction (``armeb`` is not
    ``armel``), so callers holding only a label need no byte order. The parameter
    exists for the case where that stops being true: if a future label goes back to
    reporting every ARM binary as ``armel`` -- which is what two of the three
    original census implementations did -- then the ELF header is the only thing
    left that knows the answer, and a big-endian guest would otherwise be started
    on the little-endian ``zImage.armel``, where it runs the init script and then
    misexecs.

    >>> census_to_runnable("aarch64")
    'arm64'
    >>> census_to_runnable("armel", "eb")
    ''
    >>> census_to_runnable("x64")
    ''
    """
    resolved = _ALIASES.get(name.strip().lower())
    if resolved is None:
        return ""
    if endianness == "eb" and resolved in {RunnableArch.ARMEL, RunnableArch.ARM64}:
        # No big-endian ARM or aarch64 kernel ships. mipseb is the one
        # big-endian architecture that does, and it is not in this set.
        return ""
    return str(resolved)


def is_little_endian(arch: str) -> bool:
    """Whether ``arch`` stores integers least-significant-byte first.

    Unknown architectures answer ``False``: the caller is decoding a guest
    address, and guessing little-endian for an architecture nobody has seen would
    produce a wrong address that still looks like an address.
    """
    return arch in LITTLE_ENDIAN_ARCHS


def arch_endianness(arch: str) -> str:
    """``"le"`` or ``"eb"``. Unknown architectures answer ``""``."""
    return _ENDIANNESS.get(arch, "")


def endianness_of(ei_data: int) -> str:
    """``"le"`` / ``"eb"`` from an ELF ``EI_DATA`` byte. Anything else is ``""``.

    >>> endianness_of(1), endianness_of(2), endianness_of(9)
    ('le', 'eb', '')
    """
    return {1: "le", 2: "eb"}.get(ei_data, "")


def class_bits(ei_class: int) -> int:
    """32 / 64 from an ELF ``EI_CLASS`` byte. Anything else is ``0``.

    >>> class_bits(1), class_bits(2), class_bits(7)
    (32, 64, 0)
    """
    return {1: 32, 2: 64}.get(ei_class, 0)


def census_label(e_machine: int, endianness: str, bits: int = 32) -> str:
    """The census label for an ELF ``e_machine``, byte order and class.

    ``bits`` is not decoration: MIPS has a 64-bit variant that shares
    ``e_machine`` 8 with the 32-bit one, and labelling it ``mipsel`` would hand
    ``vmlinux.mipsel.4`` a guest that cannot be executed at all -- a failure that
    reads as a broken simulator rather than a wrong kernel.

    Unknown machines keep the ``unk(<n>)`` spelling the census has always used, so
    an unrecognised architecture stays visible in the counts instead of being
    silently dropped -- which is what makes "this firmware is mostly something we
    cannot parse" readable off a histogram.

    An unrecognised *byte order* answers ``mips`` / ``arm``: the machine is known
    but the one fact that decides its label is not, and inventing an ``mipsel``
    here is how a truncated vendor blob inside a firmware image gets to be booted
    on the wrong kernel. ``census_to_runnable`` refuses both.

    >>> census_label(8, "le")
    'mipsel'
    >>> census_label(8, "le", bits=64)
    'mips64le'
    >>> census_label(40, "eb")
    'armeb'
    >>> census_label(8, "")
    'mips'
    >>> census_label(999, "le")
    'unk(999)'
    """
    if e_machine == EM_MIPS:
        if bits == 64:
            return f"mips64{endianness}"
        return _MIPS_LABELS.get(endianness, "mips")
    if e_machine == EM_ARM:
        if bits == 64:
            return f"arm64{endianness}"
        return _ARM_LABELS.get(endianness, "arm")
    return _EM_LABELS.get(e_machine, f"unk({e_machine})")