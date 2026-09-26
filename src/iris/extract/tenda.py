"""L1 parser for Tenda firmware containers.

Two families validated on the working corpus:

  TendaW (RP3V3.0, TC3T14C): ASCII "TDxxxx" header + embedded ZIP whose
  members are 64-byte uImage-wrapped partitions. Members named "*.squash"
  are actually JFFS2 (payload magic 0x85190320); jefferson mis-sniffs
  endianness unless the 64-byte wrapper is stripped before extraction.

  Tenda wrapper (G1V3.1si, i27): uImage magic with name field
  "Tenda_upgrade" at [48:61]; payload at [64:] is an inner image
  (uImage kernel chain or FIT) possibly followed by encrypted
  "YZTenda"-marked segments.
"""

from __future__ import annotations

import io
import re
import shutil
import struct
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

UIMAGE_MAGIC_BYTES = b"\x27\x05\x19\x56"
TENDA_WRAPPER_NAME = b"Tenda_upgrade"
TENDA_ZIP_SCAN_LIMIT = 8192
SEGMENT_MARKER = b"YZTenda"

JFFS2_MAGICS = (b"\x85\x19\x03\x20", b"\x20\x03\x19\x85")
SQUASHFS_MAGICS = (b"hsqs", b"sqsh")
FIT_MAGIC = b"\xd0\x0d\xfe\xed"

PARTITION_MOUNTS = {
    "romfs": "/",
    "user": "/opt/app",
    "custom": "/opt/custom",
    "slave": "/opt/sav",
}


@dataclass
class TendaPartition:
    name: str
    filename: str
    size: int
    payload_type: str  # jffs2 | squashfs | uimage | fit | raw
    mount_point: str = ""
    entry: str = ""  # full ZIP member path; filename is only the basename


@dataclass
class TendaContainer:
    model: str
    version: str
    zip_offset: int
    partitions: list[TendaPartition] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


def _ascii_tag(data: bytes, offset: int, maxlen: int) -> str:
    raw = data[offset : offset + maxlen].split(b"\x00")[0]
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return ""
    return text if text.isprintable() else ""


def _header_fields(data: bytes, upto: int) -> tuple[str, str]:
    """TendaW header is NUL-separated ASCII fields: model tag, version, flags."""
    tokens = [
        t.decode("ascii")
        for t in data[:upto].split(b"\x00")
        if t and all(32 <= b < 127 for b in t)
    ]
    model = tokens[0] if tokens else ""
    version = next((t for t in tokens[1:] if t[:1].isalpha()), "")
    return model, version


def is_tendaw(data: bytes) -> bool:
    if len(data) < 64 or not data.startswith(b"TD"):
        return False
    if not _ascii_tag(data, 0, 16):
        return False
    return b"PK\x03\x04" in data[:TENDA_ZIP_SCAN_LIMIT]


def is_tenda_wrapper(data: bytes) -> bool:
    return len(data) > 64 and data[:4] == UIMAGE_MAGIC_BYTES and data[48:61] == TENDA_WRAPPER_NAME


def classify_payload(payload: bytes) -> str:
    if payload[:4] in JFFS2_MAGICS:
        return "jffs2"
    if payload[:4] in SQUASHFS_MAGICS:
        return "squashfs"
    if payload[:4] == UIMAGE_MAGIC_BYTES:
        return "uimage"
    if payload[:4] == FIT_MAGIC:
        return "fit"
    return "raw"


def _load_zip(data: bytes, base: int) -> tuple[zipfile.ZipFile, int, bytearray]:
    """Open the embedded ZIP, re-anchoring and un-obfuscating Tenda tweaks:
    the first local header magic is "ZL\\x03\\x04" and the central directory
    uses offsets relative to that earlier start (negative for our base)."""
    buf = bytearray(data[base:])
    zf = zipfile.ZipFile(io.BytesIO(buf))
    min_off = min((i.header_offset for i in zf.infolist()), default=0)
    if min_off < 0:
        base += min_off
        buf = bytearray(data[base:])
    for info in zf.infolist():
        off = info.header_offset
        if buf[off : off + 4] == b"ZL\x03\x04":
            buf[off : off + 2] = b"PK"
    return zipfile.ZipFile(io.BytesIO(buf)), base, buf


def parse_tendaw(data: bytes) -> TendaContainer | None:
    if not is_tendaw(data):
        return None
    zip_offset = data.find(b"PK\x03\x04")
    if zip_offset < 0:
        return None
    model, version = _header_fields(data, zip_offset)

    try:
        zf, zip_offset, _buf = _load_zip(data, zip_offset)
    except (zipfile.BadZipFile, ValueError, OSError):
        return None
    container = TendaContainer(model=model, version=version, zip_offset=zip_offset)
    for entry in zf.namelist():
        if entry.endswith("/"):
            continue
        base_name = Path(entry).name
        try:
            member = zf.read(entry)
        except (ValueError, RuntimeError, NotImplementedError):
            container.unreadable.append(base_name)
            continue
        if not base_name.endswith(".img"):
            container.scripts.append(base_name)
            continue
        if len(member) < 72:
            continue
        ptype = classify_payload(member[64:])
        raw_name = _ascii_tag(member, 32, 32) or Path(base_name).stem
        part = TendaPartition(
            name=re.sub(r"[^A-Za-z0-9._+-]", "_", raw_name),
            filename=base_name,
            size=len(member) - 64,
            payload_type=ptype,
            entry=entry,
        )
        part.mount_point = PARTITION_MOUNTS.get(part.name, "")
        container.partitions.append(part)
    return container


def detect_segmented_regions(data: bytes, marker: bytes = SEGMENT_MARKER, min_count: int = 3) -> list[int]:
    """Offsets of repeated segment headers (e.g. encrypted i27 rootfs chunks)."""
    hits: list[int] = []
    pos = 0
    while True:
        pos = data.find(marker, pos)
        if pos < 0:
            break
        hits.append(pos)
        pos += 1
    if len(hits) < min_count:
        return []
    return hits


def extract_tenda_wrapper(data: bytes) -> bytes:
    """Strip the 64-byte Tenda_upgrade header, returning the inner payload."""
    if not is_tenda_wrapper(data):
        return data
    return data[64:]


def slice_partition(data: bytes, zip_offset: int, part: TendaPartition, dest: Path) -> Path:
    """Extract one ZIP member's payload (64-byte wrapper stripped) to *dest*."""
    zf, _base, _buf = _load_zip(data, zip_offset)
    member = zf.read(part.entry or part.filename)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(member[64:])
    return dest


def jefferson_extract(jffs2_path: Path, dest_dir: Path) -> Path:
    exe = shutil.which("jefferson")
    if exe is None:
        raise RuntimeError("jefferson not found on PATH (pip install jefferson)")
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True)
    result = subprocess.run(
        [exe, "-d", str(dest_dir), "-f", str(jffs2_path)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"jefferson failed (rc={result.returncode}): {result.stderr.strip()[-300:]}")
    return dest_dir
