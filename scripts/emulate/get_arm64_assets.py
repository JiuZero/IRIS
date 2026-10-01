"""Fetch & install arm64 L2 emulation assets into binaries/ (idempotent).

Sources (pinned):
  - Alpine v3.20 netboot vmlinuz-virt (EFI zboot) -> unwrap to raw arm64 Image
  - Alpine v3.20 minirootfs (aarch64)             -> /bin/busybox

Usage:  python scripts/emulate/get_arm64_assets.py [--dest binaries]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import pathlib
import struct
import sys
import tarfile
import urllib.request
import zlib

ALPINE_BASE = "https://dl-cdn.alpinelinux.org/alpine/v3.20/releases/aarch64/"
VMLINUX_URL = ALPINE_BASE + "netboot-3.20.10/vmlinuz-virt"
MINIROOTFS_URL = ALPINE_BASE + "alpine-minirootfs-3.20.10-aarch64.tar.gz"

IMAGE_SHA256 = "f8fbdc03e49084b03518c35f13c7a1295a8f7d5bc5ac47f8af1ee4b9f061fa1f"
BUSYBOX_SHA256 = "b9d035e89b4c0575872bade852c34ccc5a4a1ee4bdd105bb445eb16c9aeba1ba"

ARM64_IMAGE_MAGIC = b"ARMd"  # at offset 0x38 of a raw arm64 kernel Image


def _fetch(url: str) -> bytes:
    print(f"fetching {url} ...", flush=True)
    with urllib.request.urlopen(url, timeout=300) as resp:
        return resp.read()


def _try_gzip_member(data: bytes, pos: int) -> bytes | None:
    d = zlib.decompressobj(31)
    try:
        out = d.decompress(data[pos : pos + 64 << 20])
    except zlib.error:
        return None
    return out if len(out) > 0x40 else None


def unwrap_zboot(blob: bytes) -> bytes:
    """EFI zboot: 'zimg' at 0x4; payload offset/size live in the header."""
    if blob[4:8] != b"zimg":
        raise RuntimeError("not an EFI zboot image")
    candidates = [struct.unpack("<I", blob[8:12])[0]]
    pos = 0
    while True:
        pos = blob.find(b"\x1f\x8b\x08", pos)
        if pos < 0:
            break
        candidates.append(pos)
        pos += 1
    for off in candidates:
        inner = _try_gzip_member(blob, off) if off + 2 < len(blob) else None
        if inner and inner[0x38:0x3C] == ARM64_IMAGE_MAGIC:
            return inner
    raise RuntimeError("no gzip'd arm64 Image found inside zboot header")


def _install(dest: pathlib.Path, name: str, data: bytes, want_sha: str) -> None:
    got = hashlib.sha256(data).hexdigest()
    if want_sha and got != want_sha:
        raise RuntimeError(f"{name}: sha256 mismatch: {got} (expected {want_sha})")
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / name
    path.write_bytes(data)
    print(f"wrote {path} ({len(data)} bytes, sha256 {got[:16]}...)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default="binaries", type=pathlib.Path)
    args = ap.parse_args()

    image = pathlib.Path(args.dest) / "Image.arm64"
    busybox = pathlib.Path(args.dest) / "busybox.arm64"
    if image.exists() and hashlib.sha256(image.read_bytes()).hexdigest() == IMAGE_SHA256:
        print("Image.arm64 already present and verified")
    else:
        _install(args.dest, "Image.arm64", unwrap_zboot(_fetch(VMLINUX_URL)), IMAGE_SHA256)
    if busybox.exists() and hashlib.sha256(busybox.read_bytes()).hexdigest() == BUSYBOX_SHA256:
        print("busybox.arm64 already present and verified")
    else:
        with tarfile.open(fileobj=io.BytesIO(_fetch(MINIROOTFS_URL)), mode="r:gz") as tf:
            member = next(n for n in tf.getnames() if n.endswith("bin/busybox"))
            _install(args.dest, "busybox.arm64", tf.extractfile(member).read(), BUSYBOX_SHA256)
    print("arm64 assets OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
