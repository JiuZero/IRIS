"""L1 rootfs extraction: squashfs slice + Docker unsquashfs + ELF arch verification.

Workflow:
  1. analyze_firmware() to find squashfs offset
  2. slice squashfs from firmware binary
  3. docker run alpine + unsquashfs to decompress
  4. walk extracted tree for ELF files, census archs
"""

from __future__ import annotations

import os
import struct
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from iris.extract.firmware import FirmwareInfo, analyze_firmware


@dataclass
class RootfsExtraction:
    firmware_info: FirmwareInfo
    squashfs_path: Path | None = None
    rootfs_dir: Path | None = None
    elf_count: int = 0
    elf_archs: Counter = None  # type: ignore[assignment]
    arch_verified: str = ""

    def __post_init__(self) -> None:
        if self.elf_archs is None:
            self.elf_archs = Counter()


def _slice_squashfs(data: bytes, offset: int, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data[offset:])
    return dest


def _docker_unsquashfs(squashfs_path: Path, dest_dir: Path, image: str = "alpine:3.20") -> Path:
    scratch_dir = squashfs_path.parent
    rel_sqfs = squashfs_path.name
    rel_dest = dest_dir.name

    if dest_dir.exists():
        import shutil

        shutil.rmtree(dest_dir)

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{scratch_dir.resolve()}:/work",
        image, "sh", "-c",
        (
            f"apk add --no-cache squashfs-tools >/dev/null 2>&1 && "
            f"unsquashfs -d /work/{rel_dest} /work/{rel_sqfs} >/dev/null 2>&1 && "
            f"echo OK"
        ),
    ]
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120, check=False)
    if result.returncode != 0 or "OK" not in result.stdout:
        raise RuntimeError(
            f"unsquashfs failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    return dest_dir


def _census_elfs(rootfs_dir: Path) -> tuple[int, Counter]:
    counter: Counter = Counter()
    count = 0
    for root, _dirs, files in os.walk(rootfs_dir):
        for f in files:
            path = os.path.join(root, f)
            try:
                with open(path, "rb") as fh:
                    magic = fh.read(4)
                    if magic != b"\x7fELF":
                        continue
                    fh.seek(5)
                    ei_data = ord(fh.read(1))
                    fh.seek(18)
                    e_machine = struct.unpack("<H" if ei_data == 1 else ">H", fh.read(2))[0]
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
                    count += 1
            except OSError:
                pass
    return count, counter


def extract_rootfs(
    firmware_path: Path,
    scratch_dir: Path,
    arch_hint: str = "",
    docker_image: str = "alpine:3.20",
) -> RootfsExtraction:
    data = firmware_path.read_bytes()
    fw_info = analyze_firmware(data, arch_hint=arch_hint)
    result = RootfsExtraction(firmware_info=fw_info)

    if fw_info.rootfs_offset is None:
        return result

    stem = firmware_path.stem
    sqfs_path = scratch_dir / f"{stem}.squashfs"
    rootfs_dir = scratch_dir / f"{stem}-rootfs"

    _slice_squashfs(data, fw_info.rootfs_offset, sqfs_path)
    result.squashfs_path = sqfs_path

    _docker_unsquashfs(sqfs_path, rootfs_dir, image=docker_image)
    result.rootfs_dir = rootfs_dir

    count, counter = _census_elfs(rootfs_dir)
    result.elf_count = count
    result.elf_archs = counter
    if counter:
        result.arch_verified = counter.most_common(1)[0][0]

    return result