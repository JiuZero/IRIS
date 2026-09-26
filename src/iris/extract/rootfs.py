"""Root filesystem detection (L1).

Replicates FirmAE's heuristic (sources/extractor/extractor.py): a rootfs is a
directory prefix whose direct children contain >= UNIX_THRESHOLD standard UNIX
directories. Extended with two extra signals (busybox presence, /etc/init.d)
reported for scoring, and an O(n) prefix-depth scan (depth 0..2).
"""

import tarfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

UNIX_DIRS = frozenset(
    {"bin", "etc", "dev", "home", "lib", "mnt", "opt", "proc", "root", "sbin", "sys", "tmp", "usr", "var"}
)
UNIX_THRESHOLD = 4
MAX_PREFIX_DEPTH = 2


@dataclass(frozen=True)
class RootfsCandidate:
    prefix: str
    unix_hits: int
    has_busybox: bool
    has_initd: bool
    total_entries: int

    @property
    def score(self) -> int:
        return self.unix_hits + (2 if self.has_busybox else 0) + (1 if self.has_initd else 0)

    @property
    def is_rootfs(self) -> bool:
        return self.unix_hits >= UNIX_THRESHOLD


def _normalize(name: str) -> str:
    name = name.lstrip("./")
    return name.rstrip("/")


def find_rootfs(paths: Iterable[str]) -> Optional[RootfsCandidate]:
    """Given relative entry names (tar members or directory walk), find the rootfs prefix."""
    hits_by_depth: list[Counter] = [Counter() for _ in range(MAX_PREFIX_DEPTH + 1)]
    total = 0
    for raw in paths:
        p = _normalize(raw)
        if not p:
            continue
        total += 1
        parts = p.split("/")
        for depth in range(MAX_PREFIX_DEPTH + 1):
            if len(parts) > depth and parts[depth] in UNIX_DIRS:
                prefix = "/".join(parts[:depth])
                hits_by_depth[depth][prefix] += 1

    best_prefix, best_hits, best_depth = "", 0, 0
    for depth in range(MAX_PREFIX_DEPTH + 1):
        for prefix, hits in hits_by_depth[depth].items():
            if hits > best_hits:
                best_prefix, best_hits, best_depth = prefix, hits, depth

    if best_hits == 0:
        return None

    members = [p for p in paths if _normalize(p).startswith(best_prefix)]
    marker_len = len(best_prefix) + 1 if best_prefix else 0
    has_busybox = any(
        _normalize(m)[marker_len:] in ("bin/busybox", "sbin/busybox") for m in members
    )
    has_initd = any(_normalize(m)[marker_len:].startswith("etc/init.d") for m in members)
    return RootfsCandidate(
        prefix=best_prefix,
        unix_hits=best_hits,
        has_busybox=has_busybox,
        has_initd=has_initd,
        total_entries=total,
    )


def find_rootfs_in_archive(archive: Path) -> Optional[RootfsCandidate]:
    with tarfile.open(archive, "r:*") as tf:
        return find_rootfs(tf.getnames())