"""L1 firmware format identification and architecture inference.

Pure-Python analysis of raw firmware images: uImage header parsing,
squashfs detection, ELF census, and gzip recursive decompression.
No external binwalk/unblob dependency required for the common cases
covered in M1 (OpenWrt uImage/squashfs, gzip-wrapped firmware, Tenda
bin wrappers). Outer zip containers are NOT parsed — their internal
directory layout is unpredictable, so the upgrade package must be
unpacked by the caller and the firmware .bin fed in directly.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import struct
from collections import Counter
from dataclasses import dataclass, field

from iris.extract.tenda import (
    TendaContainer,
    detect_segmented_regions,
    extract_tenda_wrapper,
    is_tenda_wrapper,
    is_tendaw,
    parse_tendaw,
)

UIMAGE_MAGIC = 0x27051956

SQUASHFS_MAGICS: dict[bytes, tuple[str, str]] = {
    b"hsqs": ("le", "zlib"),
    b"sqsh": ("be", "zlib"),
    b"sqlz": ("le", "lzma"),
    b"hsqt": ("le", "lz4"),
    b"shsq": ("be", "lzma"),
}

UIMAGE_COMP = {0: "none", 1: "gzip", 2: "bzip2", 3: "lzma", 4: "lzo", 5: "lz4", 6: "zstd"}


@dataclass
class SquashfsInfo:
    offset: int
    endian: str
    comp: str


@dataclass
class UImageInfo:
    arch_field: int
    arch_name: str
    comp: str
    size: int
    load: int
    ep: int
    name: str


@dataclass
class FirmwareInfo:
    format: str
    uimage: UImageInfo | None = None
    squashfs: list[SquashfsInfo] = field(default_factory=list)
    elf_archs: Counter = field(default_factory=Counter)
    arch: str = ""
    rootfs_offset: int | None = None
    ubi_offset: int | None = None
    tendaw: TendaContainer | None = None
    segmented_offsets: list[int] = field(default_factory=list)
    fit: bool = False


def identify_format(data: bytes) -> str:
    if len(data) < 4:
        return "raw"
    if is_tendaw(data):
        return "tendaw"
    if data[:4] == struct.pack(">I", UIMAGE_MAGIC):
        if is_tenda_wrapper(data):
            return "tenda_wrapper"
        return "uimage"
    if data[:2] == b"\x1f\x8b":
        return "gzip"
    if data[:4] == b"PK\x03\x04":
        return "zip"
    if data[:4] in SQUASHFS_MAGICS:
        return "squashfs"
    if data[:4] == b"HDR0":
        return "trx"
    if data[:4] == b"UBI#":
        return "ubi"
    if data[:4] == b"\xd0\x0d\xfe\xed":
        return "fit"
    if data[:4] == b"\x01\x00\x00\x00" and b"OpenWrt" in data[:64]:
        return "tplink"
    if data[:7] == b"device:":
        return "netgear"
    return "raw"


def parse_uimage(data: bytes) -> UImageInfo | None:
    if len(data) < 64:
        return None
    magic = struct.unpack(">I", data[0:4])[0]
    if magic != UIMAGE_MAGIC:
        return None
    size = struct.unpack(">I", data[12:16])[0]
    load = struct.unpack(">I", data[16:20])[0]
    ep = struct.unpack(">I", data[20:24])[0]
    arch_field = data[29]
    comp_field = data[31]
    name = data[32:64].split(b"\x00")[0].decode("ascii", errors="replace")
    comp = UIMAGE_COMP.get(comp_field, f"unknown({comp_field})")

    arch_name = ""
    name_lower = name.lower()
    if "arm" in name_lower:
        arch_name = "armel"
    elif "mips" in name_lower:
        arch_name = "mips"
    elif "x86" in name_lower or "x64" in name_lower:
        arch_name = "x64"

    return UImageInfo(
        arch_field=arch_field,
        arch_name=arch_name,
        comp=comp,
        size=size,
        load=load,
        ep=ep,
        name=name,
    )


def find_squashfs(data: bytes, max_results: int = 5) -> list[SquashfsInfo]:
    results: list[SquashfsInfo] = []
    for magic, (endian, comp) in SQUASHFS_MAGICS.items():
        pos = 0
        while len(results) < max_results:
            pos = data.find(magic, pos)
            if pos == -1:
                break
            if pos + 48 < len(data):
                e = "<" if endian == "le" else ">"
                bytes_used = struct.unpack(f"{e}Q", data[pos + 40 : pos + 48])[0]
                remaining = len(data) - pos
                if 0 < bytes_used <= remaining:
                    results.append(SquashfsInfo(offset=pos, endian=endian, comp=comp))
            pos += 1
    results.sort(key=lambda x: x.offset)
    return results[:max_results]


def find_elf_archs(data: bytes) -> Counter:
    counter: Counter = Counter()
    pos = 0
    while pos < len(data) - 18:
        pos = data.find(b"\x7fELF", pos)
        if pos == -1:
            break
        if pos + 18 < len(data):
            ei_data = data[pos + 5]
            e_machine = struct.unpack("<H" if ei_data == 1 else ">H", data[pos + 18 : pos + 20])[0]
            if e_machine == 8:
                arch = "mipsel" if ei_data == 1 else "mipseb"
            elif e_machine == 40:
                arch = "armel"
            elif e_machine == 62:
                arch = "x64"
            elif e_machine == 3:
                arch = "x86"
            elif e_machine == 183:
                arch = "aarch64"
            else:
                arch = f"unk({e_machine})"
            counter[arch] += 1
        pos += 1
    return counter


def _decompress_uimage_payload(data: bytes, ui: UImageInfo | None) -> bytes | None:
    if ui is None or ui.comp not in ("gzip", "bzip2", "lzma"):
        return None
    end = 64 + ui.size if 0 < ui.size <= len(data) - 64 else len(data)
    payload = data[64:end]
    try:
        if ui.comp == "gzip":
            return gzip.decompress(payload)
        if ui.comp == "bzip2":
            return bz2.decompress(payload)
        return lzma.decompress(payload)
    except (OSError, ValueError, lzma.LZMAError):
        return None


def analyze_firmware(data: bytes, arch_hint: str = "", depth: int = 0) -> FirmwareInfo:
    if depth > 3:
        return FirmwareInfo(format="raw", arch=arch_hint)

    fmt = identify_format(data)
    if fmt == "zip":
        raise ValueError(
            "zip containers are not accepted as input (their internal layout is "
            "unpredictable); unpack the upgrade package locally and feed the "
            "firmware .bin file to IRIS"
        )
    info = FirmwareInfo(format=fmt)

    if fmt == "uimage":
        info.uimage = parse_uimage(data)

    if fmt == "tendaw":
        info.tendaw = parse_tendaw(data)
        info.segmented_offsets = detect_segmented_regions(data)
        info.arch = arch_hint
        return info

    if fmt == "tenda_wrapper":
        inner = extract_tenda_wrapper(data)
        sub = analyze_firmware(inner, arch_hint, depth + 1)
        info.format = "tenda_wrapper"
        info.uimage = sub.uimage
        info.squashfs = [
            SquashfsInfo(offset=s.offset + 64, endian=s.endian, comp=s.comp) for s in sub.squashfs
        ]
        info.elf_archs = sub.elf_archs
        info.ubi_offset = sub.ubi_offset + 64 if sub.ubi_offset is not None else None
        info.rootfs_offset = sub.rootfs_offset + 64 if sub.rootfs_offset is not None else None
        info.fit = sub.fit
        info.segmented_offsets = detect_segmented_regions(data)
        info.arch = _infer_arch(info, arch_hint)
        return info

    info.squashfs = find_squashfs(data)
    if info.squashfs:
        info.rootfs_offset = info.squashfs[0].offset

    ubi_pos = data.find(b"UBI#")
    if ubi_pos != -1:
        info.ubi_offset = ubi_pos
        if info.rootfs_offset is None:
            info.rootfs_offset = ubi_pos

    info.elf_archs = find_elf_archs(data)

    if fmt == "fit":
        info.fit = True

    if fmt == "uimage" and depth < 3:
        payload = _decompress_uimage_payload(data, info.uimage)
        if payload is not None:
            sub = analyze_firmware(payload, arch_hint, depth + 1)
            info.squashfs = sub.squashfs or info.squashfs
            if sub.rootfs_offset is not None or sub.ubi_offset is not None:
                # payload is a filesystem image, not a bare kernel: trust its ELF census
                info.elf_archs = sub.elf_archs or info.elf_archs
            if sub.rootfs_offset is not None:
                info.rootfs_offset = sub.rootfs_offset
            if sub.ubi_offset is not None and info.ubi_offset is None:
                info.ubi_offset = sub.ubi_offset
            info.fit = sub.fit or info.fit

    if fmt == "gzip" and depth < 3:
        try:
            decompressed = gzip.decompress(data)
            sub = analyze_firmware(decompressed, arch_hint, depth + 1)
            info.squashfs = sub.squashfs or info.squashfs
            info.elf_archs = sub.elf_archs or info.elf_archs
            info.rootfs_offset = sub.rootfs_offset or info.rootfs_offset
            if sub.uimage:
                info.uimage = sub.uimage
        except OSError:
            pass

    info.arch = _infer_arch(info, arch_hint)
    return info


def _infer_arch(info: FirmwareInfo, arch_hint: str = "") -> str:
    known = Counter({a: n for a, n in info.elf_archs.items() if not a.startswith("unk(")})
    if known:
        return known.most_common(1)[0][0]

    if info.uimage and info.uimage.arch_name:
        arch = info.uimage.arch_name
        if arch == "mips":
            if arch_hint in ("mipsel", "mipseb"):
                return arch_hint
            if info.squashfs:
                return "mipsel" if info.squashfs[0].endian == "le" else "mipseb"
            return "mipsel"
        return arch

    if arch_hint:
        return arch_hint

    if info.squashfs:
        return "mipsel" if info.squashfs[0].endian == "le" else "mipseb"

    return ""
