"""Filesystem queries that answer instead of raising.

A firmware rootfs is POSIX by construction, so it is full of symlinks
(``/sbin -> /bin``, ``/tmp -> /var/tmp``, ``bin/ash -> busybox``). Recreated on a
Windows host they become reparse points whose targets are unresolvable POSIX
paths, and every query on them raises instead of answering:

    >>> Path("rootfs/sbin").exists()      # doctest: +SKIP
    OSError: [WinError 1920] 系统无法访问此文件。

``Path.exists()`` only swallows a short list of errnos (ENOENT, ENOTDIR, EBADF,
EINVAL, EACCES-ish cases); ``WinError 1920``/errno 22 is not among them. The
difference matters because these links are *normal* in a firmware image, not a
corruption: an unreadable one means "cannot inspect", and for rule evaluation,
catalogue listing or a health verdict that is the same answer as "absent".
Raising instead lets a single dead link abort a simulation that is otherwise
fine — which is exactly what happened in ``iris emulate run``.

``safe_rmtree`` covers the sibling hazard: wiping a cached extraction whose tree
still holds such reparse points. A partial delete that leaves the directory in
place makes every later run fail on the stale remnant, so a failed wipe is
reported rather than silently half-done.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path


def safe_exists(path: Path) -> bool:
    """True only when the path is both present and inspectable."""
    try:
        return path.exists()
    except OSError:
        return False


def safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def safe_present(path: Path) -> bool:
    """True when the path exists *or* still occupies its name.

    ``exists()`` is False for an unresolvable reparse point that nonetheless still
    occupies its name, so a stale-remnant check built on ``exists()`` alone reports
    "nothing there" and lets the next step write into it.
    """
    path = Path(path)
    return safe_exists(path) or _entry_visible(path)


def safe_stat_size(path: Path) -> int:
    """Size in bytes, or 0 when the entry cannot be stat'ed."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def safe_read_text(path: Path) -> str:
    """Read UTF-8 text, returning "" for anything unreadable or binary-ish."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def safe_rmtree(path: Path) -> bool:
    """Remove a tree that may contain unreachable reparse points.

    ``shutil.rmtree`` classifies an entry it cannot ``stat()`` as a non-directory
    and unlinks it, which is right here; it still raises on permission problems
    and on the occasional junction that refuses to be removed. Returns True when
    the path is gone afterwards, so callers can fail loudly instead of reusing a
    half-deleted extraction cache.
    """
    path = Path(path)
    if not safe_present(path):
        return True
    # ``onerror`` is the 3.11 spelling and was removed in 3.14; ``onexc`` is the
    # 3.12+ spelling. The project supports ">=3.11", so pick per interpreter rather
    # than crash with a TypeError on the failure path we are trying to survive.
    kwarg = "onexc" if sys.version_info >= (3, 12) else "onerror"
    try:
        shutil.rmtree(path, **{kwarg: _force_remove})
    except OSError:
        return not safe_present(path)
    return not safe_present(path)


def _force_remove(func, path, exc_info) -> None:
    """``rmtree`` error handler: clear read-only bits and retry, else give up.

    A vendor image that ships mode-0400 files is ordinary, not broken; without the
    retry the wipe fails on a read-only entry and every later run inherits the
    remnant. The original error is re-raised when the retry does not work, so a
    genuine permission problem still surfaces as one.
    """
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        raise exc_info[1]


def _entry_visible(path: Path) -> bool:
    """True when the path shows up in its parent, even if unusable itself.

    ``exists()`` is False for an unresolvable reparse point that still occupies
    its name; the directory listing is what proves the remnant survived.
    """
    try:
        parent = path.parent
        if not parent.is_dir():
            return False
        return any(entry.name == path.name for entry in parent.iterdir())
    except OSError:
        return False