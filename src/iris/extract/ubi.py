"""L1 UBI volume parser — extract embedded squashfs from UBI containers.

Pure-Python implementation of UBI PEB scanning, EC/VID header parsing,
and LEB concatenation. Handles OpenWrt mvebu/ipq806x firmware images
that wrap rootfs squashfs inside a UBI static volume.

UBI on-flash layout (per PEB):
  [EC header (64B)] [VID header at vid_hdr_offset] [data at data_offset] [padding]

EC header magic: b"UBI#"  — erase counter header
VID header magic: b"UBI!" — volume ID header (only on mapped PEBs)
Internal layout volume uses vol_id = 0x7FFFEFFF (UBI_INT_VOL_ID) and is skipped.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

UBI_EC_MAGIC = b"UBI#"
UBI_VID_MAGIC = b"UBI!"
UBI_INT_VOL_ID = 0x7FFFEFFF

EC_HDR_LEN = 64
VID_HDR_LEN = 64


@dataclass
class UbiEcHeader:
    offset: int
    version: int
    ec: int
    vid_hdr_offset: int
    data_offset: int
    image_seq: int


@dataclass
class UbiVidHeader:
    vol_type: int
    vol_id: int
    lnum: int
    data_size: int
    used_ebs: int
    data_pad: int


@dataclass
class UbiVolume:
    vol_id: int
    lebs: dict[int, bytes] = field(default_factory=dict)

    @property
    def data(self) -> bytes:
        return b"".join(self.lebs[k] for k in sorted(self.lebs))


def parse_ec_header(data: bytes, offset: int) -> UbiEcHeader | None:
    if offset + EC_HDR_LEN > len(data):
        return None
    if data[offset : offset + 4] != UBI_EC_MAGIC:
        return None
    version = data[offset + 4]
    ec = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
    vid_hdr_offset = struct.unpack(">I", data[offset + 16 : offset + 20])[0]
    data_offset = struct.unpack(">I", data[offset + 20 : offset + 24])[0]
    image_seq = struct.unpack(">I", data[offset + 24 : offset + 28])[0]
    return UbiEcHeader(
        offset=offset,
        version=version,
        ec=ec,
        vid_hdr_offset=vid_hdr_offset,
        data_offset=data_offset,
        image_seq=image_seq,
    )


def parse_vid_header(peb: bytes, vid_off: int) -> UbiVidHeader | None:
    if vid_off + VID_HDR_LEN > len(peb):
        return None
    if peb[vid_off : vid_off + 4] != UBI_VID_MAGIC:
        return None
    vol_type = peb[vid_off + 5]
    vol_id = struct.unpack(">I", peb[vid_off + 8 : vid_off + 12])[0]
    lnum = struct.unpack(">I", peb[vid_off + 12 : vid_off + 16])[0]
    data_size = struct.unpack(">I", peb[vid_off + 16 : vid_off + 20])[0]
    used_ebs = struct.unpack(">I", peb[vid_off + 20 : vid_off + 24])[0]
    data_pad = struct.unpack(">I", peb[vid_off + 24 : vid_off + 28])[0]
    return UbiVidHeader(
        vol_type=vol_type,
        vol_id=vol_id,
        lnum=lnum,
        data_size=data_size,
        used_ebs=used_ebs,
        data_pad=data_pad,
    )


def find_ec_headers(data: bytes, start: int = 0) -> list[int]:
    results: list[int] = []
    pos = start
    while True:
        pos = data.find(UBI_EC_MAGIC, pos)
        if pos == -1:
            break
        results.append(pos)
        pos += 4
    return results


def determine_peb_size(data: bytes, ec_offsets: list[int]) -> int:
    if len(ec_offsets) < 2:
        return 0
    sizes: dict[int, int] = {}
    for i in range(1, len(ec_offsets)):
        delta = ec_offsets[i] - ec_offsets[i - 1]
        sizes[delta] = sizes.get(delta, 0) + 1
    if not sizes:
        return 0
    return max(sizes, key=sizes.get)


def extract_volumes(data: bytes, ubi_offset: int = 0) -> dict[int, UbiVolume]:
    ec_offsets = find_ec_headers(data, ubi_offset)
    if len(ec_offsets) < 2:
        return {}
    peb_size = determine_peb_size(data, ec_offsets)
    if peb_size == 0:
        return {}

    volumes: dict[int, UbiVolume] = {}
    for ec_off in ec_offsets:
        ec = parse_ec_header(data, ec_off)
        if ec is None:
            continue
        peb = data[ec_off : ec_off + peb_size]
        vid = parse_vid_header(peb, ec.vid_hdr_offset)
        if vid is None:
            continue
        if vid.vol_id == UBI_INT_VOL_ID:
            continue
        leb_data = peb[ec.data_offset :]
        if vid.vol_id not in volumes:
            volumes[vid.vol_id] = UbiVolume(vol_id=vid.vol_id)
        volumes[vid.vol_id].lebs[vid.lnum] = leb_data
    return volumes


def extract_squashfs_from_ubi(data: bytes, ubi_offset: int = 0) -> bytes | None:
    volumes = extract_volumes(data, ubi_offset)
    if not volumes:
        return None
    target_vol = None
    for vol in volumes.values():
        if vol.data[:4] == b"hsqs" or vol.data[:4] == b"sqsh":
            target_vol = vol
            break
    if target_vol is None:
        target_vol = volumes[min(volumes.keys())]
    vol_data = target_vol.data
    if vol_data[:4] in (b"hsqs", b"sqsh"):
        endian = "<" if vol_data[:4] == b"hsqs" else ">"
        if len(vol_data) >= 48:
            bytes_used = struct.unpack(f"{endian}Q", vol_data[40:48])[0]
            if 0 < bytes_used <= len(vol_data):
                return vol_data[:bytes_used]
        return vol_data
    return None