"""Tests for iris.extract.tenda — TendaW ZIP container and Tenda_upgrade wrapper."""

from __future__ import annotations

import io
import struct
import zipfile

from iris.extract.firmware import analyze_firmware, identify_format, parse_uimage
from iris.extract.tenda import (
    SEGMENT_MARKER,
    TendaContainer,
    detect_segmented_regions,
    extract_tenda_wrapper,
    is_tenda_wrapper,
    is_tendaw,
    parse_tendaw,
)


def _tenda_wrapper_header(name: bytes = b"Tenda_upgrade") -> bytes:
    hdr = bytearray(64)
    hdr[0:4] = b"\x27\x05\x19\x56"
    hdr[48 : 48 + len(name)] = name
    return bytes(hdr)


def _part_img(part_name: str, payload: bytes) -> bytes:
    hdr = bytearray(64)
    hdr[0:4] = b"\x27\x05\x19\x56"
    hdr[32:64] = part_name.encode("ascii").ljust(32, b"\x00")
    return bytes(hdr) + payload


def _uimage_header(name: str = "MIPS Tenda Linux-4.14.90") -> bytes:
    hdr = bytearray(64)
    struct.pack_into(">I", hdr, 0, 0x27051956)
    hdr[28] = 5
    hdr[29] = 5  # MIPS
    hdr[30] = 2
    hdr[31] = 3  # lzma
    hdr[32:64] = name.encode("ascii")[:32].ljust(32, b"\x00")
    return bytes(hdr)


def _squashfs_block() -> bytes:
    blk = bytearray(b"hsqs" + b"\x00" * 128)
    struct.pack_into("<Q", blk, 40, 64)
    return bytes(blk)


def _jffs2_payload() -> bytes:
    return b"\x20\x03\x19\x85" + b"\x00" * 60


def _make_tendaw() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Install.lua", b"-- lua\n")
        zf.writestr("Install", b"upg\n")
        zf.writestr("romfs-x.squash.img", _part_img("romfs", _jffs2_payload()))
        zf.writestr("user-x.squash.img", _part_img("user", _jffs2_payload()))
        zf.writestr("custom-x.squash.img", _part_img("custom", _jffs2_payload()))
        zf.writestr("slave-x.jffs2.img", _part_img("slave", _jffs2_payload()))
        zf.writestr("uImage.img", _part_img("uImage", _uimage_header() + b"\x00" * 8))
    zip_bytes = buf.getvalue()

    header = bytearray(2985)
    header[0:11] = b"TD0101AC_39"
    header[21:32] = b"V31.1.10.91"
    return bytes(header) + zip_bytes


class TestTendaW:
    def test_identify(self):
        assert is_tendaw(_make_tendaw())
        assert identify_format(_make_tendaw()) == "tendaw"

    def test_not_tendaw_random_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.txt", b"x")
        assert not is_tendaw(buf.getvalue())

    def test_parse_partitions(self):
        c = parse_tendaw(_make_tendaw())
        assert isinstance(c, TendaContainer)
        assert c.model == "TD0101AC_39"
        assert c.version == "V31.1.10.91"
        by_name = {p.name: p for p in c.partitions}
        assert by_name["romfs"].payload_type == "jffs2"
        assert by_name["romfs"].mount_point == "/"
        assert by_name["user"].mount_point == "/opt/app"
        assert by_name["custom"].mount_point == "/opt/custom"
        assert by_name["slave"].mount_point == "/opt/sav"
        assert by_name["uImage"].payload_type == "uimage"
        assert set(c.scripts) == {"Install.lua", "Install"}

    def test_analyze_firmware_tendaw(self):
        info = analyze_firmware(_make_tendaw())
        assert info.format == "tendaw"
        assert info.tendaw is not None
        assert len(info.tendaw.partitions) == 5

    def test_zl_obfuscated_first_local_header(self):
        # real RP3/Tenda archives rewrite the first local header magic PK -> ZL
        raw = bytearray(_make_tendaw())
        first_pk = raw.find(b"PK\x03\x04")
        raw[first_pk : first_pk + 2] = b"ZL"
        c = parse_tendaw(bytes(raw))
        assert c is not None
        assert c.zip_offset == first_pk
        assert len(c.partitions) == 5
        assert not c.unreadable


class TestTendaWrapper:
    def test_identify_and_unwrap(self):
        data = _tenda_wrapper_header() + b"inner"
        assert is_tenda_wrapper(data)
        assert identify_format(data) == "tenda_wrapper"
        assert extract_tenda_wrapper(data) == b"inner"

    def test_wrapper_offsets_shifted(self):
        inner = _uimage_header() + b"\x00" * 16 + _squashfs_block()
        data = _tenda_wrapper_header() + inner
        info = analyze_firmware(data)
        assert info.format == "tenda_wrapper"
        assert info.uimage is not None
        assert info.uimage.name.startswith("MIPS Tenda")
        assert info.rootfs_offset == 64 + 64 + 16 + 0
        assert info.squashfs[0].offset == 144

    def test_plain_uimage_not_wrapper(self):
        data = _uimage_header(name="MIPS OpenWrt") + b"\x00" * 64
        assert not is_tenda_wrapper(data)
        assert identify_format(data) == "uimage"


class TestSegmentedRegions:
    def test_detects_repeated_markers(self):
        data = bytearray(b"\x00" * 1_000_000)
        for off in (300_000, 600_000, 900_000):
            data[off : off + len(SEGMENT_MARKER)] = SEGMENT_MARKER
        hits = detect_segmented_regions(bytes(data))
        assert hits == [300_000, 600_000, 900_000]

    def test_below_threshold_empty(self):
        data = b"abc" + SEGMENT_MARKER + b"def" + SEGMENT_MARKER
        assert detect_segmented_regions(data) == []
