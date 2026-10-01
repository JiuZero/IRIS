"""Tests for iris.extract.ubi — UBI EC/VID header parsing and volume extraction."""

from __future__ import annotations

import struct

from iris.extract.ubi import (
    UBI_EC_MAGIC,
    UBI_INT_VOL_ID,
    UBI_VID_MAGIC,
    determine_peb_size,
    extract_squashfs_from_ubi,
    extract_volumes,
    find_ec_headers,
    parse_ec_header,
    parse_vid_header,
)


def _make_ec_header(vid_hdr_offset: int = 0x800, data_offset: int = 0x1000) -> bytes:
    hdr = bytearray(64)
    hdr[0:4] = UBI_EC_MAGIC
    hdr[4] = 1
    struct.pack_into(">Q", hdr, 8, 0)
    struct.pack_into(">I", hdr, 16, vid_hdr_offset)
    struct.pack_into(">I", hdr, 20, data_offset)
    struct.pack_into(">I", hdr, 24, 0x67A14CB1)
    return bytes(hdr)


def _make_vid_header(
    vol_type: int = 1,
    vol_id: int = 0,
    lnum: int = 0,
    data_size: int = 0,
) -> bytes:
    hdr = bytearray(64)
    hdr[0:4] = UBI_VID_MAGIC
    hdr[4] = 1
    hdr[5] = vol_type
    hdr[6] = 0
    hdr[7] = 5
    struct.pack_into(">I", hdr, 8, vol_id)
    struct.pack_into(">I", hdr, 12, lnum)
    struct.pack_into(">I", hdr, 16, data_size)
    return bytes(hdr)


def _make_squashfs(size: int = 256) -> bytes:
    hdr = bytearray(size)
    hdr[0:4] = b"hsqs"
    struct.pack_into("<Q", hdr, 40, size)
    return bytes(hdr)


def _make_peb(
    peb_size: int = 131072,
    vid_hdr_offset: int = 0x800,
    data_offset: int = 0x1000,
    vol_id: int = 0,
    lnum: int = 0,
    payload: bytes | None = None,
    empty: bool = False,
) -> bytes:
    peb = bytearray(peb_size)
    if empty:
        return bytes(peb)
    peb[0:64] = _make_ec_header(vid_hdr_offset, data_offset)
    vid = _make_vid_header(vol_id=vol_id, lnum=lnum)
    if vid_hdr_offset + 64 <= peb_size:
        peb[vid_hdr_offset : vid_hdr_offset + 64] = vid
    if payload and data_offset + len(payload) <= peb_size:
        peb[data_offset : data_offset + len(payload)] = payload
    return bytes(peb)


class TestParseEcHeader:
    def test_valid(self):
        data = _make_ec_header()
        ec = parse_ec_header(data, 0)
        assert ec is not None
        assert ec.version == 1
        assert ec.vid_hdr_offset == 0x800
        assert ec.data_offset == 0x1000

    def test_bad_magic(self):
        data = b"\x00" * 64
        assert parse_ec_header(data, 0) is None

    def test_too_short(self):
        assert parse_ec_header(b"UBI#", 0) is None

    def test_at_offset(self):
        data = b"\x00" * 256 + _make_ec_header()
        ec = parse_ec_header(data, 256)
        assert ec is not None
        assert ec.offset == 256


class TestParseVidHeader:
    def test_valid(self):
        peb = _make_peb()
        vid = parse_vid_header(peb, 0x800)
        assert vid is not None
        assert vid.vol_id == 0
        assert vid.lnum == 0

    def test_bad_magic(self):
        peb = bytearray(64)
        assert parse_vid_header(bytes(peb), 0) is None

    def test_vol_id(self):
        peb = _make_peb(vol_id=5, lnum=3)
        vid = parse_vid_header(peb, 0x800)
        assert vid is not None
        assert vid.vol_id == 5
        assert vid.lnum == 3


class TestFindEcHeaders:
    def test_single(self):
        data = _make_ec_header()
        assert find_ec_headers(data) == [0]

    def test_multiple(self):
        peb = _make_peb(peb_size=512)
        data = peb + peb
        offsets = find_ec_headers(data)
        assert offsets == [0, 512]

    def test_none(self):
        assert find_ec_headers(b"\x00" * 256) == []

    def test_with_prefix(self):
        data = b"\x00" * 100 + _make_ec_header()
        assert find_ec_headers(data) == [100]


class TestDeterminePebSize:
    def test_uniform(self):
        offsets = [0, 131072, 262144, 393216]
        assert determine_peb_size(offsets) == 131072

    def test_single_offset(self):
        assert determine_peb_size([0]) == 0

    def test_majority_vote(self):
        offsets = [0, 131072, 262144, 262145]
        assert determine_peb_size(offsets) == 131072


class TestExtractVolumes:
    def test_single_volume_single_leb(self):
        sqfs = _make_squashfs(256)
        peb = _make_peb(vol_id=0, lnum=0, payload=sqfs, peb_size=8192)
        peb_empty = _make_peb(peb_size=8192, vol_id=0, lnum=1)
        vols = extract_volumes(peb + peb_empty, 0)
        assert 0 in vols
        assert vols[0].data[:4] == b"hsqs"

    def test_multi_leb(self):
        peb_size = 8192
        leb_size = peb_size - 0x1000
        payload_a = b"\xAA" * leb_size
        payload_b = b"\xBB" * leb_size
        peb0 = _make_peb(vol_id=0, lnum=0, payload=payload_a, peb_size=peb_size)
        peb1 = _make_peb(vol_id=0, lnum=1, payload=payload_b, peb_size=peb_size)
        vols = extract_volumes(peb0 + peb1, 0)
        assert 0 in vols
        assert vols[0].data[:leb_size] == b"\xAA" * leb_size
        assert vols[0].data[leb_size : leb_size * 2] == b"\xBB" * leb_size

    def test_skip_internal_volume(self):
        peb_size = 8192
        sqfs = _make_squashfs(256)
        peb_int = _make_peb(
            vol_id=UBI_INT_VOL_ID, lnum=0, payload=b"\x00" * 256, peb_size=peb_size
        )
        peb_data = _make_peb(vol_id=0, lnum=0, payload=sqfs, peb_size=peb_size)
        vols = extract_volumes(peb_int + peb_data, 0)
        assert UBI_INT_VOL_ID not in vols
        assert 0 in vols

    def test_empty_pebs_skipped(self):
        peb_size = 8192
        sqfs = _make_squashfs(256)
        peb_empty = _make_peb(peb_size=peb_size, empty=True)
        peb_data0 = _make_peb(vol_id=0, lnum=0, payload=sqfs, peb_size=peb_size)
        peb_data1 = _make_peb(vol_id=0, lnum=1, peb_size=peb_size)
        vols = extract_volumes(peb_empty + peb_data0 + peb_data1 + peb_empty, 0)
        assert 0 in vols

    def test_no_ec_headers(self):
        assert extract_volumes(b"\x00" * 256, 0) == {}


class TestExtractSquashfsFromUbi:
    def test_squashfs_volume(self):
        sqfs = _make_squashfs(256)
        peb = _make_peb(vol_id=0, lnum=0, payload=sqfs, peb_size=8192)
        peb2 = _make_peb(peb_size=8192, vol_id=0, lnum=1)
        result = extract_squashfs_from_ubi(peb + peb2, 0)
        assert result is not None
        assert result[:4] == b"hsqs"

    def test_no_volumes(self):
        assert extract_squashfs_from_ubi(b"\x00" * 256, 0) is None

    def test_non_squashfs_volume(self):
        peb = _make_peb(vol_id=0, lnum=0, payload=b"\x00" * 256, peb_size=8192)
        peb2 = _make_peb(peb_size=8192, vol_id=0, lnum=1)
        result = extract_squashfs_from_ubi(peb + peb2, 0)
        assert result is None

    def test_truncated_squashfs(self):
        hdr = bytearray(64)
        hdr[0:4] = b"hsqs"
        struct.pack_into("<Q", hdr, 40, 64)
        peb = _make_peb(vol_id=0, lnum=0, payload=bytes(hdr), peb_size=8192)
        peb2 = _make_peb(peb_size=8192, vol_id=0, lnum=1)
        result = extract_squashfs_from_ubi(peb + peb2, 0)
        assert result is not None
        assert len(result) == 64
