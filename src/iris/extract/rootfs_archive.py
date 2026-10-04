"""Unpacking an uploaded rootfs archive (L1).

The workbench lets a user start an emulation from an archive they already have on
disk, because "my rootfs is already extracted, why do I have to find its squashfs
inside a firmware image again" is a real reason to stop using the product. That
means unpacking a file the server did not create, which is a different job from
:mod:`iris.extract.rootfs_extract`: that module slices bytes out of an image and
hands the tree to a container, while this one writes attacker-shaped member names
straight onto the host filesystem.

So every member is checked before it becomes a path:

* **No traversal.** An absolute name, a ``..`` component, or a Windows drive
  letter is refused -- the resolved destination is required to sit under the
  extraction root, which is the same containment check :func:`iris.api.web_app.safe_join`
  applies to the SPA's catch-all for the same reason.
* **No links out of the tree.** A firmware rootfs is mostly links (``/sbin ->
  /bin``, every busybox applet), so links cannot be dropped -- but they are
  *re-anchored* onto the tree instead of being written verbatim, the same rule
  :func:`iris.extract.rootfs_extract._link_target_within` applies to a jefferson
  tree. A link that cannot be re-anchored is skipped and counted, never created.
* **No special files.** Device nodes, FIFOs and sockets are refused: nothing in the
  emulation path needs one, and ``mknod`` on a host is not a capability this
  endpoint should hand out.
* **Bounded size.** Both the member count and the total unpacked byte count are
  capped. The upload cap already bounds the compressed body; without a second cap a
  few hundred kilobytes of gzip expands until the disk is full, which is the same
  class of problem as the unbounded ``firmware.read()`` that
  ``/api/v1/pipeline`` used to do.

**What this cannot do on Windows.** Creating a symlink needs Developer Mode or an
elevated shell, so on a stock Windows host most of a rootfs's links are skipped
rather than created. That is reported (``links_skipped``, plus a note) instead of
being hidden, because the consequence -- a guest whose ``/sbin`` is empty and whose
init cannot be found -- would otherwise arrive as an unexplained boot failure much
later. Container-side tree building (see
:func:`iris.emulate.orchestrator._compose_rootfs_from_slices`) is the path that
relinks properly on NTFS.
"""

from __future__ import annotations

import contextlib
import os
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from iris.extract.rootfs import UNIX_THRESHOLD, find_rootfs
from iris.failures import Failure, FailureKind
from iris.fsutil import safe_is_dir, safe_present, safe_rmtree

__all__ = [
    "MAX_ARCHIVE_MEMBERS",
    "MAX_UNPACKED_BYTES",
    "ArchiveExtraction",
    "is_rootfs_archive",
    "unpack_rootfs_archive",
]

#: Member count cap. A real rootfs is a few tens of thousands of files; anything
#: an order of magnitude past that is not a rootfs, and the per-member stat calls
#: it forces are the expensive part of unpacking.
MAX_ARCHIVE_MEMBERS = 200_000

#: Total unpacked byte cap. Compressed firmware rootfs are typically well under a
#: gigabyte unpacked, and this leaves headroom for a large one while still bounding
#: a decompression bomb to a number a person can recognise as "too much".
MAX_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024

_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


def human_bytes(count: int) -> str:
    """A byte count a person can read off a limit they typed themselves.

    Integer-dividing by MiB would print "0 MiB" for any cap under a megabyte, which
    is exactly the range where someone is testing the limit and cannot tell whether
    the message means "under one MiB" or "no limit at all". The trailing ".0" is
    dropped for a whole number so "64 MiB" reads as what somebody typed.
    """
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            if value >= 100 or value.is_integer():
                return f"{value:.0f} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


@dataclass
class ArchiveExtraction:
    """What an unpack produced, or why it produced nothing.

    ``failure`` is a :class:`~iris.failures.Failure` rather than a string for the
    same reason :class:`iris.extract.rootfs_extract.RootfsExtraction` uses one: the
    kind is what gets counted, and a prose-only field makes the taxonomy
    uncountable.
    """

    rootfs_dir: Path | None = None
    #: Directory prefix inside the archive that holds the root, "" when it is the
    #: archive root. Stripped on the way out so the destination always *is* the
    #: rootfs rather than a directory inside one.
    prefix: str = ""
    members: int = 0
    total_bytes: int = 0
    links_created: int = 0
    #: Links present in the archive that were not recreated -- either refused for
    #: escaping the tree or refused because the host would not create one.
    links_skipped: int = 0
    #: How many members were refused. Separate from ``rejected`` because the sample
    #: is capped: reading the count off the length of the sample would report "20"
    #: for an archive that refused ten thousand.
    rejected_count: int = 0
    #: A bounded sample of refused member names, for diagnosis.
    rejected: tuple[str, ...] = ()
    failure: Failure | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def failure_reason(self) -> str:
        return self.failure.message if self.failure else ""


def is_rootfs_archive(path: Path) -> bool:
    """Whether ``path`` is a tar archive this module can open.

    The check is tarfile's own magic-number probe rather than a filename suffix:
    the endpoint has to tell an uploaded rootfs archive from an uploaded firmware
    image, and a firmware image that happens to be called ``rootfs.tar.gz`` is
    still a firmware image.
    """
    try:
        return tarfile.is_tarfile(path)
    except OSError:
        return False


def _member_relpath(name: str, prefix: str) -> str | None:
    """``name`` relative to the rootfs prefix, or None if it is not inside it.

    Both the traversal refusal and the prefix strip happen here, on the *name*,
    before anything touches the filesystem. ``None`` covers "outside the rootfs"
    and "unsafe" together because both end in the same place: the member is not
    extracted.

    An absolute name is *refused*, not reinterpreted as relative the way
    ``tarfile`` does. Stripping the leading slash would land ``/etc/passwd`` at
    ``<dest>/etc/passwd``, which happens to be inert -- but the same rule would
    quietly rewrite a hostile name into a plausible-looking one and leave the
    refusal count reporting zero members rejected.
    """
    cleaned = name.replace("\\", "/")
    if cleaned.startswith("/") or bool(_DRIVE_LETTER.match(cleaned)):
        return None
    if not cleaned or cleaned == ".":
        return None
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    if prefix:
        if parts == prefix.split("/"):
            return ""
        head = "/".join(parts[: len(prefix.split("/"))])
        if head != prefix:
            return None
        parts = parts[len(prefix.split("/")):]
    if not parts:
        return ""
    return "/".join(parts)


def _contained(dest: Path, relpath: str) -> Path:
    """Join ``relpath`` under ``dest``, refusing anything that leaves it.

    The name checks above already refuse traversal on the *string*; this is the
    second half, on the resolved path, because a name that survives them can still
    resolve outside -- through an existing reparse point in the destination, which
    a firmware-shaped tree can plausibly contain.
    """
    candidate = (dest / relpath).resolve()
    if not candidate.is_relative_to(dest.resolve()):
        raise ValueError(f"member escapes the extraction root: {relpath}")
    return candidate


def _link_within(dest: Path, relpath: str, linkname: str) -> str | None:
    """Re-anchor a link so it points inside the tree, or None to skip it.

    A relative link is kept when its resolved target stays under the root; an
    absolute one (``/sbin -> /bin``) is re-expressed relative to the link's own
    directory, which inside the emulation container is the same path and on the
    host is inside the tree rather than at ``C:\\bin``. A link whose target is
    missing is skipped rather than created dangling: an unfollowable link makes
    every later ``stat()`` raise on Windows, which is what
    :mod:`iris.fsutil` exists to work around.
    """
    text = linkname.replace("\\", "/")
    if not text or text == ".":
        return None
    absolute = text.startswith("/") or bool(_DRIVE_LETTER.match(text))
    inner = text.lstrip("/") if absolute else text
    if not inner:
        return None

    anchor = os.path.dirname(relpath)
    parts = [p for p in anchor.split("/") if p] if anchor else []
    stack = list(parts)
    for segment in inner.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if not stack:
                return None
            stack.pop()
            continue
        stack.append(segment)
    resolved = "/".join(stack)
    if not resolved:
        return None
    # Rewrite as a path relative to the link's own directory.
    common = 0
    mine = anchor.split("/") if anchor else []
    for a, b in zip(mine, resolved.split("/")):
        if a != b:
            break
        common += 1
    ups = [".."] * (len(mine) - common)
    down = resolved.split("/")[common:]
    anchored = "/".join(ups + down)
    if not anchored:
        return None
    if anchored.startswith("../"):
        return None
    return anchored.replace("/", os.sep)


def unpack_rootfs_archive(
    archive: Path,
    dest_dir: Path,
    *,
    max_members: int = MAX_ARCHIVE_MEMBERS,
    max_bytes: int = MAX_UNPACKED_BYTES,
) -> ArchiveExtraction:
    """Extract the rootfs inside ``archive`` into ``dest_dir``.

    ``dest_dir`` is replaced, not merged: it holds only IRIS-derived trees, so a
    stale remnant from a previous upload would otherwise produce a tree that is
    the *union* of two different uploads -- a rootfs no longer matches any firmware
    and the failure is invisible from the outside.
    """
    result = ArchiveExtraction()
    # ExitStack rather than a plain `with`: opening has to be inside the `try` so a
    # non-tar file reports a failure instead of raising out of the function, and a
    # context manager has to exist before the first `try` can hand it one.
    with contextlib.ExitStack() as stack:
        try:
            tf = stack.enter_context(tarfile.open(archive, "r:*"))
        except (tarfile.TarError, OSError) as exc:
            result.failure = Failure(FailureKind.NO_ROOTFS,
                                     f"not a readable tar archive: {exc}")
            return result

        try:
            names = tf.getnames()
        except (tarfile.TarError, OSError) as exc:
            result.failure = Failure(FailureKind.NO_ROOTFS,
                                     f"archive index is unreadable: {exc}")
            return result

        if len(names) > max_members:
            result.failure = Failure(
                FailureKind.NO_ROOTFS,
                f"archive holds {len(names)} members, over the {max_members} limit",
            )
            return result

        candidate = find_rootfs(names)
        if candidate is None or not candidate.is_rootfs:
            result.failure = Failure(
                FailureKind.NO_ROOTFS,
                "no rootfs directory structure in the archive: expected at least "
                f"{UNIX_THRESHOLD} of bin/etc/lib/sbin/usr/var under one prefix",
            )
            return result

        prefix = candidate.prefix
        result.prefix = prefix
        result.notes.append(
            f"rootfs prefix {prefix or '<archive root>'} "
            f"({candidate.unix_hits} unix dirs, score {candidate.score})"
        )

        if safe_present(dest_dir) and not safe_rmtree(dest_dir):
            result.failure = Failure(
                FailureKind.NO_ROOTFS,
                f"cannot clear the previous tree at {dest_dir}: reparse points or "
                "locked files survive the delete, so the new tree would merge into it",
            )
            return result
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            result.failure = Failure(FailureKind.NO_ROOTFS,
                                     f"cannot create {dest_dir}: {exc}")
            return result

        _extract_members(tf, dest_dir, prefix, result, max_bytes)

    # A byte-cap trip lands here mid-walk with a half-written tree on disk. Report
    # it as a failure rather than as a successful unpack of whatever fit: the tree
    # is a prefix of the real rootfs and would boot into a missing-init failure
    # that says nothing about the archive being too large.
    if result.failure is not None:
        result.rootfs_dir = None
        return result

    if result.rejected_count:
        result.notes.append(
            f"{result.rejected_count} member(s) refused: unsafe path, link leaving "
            f"the tree, or a special file ({', '.join(result.rejected[:3])})"
        )
    if result.links_skipped:
        result.notes.append(
            f"{result.links_skipped} symlink(s) not recreated: creating one on this "
            "host needs Developer Mode or an elevated shell. A guest whose /sbin or "
            "applet links are missing will not boot -- the emulation result is the "
            "authority on whether that happened"
        )

    if result.members == 0:
        result.failure = Failure(FailureKind.NO_ROOTFS,
                                 "the rootfs prefix held no extractable file")
        result.rootfs_dir = None
        return result
    if not safe_is_dir(dest_dir):
        result.failure = Failure(FailureKind.NO_ROOTFS,
                                 "the extracted tree is not a readable directory")
        result.rootfs_dir = None
        return result

    result.rootfs_dir = dest_dir
    return result



def _extract_members(
    tf: tarfile.TarFile,
    dest_dir: Path,
    prefix: str,
    result: ArchiveExtraction,
    max_bytes: int,
) -> None:
    """Walk the archive once, writing safe members and accounting for the rest."""
    # Which members are directories, so a symlink can be created with the right
    # ``target_is_directory`` flag: Windows stores it inside the reparse point, and
    # a file link recorded as a directory link is unreadable as a file.
    dir_names: set[str] = set()
    for member in tf:
        if not member.isdir():
            continue
        rel = _member_relpath(member.name, prefix)
        if rel:
            dir_names.add(rel.replace("/", os.sep))

    for member in tf:
        rel = _member_relpath(member.name, prefix)
        if rel is None:
            if not member.isdir() and member.name not in (".", "./"):
                _reject(result, member.name)
            continue
        if member.isdir():
            try:
                _contained(dest_dir, rel).mkdir(parents=True, exist_ok=True)
            except (OSError, ValueError):
                _reject(result, member.name)
            continue
        if member.issym() or member.islnk():
            _extract_link(tf, member, dest_dir, rel, dir_names, result)
            continue
        if not member.isfile():
            _reject(result, member.name)
            continue
        if result.total_bytes + member.size > max_bytes:
            result.failure = Failure(
                FailureKind.NO_ROOTFS,
                f"archive unpacks past the {human_bytes(max_bytes)} limit "
                f"(already at {human_bytes(result.total_bytes)})",
            )
            return
        _extract_file(tf, member, dest_dir, rel, result)


def _extract_file(
    tf: tarfile.TarFile,
    member: tarfile.TarInfo,
    dest_dir: Path,
    rel: str,
    result: ArchiveExtraction,
) -> None:
    try:
        target = _contained(dest_dir, rel)
    except ValueError:
        _reject(result, member.name)
        return
    try:
        handle = tf.extractfile(member)
    except (tarfile.TarError, OSError):
        _reject(result, member.name)
        return
    if handle is None:
        _reject(result, member.name)
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as out:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        os.chmod(target, member.mode & 0o777)
    except OSError:
        _reject(result, member.name)
        return
    finally:
        handle.close()
    result.members += 1
    result.total_bytes += member.size


def _extract_link(
    tf: tarfile.TarFile,
    member: tarfile.TarInfo,
    dest_dir: Path,
    rel: str,
    dir_names: set[str],
    result: ArchiveExtraction,
) -> None:
    anchored = _link_within(dest_dir, rel, member.linkname)
    if anchored is None:
        result.links_skipped += 1
        return
    try:
        target = _contained(dest_dir, rel)
    except ValueError:
        result.links_skipped += 1
        return
    if not safe_is_dir(dest_dir / anchored):
        # Creating a link whose target is missing makes every later stat() raise
        # WinError 1920 instead of answering; skip it and say so.
        result.links_skipped += 1
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            target.unlink()
        os.symlink(anchored, target, target_is_directory=anchored in dir_names)
    except OSError:
        result.links_skipped += 1
        return
    result.links_created += 1


#: How many refused member names to keep verbatim before truncating.
REJECTED_SAMPLE = 20


def _reject(result: ArchiveExtraction, name: str) -> None:
    """Record a refused member, counting all of them but naming only a sample.

    The count is what a reader needs ("12 members refused"); the exhaustive list of
    a hostile archive is unbounded input echoed back into a log line. ``truncated``
    marks that the sample is a subset, so the note never reads as a full list.
    """
    result.rejected_count += 1
    if len(result.rejected) < REJECTED_SAMPLE:
        result.rejected = result.rejected + (name,)
    else:
        result.rejected = result.rejected[: REJECTED_SAMPLE - 1] + (
            f"... and {result.rejected_count - REJECTED_SAMPLE + 1} more",
        )