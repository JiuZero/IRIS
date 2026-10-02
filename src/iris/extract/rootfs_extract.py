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
import re
import shutil
import struct
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from iris.extract.firmware import FirmwareInfo, analyze_firmware
from iris.extract.ubi import extract_squashfs_from_ubi
from iris.failures import Failure, FailureKind
from iris.fsutil import safe_exists, safe_is_dir, safe_present, safe_rmtree


@dataclass
class RootfsExtraction:
    firmware_info: FirmwareInfo
    squashfs_path: Path | None = None
    rootfs_dir: Path | None = None
    elf_count: int = 0
    elf_archs: Counter = None  # type: ignore[assignment]
    arch_verified: str = ""
    extraction_method: str = ""
    #: Set when ``rootfs_dir`` is None. A ``Failure``, not a string: the kind is
    #: what gets aggregated, and a prose-only field is exactly what made the
    #: failure taxonomy uncountable before.
    failure: Failure | None = None

    def __post_init__(self) -> None:
        if self.elf_archs is None:
            self.elf_archs = Counter()

    @property
    def failure_reason(self) -> str:
        """The prose form, for display. Empty when extraction succeeded."""
        return self.failure.message if self.failure else ""


def classify_failure(fw_info: FirmwareInfo) -> Failure:
    """Attribute an extraction failure to a concrete format-level cause."""
    if fw_info.fit and fw_info.segmented_offsets:
        return Failure(
            FailureKind.ENCRYPTED_FIT,
            f"image wraps a FIT containing {len(fw_info.segmented_offsets)} "
            "YZTenda-encrypted segments; rootfs is not extractable without the vendor "
            "decryption key",
            evidence={"segments": len(fw_info.segmented_offsets)},
        )
    if fw_info.fit:
        return Failure(FailureKind.FIT_UNSUPPORTED,
                       "FIT image recognized, rootfs blob unpacking not implemented")
    if fw_info.tendaw is not None:
        return Failure(FailureKind.TENDAW_NO_JFFS2,
                       "TendaW container parsed but no mountable JFFS2 partition",
                       evidence={"partitions": len(fw_info.tendaw.partitions)})
    return Failure(FailureKind.NO_ROOTFS,
                   "no squashfs/UBI/TendaW structure found in image")


def _slice_squashfs(data: bytes, offset: int, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data[offset:])
    return dest


def _bind_mount(*paths: Path) -> Path:
    """Smallest directory that contains every path, used as the single docker mount.

    Docker accepts one bind source per volume, so both the squashfs slice and the
    destination must be reachable from it. The previous code mounted
    ``squashfs_path.parent`` and addressed the destination by *name only*, which
    silently re-rooted any caller-supplied ``--out`` outside the scratch tree into
    the scratch directory -- the command reported success while writing the tree
    somewhere the caller never asked for.

    Raises ValueError when the paths span volumes: no ancestor can contain both,
    and walking up to the volume root would never terminate.
    """
    resolved = [Path(p).resolve() for p in paths]
    common = resolved[0]
    for other in resolved[1:]:
        while not other.is_relative_to(common):
            if common.parent == common:
                raise ValueError(
                    f"cannot bind {resolved[0]} and {other} into a single docker "
                    "mount: they sit on different volumes, so no shared ancestor "
                    "exists. Keep --out on the same drive as the scratch directory."
                )
            common = common.parent
    return common


def _dir_has_entries(path: Path) -> bool:
    """True when path is a directory holding at least one entry."""
    if not safe_is_dir(path):
        return False
    try:
        return any(True for _ in Path(path).iterdir())
    except OSError:
        return False


def _reset_dest(dest_dir: Path, *, force: bool, explicit: bool) -> None:
    """Clear a previous extraction so the next one cannot merge into a remnant.

    ``explicit`` marks a caller-chosen ``--out`` path. The scratch tree holds only
    IRIS-derived directories, so clearing those unconditionally is fine, but a
    user-named directory may hold unrelated work and is only cleared on ``--force``.
    """
    if not safe_present(dest_dir):
        return
    if explicit and not force:
        if not safe_is_dir(dest_dir):
            raise ValueError(
                f"--out {dest_dir} already exists and is not a directory; "
                "remove it or choose another path"
            )
        if _dir_has_entries(dest_dir):
            raise ValueError(
                f"refusing to overwrite non-empty --out directory {dest_dir}: a "
                "caller-named path may hold unrelated work. Re-run with --force, "
                "or point --out at an empty path."
            )
    if not safe_rmtree(dest_dir):
        raise RuntimeError(
            f"cannot clear stale extraction {dest_dir}: reparse points or "
            "locked files survive the delete, so the new tree would merge into a "
            "partial tree"
        )


def _docker_unsquashfs(squashfs_path: Path, dest_dir: Path, image: str = "alpine:3.20") -> Path:
    """Unpack ``squashfs_path`` into ``dest_dir``.

    The caller owns clearing ``dest_dir`` (see ``_reset_dest``); this only fills it,
    so a stale tree can never silently merge into a new one.
    """
    mount_src = _bind_mount(squashfs_path.parent, dest_dir)
    rel_sqfs = squashfs_path.resolve().relative_to(mount_src).as_posix()
    rel_dest = dest_dir.resolve().relative_to(mount_src).as_posix()

    # unsquashfs will not create missing parents for -d, and the bind mount only
    # exposes what already exists on the host side.
    dest_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{mount_src.as_posix()}:/work",
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
            except (OSError, struct.error, IndexError):
                # OSError: unreadable file. struct.error/IndexError: a file that
                # passes the 4-byte magic check but ends mid-header (truncated
                # vendor blob) — census is a sample, one bad file must not sink it.
                pass
    return count, counter


def _tree_has_content(tree: Path) -> bool:
    """Whether a directory holds at least one visible entry, without raising.

    Two firmware traps meet here. ``Path.exists()``/``is_dir()`` raise
    ``WinError 1920`` on the POSIX symlinks a JFFS2 tree is dense with, while
    ``Path.rglob`` silently drops them — so a tree holding nothing *but* dead
    links looks empty. An explicit ``scandir`` walk sees those names without
    following them, which is the honest answer for "did anything land here?" and
    the one that makes a link-only tree get re-extracted instead of silently
    becoming an empty rootfs.
    """
    stack = [tree]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    return True
        except OSError:
            continue
    return False


def _link_target_within(src: Path, item: Path) -> tuple[Path, bool] | None:
    """Re-anchor a Linux symlink so it can never leave the extracted tree.

    Returns ``(target-as-seen-from-the-link, is_directory)``, or ``None`` when the
    link should not be recreated at all.

    Firmware links are POSIX by construction (``/sbin -> /bin``,
    ``/tmp -> /var/tmp``). Recreating such a link verbatim on the Windows host
    produces a reparse point whose target is an unresolvable ``/bin``: every
    later ``stat()`` on it raises ``WinError 1920`` instead of answering False,
    which took down rule verification mid-simulation. Worse, anything that reads
    the tree through an MSYS/POSIX layer resolves ``/bin`` against the *host* —
    a repair would then rename the user's own files.

    So an absolute link is re-anchored onto the tree (``/sbin -> /bin`` becomes
    ``sbin -> bin``): inside the chroot that is the same place, and on the host
    it stays inside the rootfs. Relative links are already self-contained, but
    ``../..`` can still climb out, so they are normalized and put through the
    same containment check. A link whose target is missing from the tree is not
    recreated at all — a dangling link that cannot be followed is worse than no
    link, which is the pre-existing behaviour this keeps.
    """
    text = str(os.readlink(item))
    anchor = item.parent.relative_to(src)
    # Treat "/bin", "\bin" and drive-lettered "C:\bin" alike: none is meaningful
    # inside a firmware root, all of them would escape it on the host.
    absolute = os.path.isabs(text) or re.match(r"^[A-Za-z]:", text)
    inner = text.lstrip("/\\") if absolute else text
    if not inner:
        return None

    # ``resolved`` is where the link really points, as a tree-root-relative path.
    resolved = Path(os.path.normpath(inner if absolute else anchor / inner))
    if resolved.is_absolute() or ".." in resolved.parts:
        return None
    if not safe_exists(src / resolved):
        return None
    # Windows stores the directory flag inside the reparse point, so getting it
    # wrong makes a file link unreadable as a file — the exact thing this copy
    # exists to preserve.
    return Path(os.path.relpath(resolved, anchor)), safe_is_dir(src / resolved)


def _copy_tree_tolerant(src: Path, dst: Path) -> None:
    """Copy a jefferson-extracted tree to NTFS, re-anchoring every symlink.

    JFFS2 partitions are mostly soft links; on Windows they land as broken
    reparse points that raise WinError 123 on read. Real re-linking happens
    container-side (see orchestrator); here we only need regular files for
    ELF census and an inspectable tree — but the links we do keep must point
    *inside* the tree (see ``_link_target_within``) so that rule evaluation and
    any shell tooling cannot act on the host filesystem.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        target = dst / rel
        try:
            if item.is_symlink():
                anchored = _link_target_within(src, item)
                if anchored is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    target.symlink_to(anchored[0], target_is_directory=anchored[1])
                except OSError:
                    pass
                continue
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
        except OSError:
            # One unreachable entry must not abort the whole extraction; the
            # census below samples what did make it across.
            continue


def _extract_tendaw(
    data: bytes,
    fw_info: FirmwareInfo,
    firmware_path: Path,
    scratch_dir: Path,
    result: RootfsExtraction,
    out_dir: Path | None = None,
    force: bool = False,
) -> RootfsExtraction:
    from iris.extract.tenda import jefferson_extract, slice_partition

    container = fw_info.tendaw
    stem = firmware_path.stem
    parts_dir = scratch_dir / f"{stem}-parts"
    rootfs_dir = out_dir if out_dir is not None else scratch_dir / f"{stem}-rootfs"
    _reset_dest(rootfs_dir, force=force, explicit=out_dir is not None)

    extracted: dict[str, Path] = {}
    for part in container.partitions:
        if part.payload_type != "jffs2" or not part.mount_point:
            continue
        tree = parts_dir / part.name
        if not _tree_has_content(tree):
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
    out_dir: Path | None = None,
    force: bool = False,
) -> RootfsExtraction:
    """Extract the rootfs tree of ``firmware_path``.

    ``out_dir`` relocates the extracted tree out of the scratch directory; the
    squashfs slice and the TendaW ``-parts`` cache stay in ``scratch_dir`` because
    they are regenerable intermediates rather than caller-facing results. Without
    it the tree lands in ``scratch_dir/<firmware-stem>-rootfs`` as before.
    """
    data = firmware_path.read_bytes()
    fw_info = analyze_firmware(data, arch_hint=arch_hint)
    result = RootfsExtraction(firmware_info=fw_info)

    if fw_info.tendaw is not None:
        return _extract_tendaw(
            data, fw_info, firmware_path, scratch_dir, result, out_dir=out_dir, force=force
        )

    if fw_info.rootfs_offset is None:
        result.failure = classify_failure(fw_info)
        return result

    stem = firmware_path.stem
    sqfs_path = scratch_dir / f"{stem}.squashfs"
    rootfs_dir = out_dir if out_dir is not None else scratch_dir / f"{stem}-rootfs"

    if fw_info.ubi_offset is not None:
        sqfs_data = extract_squashfs_from_ubi(data, fw_info.ubi_offset)
        if sqfs_data is None:
            result.failure = Failure(
                FailureKind.UBI_NO_SQUASHFS,
                "UBI container present but no squashfs volume extracted",
                evidence={"ubi_offset": fw_info.ubi_offset},
            )
            return result
        sqfs_path.parent.mkdir(parents=True, exist_ok=True)
        sqfs_path.write_bytes(sqfs_data)
        result.extraction_method = "ubi"
    else:
        _slice_squashfs(data, fw_info.rootfs_offset, sqfs_path)
        result.extraction_method = "squashfs"

    result.squashfs_path = sqfs_path

    _reset_dest(rootfs_dir, force=force, explicit=out_dir is not None)
    _docker_unsquashfs(sqfs_path, rootfs_dir, image=docker_image)
    result.rootfs_dir = rootfs_dir

    count, counter = _census_elfs(rootfs_dir)
    result.elf_count = count
    result.elf_archs = counter
    if counter:
        result.arch_verified = counter.most_common(1)[0][0]

    return result
