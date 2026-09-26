"""L1 rootfs extraction: squashfs slice + Docker unsquashfs + ELF arch verification.

Workflow:
  1. analyze_firmware() to find squashfs offset or UBI container
  2a. squashfs path: slice squashfs from firmware binary
  2b. UBI path: parse UBI volumes, extract embedded squashfs
  3. docker run alpine + unsquashfs to decompress
  4. walk extracted tree for ELF files, census archs
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from iris.extract.firmware import FirmwareInfo, analyze_firmware
from iris.extract.ubi import extract_squashfs_from_ubi


@dataclass
class RootfsExtraction:
    firmware_info: FirmwareInfo
    squashfs_path: Path | None = None
    rootfs_dir: Path | None = None
    elf_count: int = 0
    elf_archs: Counter = None  # type: ignore[assignment]
    arch_verified: str = ""
    extraction_method: str = ""
    failure: str = ""  # structured failure profile when rootfs_dir is None

    def __post_init__(self) -> None:
        if self.elf_archs is None:
            self.elf_archs = Counter()


def classify_failure(fw_info: FirmwareInfo) -> str:
    """Attribute an extraction failure to a concrete format-level cause."""
    if fw_info.fit and fw_info.segmented_offsets:
        return (
            f"encrypted-fit: image wraps a FIT containing {len(fw_info.segmented_offsets)} "
            "YZTenda-encrypted segments; rootfs is not extractable without the vendor "
            "decryption key"
        )
    if fw_info.fit:
        return "fit-unsupported: FIT image recognized, rootfs blob unpacking not implemented"
    if fw_info.tendaw is not None:
        return "tendaw-nojffs2: TendaW container parsed but no mountable JFFS2 partition"
    return "no-rootfs: no squashfs/UBI/TendaW structure found in image"


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


def _copy_tree_tolerant(src: Path, dst: Path) -> None:
    """Copy a jefferson-extracted tree to NTFS, skipping dangling symlinks.

    JFFS2 partitions are mostly soft links; on Windows they land as broken
    reparse points that raise WinError 123 on read. Real re-linking happens
    container-side (see orchestrator); here we only need regular files for
    ELF census and an inspectable tree.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_symlink():
            if not item.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.symlink_to(item.readlink())
            except OSError:
                pass
            continue
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(item, target)
            except OSError:
                continue


def _extract_tendaw(
    data: bytes,
    fw_info: FirmwareInfo,
    firmware_path: Path,
    scratch_dir: Path,
    result: RootfsExtraction,
) -> RootfsExtraction:
    from iris.extract.tenda import jefferson_extract, slice_partition

    container = fw_info.tendaw
    stem = firmware_path.stem
    parts_dir = scratch_dir / f"{stem}-parts"
    rootfs_dir = scratch_dir / f"{stem}-rootfs"
    if rootfs_dir.exists():
        shutil.rmtree(rootfs_dir)

    extracted: dict[str, Path] = {}
    for part in container.partitions:
        if part.payload_type != "jffs2" or not part.mount_point:
            continue
        tree = parts_dir / part.name
        if not tree.exists() or not any(tree.rglob("*")):
            raw = parts_dir / f"{part.name}.jffs2"
            slice_partition(data, container.zip_offset, part, raw)
            jefferson_extract(raw, tree)
        extracted[part.name] = tree

    base = extracted.get("romfs")
    if base is None and extracted:
        base = next(iter(extracted.values()))
    if base is None:
        result.extraction_method = "tendaw-nojffs2"
        result.failure = classify_failure(fw_info)
        return result

    _copy_tree_tolerant(base, rootfs_dir)
    for name, tree in extracted.items():
        if tree == base:
            continue
        mount = _tendaw_mount(container, name)
        if mount and mount != "/":
            _copy_tree_tolerant(tree, rootfs_dir / mount.lstrip("/"))

    result.rootfs_dir = rootfs_dir
    result.extraction_method = "tendaw-jffs2"
    count, counter = _census_elfs(rootfs_dir)
    result.elf_count = count
    result.elf_archs = counter
    if counter:
        result.arch_verified = counter.most_common(1)[0][0]
    return result


def _tendaw_mount(container, name: str) -> str:
    for p in container.partitions:
        if p.name == name:
            return p.mount_point
    return ""


def extract_rootfs(
    firmware_path: Path,
    scratch_dir: Path,
    arch_hint: str = "",
    docker_image: str = "alpine:3.20",
) -> RootfsExtraction:
    data = firmware_path.read_bytes()
    fw_info = analyze_firmware(data, arch_hint=arch_hint)
    result = RootfsExtraction(firmware_info=fw_info)

    if fw_info.tendaw is not None:
        return _extract_tendaw(data, fw_info, firmware_path, scratch_dir, result)

    if fw_info.rootfs_offset is None:
        result.failure = classify_failure(fw_info)
        return result

    stem = firmware_path.stem
    sqfs_path = scratch_dir / f"{stem}.squashfs"
    rootfs_dir = scratch_dir / f"{stem}-rootfs"

    if fw_info.ubi_offset is not None:
        sqfs_data = extract_squashfs_from_ubi(data, fw_info.ubi_offset)
        if sqfs_data is None:
            result.failure = "ubi-no-squashfs: UBI container present but no squashfs volume extracted"
            return result
        sqfs_path.parent.mkdir(parents=True, exist_ok=True)
        sqfs_path.write_bytes(sqfs_data)
        result.extraction_method = "ubi"
    else:
        _slice_squashfs(data, fw_info.rootfs_offset, sqfs_path)
        result.extraction_method = "squashfs"

    result.squashfs_path = sqfs_path

    _docker_unsquashfs(sqfs_path, rootfs_dir, image=docker_image)
    result.rootfs_dir = rootfs_dir

    count, counter = _census_elfs(rootfs_dir)
    result.elf_count = count
    result.elf_archs = counter
    if counter:
        result.arch_verified = counter.most_common(1)[0][0]

    return result