"""Tests for iris.extract.firmware — format detection, uImage parsing, arch inference."""

from __future__ import annotations

import gzip
import io
import struct
import zipfile

from iris.extract.firmware import (
    UIMAGE_MAGIC,
    analyze_firmware,
    find_elf_archs,
    find_squashfs,
    identify_format,
    parse_uimage,
)


def _make_uimage(arch_field: int = 5, name: str = "MIPS OpenWrt Linux-6.6") -> bytes:
    header = bytearray(64)
    struct.pack_into(">I", header, 0, UIMAGE_MAGIC)
    struct.pack_into(">I", header, 12, 0x100000)
    struct.pack_into(">I", header, 16, 0x80001000)
    struct.pack_into(">I", header, 20, 0x80001000)
    header[28] = 5
    header[29] = arch_field
    header[30] = 2
    header[31] = 0
    name_bytes = name.encode("ascii")[:32]
    header[32 : 32 + len(name_bytes)] = name_bytes
    return bytes(header) + b"\x00" * 256


def _make_elf(machine: int, ei_data: int = 1) -> bytes:
    e = bytearray(64)
    e[0:4] = b"\x7fELF"
    e[4] = 1
    e[5] = ei_data
    struct.pack_into("<H" if ei_data == 1 else ">H", e, 18, machine)
    return bytes(e)


def _make_squashfs(endian: str = "le") -> bytes:
    magic = b"hsqs" if endian == "le" else b"sqsh"
    header = bytearray(magic + b"\x00" * 128)
    e = "<" if endian == "le" else ">"
    struct.pack_into(f"{e}Q", header, 40, 64)
    return bytes(header)


class TestIdentifyFormat:
    def test_uimage(self):
        assert identify_format(_make_uimage()) == "uimage"

    def test_gzip(self):
        assert identify_format(gzip.compress(b"hello")) == "gzip"

    def test_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.txt", "hello")
        assert identify_format(buf.getvalue()) == "zip"

    def test_squashfs_le(self):
        assert identify_format(_make_squashfs("le")) == "squashfs"

    def test_squashfs_be(self):
        assert identify_format(_make_squashfs("be")) == "squashfs"

    def test_tplink(self):
        data = b"\x01\x00\x00\x00OpenWrt\x00" + b"\x00" * 56
        assert identify_format(data) == "tplink"

    def test_netgear(self):
        data = b"device:R7800\nversion:V.1.0\n"
        assert identify_format(data) == "netgear"

    def test_raw(self):
        assert identify_format(b"\x00\x01\x02\x03") == "raw"

    def test_empty(self):
        assert identify_format(b"") == "raw"


class TestParseUImage:
    def test_valid_mips(self):
        info = parse_uimage(_make_uimage(arch_field=5, name="MIPS OpenWrt Linux-6.6.73"))
        assert info is not None
        assert info.arch_name == "mips"
        assert info.name == "MIPS OpenWrt Linux-6.6.73"
        assert info.load == 0x80001000

    def test_valid_arm(self):
        info = parse_uimage(_make_uimage(arch_field=1, name="ARM OpenWrt Linux-6.6.73"))
        assert info is not None
        assert info.arch_name == "armel"

    def test_invalid_magic(self):
        assert parse_uimage(b"\x00" * 64) is None

    def test_too_short(self):
        assert parse_uimage(b"\x00" * 32) is None


class TestFindSquashfs:
    def test_le(self):
        sq = find_squashfs(_make_squashfs("le"))
        assert len(sq) == 1
        assert sq[0].endian == "le"

    def test_be(self):
        sq = find_squashfs(_make_squashfs("be"))
        assert len(sq) == 1
        assert sq[0].endian == "be"

    def test_not_found(self):
        assert find_squashfs(b"\x00" * 128) == []

    def test_offset(self):
        data = b"\x00" * 1024 + _make_squashfs("le")
        sq = find_squashfs(data)
        assert len(sq) == 1
        assert sq[0].offset == 1024


class TestFindElfArchs:
    def test_arm(self):
        elf = _make_elf(40)
        c = find_elf_archs(elf)
        assert c["armel"] == 1

    def test_mipsel(self):
        elf = _make_elf(8, ei_data=1)
        c = find_elf_archs(elf)
        assert c["mipsel"] == 1

    def test_mipseb(self):
        elf = _make_elf(8, ei_data=2)
        c = find_elf_archs(elf)
        assert c["mipseb"] == 1

    def test_x64(self):
        elf = _make_elf(62)
        c = find_elf_archs(elf)
        assert c["x64"] == 1

    def test_multiple(self):
        data = _make_elf(40) + b"\x00" * 16 + _make_elf(8, ei_data=1)
        c = find_elf_archs(data)
        assert c["armel"] == 1
        assert c["mipsel"] == 1

    def test_none(self):
        assert find_elf_archs(b"\x00" * 128) == {}


class TestAnalyzeFirmware:
    def test_uimage_mips_with_hint(self):
        data = _make_uimage(arch_field=5, name="MIPS OpenWrt Linux-6.6") + _make_squashfs("le")
        info = analyze_firmware(data, arch_hint="mipsel")
        assert info.format == "uimage"
        assert info.arch == "mipsel"

    def test_uimage_arm(self):
        data = _make_uimage(arch_field=1, name="ARM OpenWrt Linux-6.6")
        info = analyze_firmware(data, arch_hint="armel")
        assert info.arch == "armel"

    def test_gzip_wrapped_uimage(self):
        inner = _make_uimage(arch_field=5, name="MIPS OpenWrt Linux-6.6")
        data = gzip.compress(inner)
        info = analyze_firmware(data, arch_hint="mipsel")
        assert info.format == "gzip"
        assert info.arch == "mipsel"
        assert info.uimage is not None

    def test_zip_wrapped_squashfs(self):
        sq = _make_squashfs("le")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("rootfs", sq)
        info = analyze_firmware(buf.getvalue(), arch_hint="mipsel")
        assert info.format == "zip"
        assert len(info.squashfs) >= 1

    def test_raw_with_hint(self):
        info = analyze_firmware(b"\x00" * 128, arch_hint="armel")
        assert info.arch == "armel"

    def test_arch_hint_priority_over_squashfs(self):
        data = _make_squashfs("le")
        info = analyze_firmware(data, arch_hint="mipseb")
        assert info.arch == "mipseb"

class TestUimageCompressionRecursion:
    def _wrap(self, comp: int, payload: bytes, name="MIPS Tenda Linux") -> bytes:
        hdr = _make_uimage(arch_field=5, name=name)
        hdr = bytearray(hdr)
        hdr[31] = comp
        struct.pack_into(">I", hdr, 12, len(payload))
        return bytes(hdr[:64]) + payload

    def test_lzma_payload_squashfs(self):
        import lzma
        sq = _make_squashfs("le")
        data = self._wrap(3, lzma.compress(sq))
        info = analyze_firmware(data)
        assert info.uimage is not None
        assert info.uimage.comp == "lzma"
        assert len(info.squashfs) >= 1
        assert info.arch == "mipsel"

    def test_gzip_payload(self):
        sq = _make_squashfs("le")
        data = self._wrap(1, gzip.compress(sq))
        info = analyze_firmware(data)
        assert info.uimage.comp == "gzip"
        assert len(info.squashfs) >= 1

    def test_bzip2_payload(self):
        import bz2
        sq = _make_squashfs("be")
        data = self._wrap(2, bz2.compress(sq))
        info = analyze_firmware(data)
        assert info.uimage.comp == "bzip2"
        assert len(info.squashfs) >= 1

    def test_lzma_corrupt_payload_no_crash(self):
        data = self._wrap(3, b"\x00\xffnot-lzma" * 10)
        info = analyze_firmware(data)
        assert info.uimage.comp == "lzma"
        assert info.squashfs == []

    def test_plain_uncompressed_uimage_not_decompressed(self):
        data = _make_uimage(name="MIPS OpenWrt")
        info = analyze_firmware(data)
        assert info.uimage.comp == "none"


class TestFitFormat:
    def test_identify_fit(self):
        fit = b"\xd0\x0d\xfe\xed" + b"\x00" * 64
        assert identify_format(fit) == "fit"

    def test_analyze_fit_flag(self):
        fit = b"\xd0\x0d\xfe\xed" + b"\x00" * 64
        info = analyze_firmware(fit, arch_hint="armel")
        assert info.format == "fit"
        assert info.fit is True
