"""Unit tests for the rootfs tarball cache (no Docker required).

The cache exists to skip a repack of an unchanged tree. Getting that decision
wrong is silent in the worst way: a pack from an earlier session keeps being
served while the freshly applied L3 rules never reach the guest, so the crash the
rules were written to prevent still happens and nothing says why.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from iris.emulate import orchestrator
from iris.emulate.orchestrator import _newest_mtime, _tarball_is_stale
from iris.failures import FailureKind


@pytest.fixture()
def tree_and_pack(tmp_path: Path) -> tuple[Path, Path]:
    rootfs = tmp_path / "fw-rootfs"
    (rootfs / "etc" / "init.d").mkdir(parents=True)
    (rootfs / "etc" / "init.d" / "rcS").write_text("#!/bin/sh\n", encoding="utf-8")
    (rootfs / "bin").mkdir()
    (rootfs / "bin" / "diag").write_bytes(b"\x7fELF" + b"\x00" * 60)
    pack = tmp_path / "emulate-1" / "1.tar.gz"
    pack.parent.mkdir(parents=True)
    pack.write_bytes(b"not really gzip")
    # "packed, then nothing touched the tree": the whole tree predates the pack.
    for path in sorted(rootfs.rglob("*")) + [rootfs]:
        _age(path, seconds=30)
    _age(pack, seconds=0)
    return rootfs, pack


def _age(path: Path, seconds: float) -> None:
    """Backdate a file so staleness comparisons are not decided by write order."""
    when = time.time() - seconds
    os.utime(path, (when, when))


class TestNewestMtime:
    def test_empty_tree(self, tmp_path):
        assert _newest_mtime(tmp_path / "nothing-here") == 0.0

    def test_reports_the_latest_file(self, tmp_path):
        (tmp_path / "old").write_text("a", encoding="utf-8")
        _age(tmp_path / "old", seconds=120)
        (tmp_path / "new").write_text("b", encoding="utf-8")
        assert _newest_mtime(tmp_path) >= (tmp_path / "new").stat().st_mtime

    def test_unstatable_entries_are_skipped_not_raised(self, tmp_path, monkeypatch):
        (tmp_path / "ok").write_text("a", encoding="utf-8")
        real_stat = os.stat

        def refuse(path, *args, **kwargs):
            if str(path).endswith("sbin"):
                raise OSError(1920, "The system cannot access this file")
            return real_stat(path, *args, **kwargs)

        monkeypatch.setattr(os, "stat", refuse)
        assert _newest_mtime(tmp_path) > 0.0


class TestTarballStaleness:
    def test_missing_pack_is_stale(self, tmp_path, tree_and_pack):
        rootfs, pack = tree_and_pack
        pack.unlink()
        assert _tarball_is_stale(rootfs, pack) is True

    def test_missing_rootfs_is_stale(self, tmp_path, tree_and_pack):
        rootfs, pack = tree_and_pack
        import shutil

        shutil.rmtree(rootfs)
        assert _tarball_is_stale(rootfs, pack) is True

    def test_unchanged_tree_reuses_the_pack(self, tree_and_pack):
        rootfs, pack = tree_and_pack
        assert _tarball_is_stale(rootfs, pack) is False

    def test_rule_edit_after_the_pack_forces_a_repack(self, tree_and_pack):
        """The exact regression: a rule renames ``bin/diag`` and the guest must see it."""
        rootfs, pack = tree_and_pack
        (rootfs / "firmadyne").mkdir()
        (rootfs / "firmadyne" / "iris_rules.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        assert _tarball_is_stale(rootfs, pack) is True

    def test_reextraction_after_the_pack_forces_a_repack(self, tree_and_pack):
        rootfs, pack = tree_and_pack
        (rootfs / "bin" / "busybox").write_bytes(b"\x7fELF" + b"\x00" * 60)
        assert _tarball_is_stale(rootfs, pack) is True

    def test_pack_touched_after_the_tree_reuses_it(self, tree_and_pack):
        rootfs, pack = tree_and_pack
        os.utime(pack, None)
        assert _tarball_is_stale(rootfs, pack) is False

    def test_unresolvable_pack_counts_as_stale(self, tree_and_pack, monkeypatch):
        rootfs, pack = tree_and_pack
        real_stat = Path.stat

        def refuse(self, *args, **kwargs):
            if self.name == pack.name:
                raise OSError(1920, "no access")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", refuse)
        # Occupies its name but cannot be stat'ed: its age is unknowable, so the
        # pack cannot be shown to be current and must be rebuilt.
        assert _tarball_is_stale(rootfs, pack) is True


class TestEmulateRebuildsStalePack:
    """The decision has to reach ``emulate_firmware``, not just the helper."""

    @pytest.fixture()
    def stop_after_pack(self, monkeypatch):
        """Run only as far as the tarball, then bail out with a recognizable error."""
        monkeypatch.setattr(orchestrator, "get_config", lambda arch: object())

        def build(_src, dst):
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(b"fresh")
            raise RuntimeError("SENTINEL: pack rebuilt")

        monkeypatch.setattr(orchestrator, "_create_tarball", build)
        return build

    def _run_emulation(self, rootfs: Path, tmp_path: Path):
        return orchestrator.emulate_firmware(rootfs, "mipsel", 1, tmp_path / "scratch", record=False)

    def test_stale_pack_is_recreated(self, tree_and_pack, stop_after_pack, tmp_path):
        """The exact regression: a rule renames ``bin/diag`` and the guest must see it."""
        rootfs, pack = tree_and_pack
        (rootfs / "firmadyne").mkdir()
        (rootfs / "firmadyne" / "iris_rules.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        scratch = pack.parent.parent

        result = orchestrator.emulate_firmware(rootfs, "mipsel", 1, scratch, record=False)
        assert result.error == "tarball-failed: SENTINEL: pack rebuilt"
        assert result.failure.kind is FailureKind.TARBALL_FAILED
        assert (scratch / "emulate-1" / "1.tar.gz").read_bytes() == b"fresh"

    def test_fresh_pack_is_reused(self, tree_and_pack, stop_after_pack, monkeypatch):
        rootfs, pack = tree_and_pack
        scratch = pack.parent.parent

        def explode(*_a, **_k):
            raise AssertionError("repacked an unchanged tree")

        monkeypatch.setattr(orchestrator, "_create_tarball", explode)
        # Nothing downstream is mocked on purpose: repacking would raise here.
        result = orchestrator.emulate_firmware(rootfs, "mipsel", 1, scratch, record=False)
        assert "repacked an unchanged tree" not in (result.error or "")