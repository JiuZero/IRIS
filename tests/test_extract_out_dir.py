"""Relocating an extracted rootfs with ``--out``.

The scratch tree is IRIS-owned, so clearing it unconditionally was fine. A
caller-named directory is not: it may hold unrelated work. These tests pin both
halves of that contract -- the path actually handed to docker, and the refusal to
overwrite something that is not a previous extraction.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from iris.extract import rootfs_extract as rx
from iris.extract.rootfs_extract import _bind_mount, _dir_has_entries, _reset_dest


class TestBindMount:
    def test_destination_inside_scratch_mounts_scratch_itself(self, tmp_path):
        scratch = tmp_path / "scratch"
        dest = scratch / "fw-rootfs"
        assert _bind_mount(scratch, dest) == scratch.resolve()

    def test_destination_outside_scratch_mounts_the_shared_ancestor(self, tmp_path):
        scratch = tmp_path / "work" / "scratch"
        dest = tmp_path / "elsewhere" / "nested" / "rootfs_out"

        mount = _bind_mount(scratch, dest)

        assert mount == tmp_path.resolve()
        assert dest.resolve().relative_to(mount).as_posix() == "elsewhere/nested/rootfs_out"

    def test_destination_path_is_not_truncated_to_its_basename(self, tmp_path):
        """The pre-``--out`` bug: only ``dest.name`` reached the container.

        ``unsquashfs -d /work/rootfs_out`` with the scratch tree mounted writes to
        ``<scratch>/rootfs_out`` no matter what the caller asked for, and the
        command still reports success.
        """
        scratch = tmp_path / "a" / "scratch"
        dest = tmp_path / "b" / "c" / "rootfs_out"
        mount = _bind_mount(scratch, dest)

        relative = dest.resolve().relative_to(mount).as_posix()

        assert relative != dest.name, "parent chain dropped, so the tree lands under scratch"
        assert relative == "b/c/rootfs_out"

    def test_volume_mismatch_is_rejected_instead_of_walking_to_the_root(self, tmp_path):
        """No ancestor spans two volumes, so the upward walk must not spin."""
        with pytest.raises(ValueError, match="different volumes"):
            _bind_mount(Path("D:/scratch"), Path("Z:/rootfs_out"))

    def test_volume_root_terminates_the_walk(self):
        with pytest.raises(ValueError, match="different volumes"):
            _bind_mount(Path("C:/a"), Path("D:/b"))


class TestDirHasEntries:
    def test_missing_path_has_no_entries(self, tmp_path):
        assert _dir_has_entries(tmp_path / "absent") is False

    def test_empty_directory_has_no_entries(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        assert _dir_has_entries(empty) is False

    def test_populated_directory_has_entries(self, tmp_path):
        populated = tmp_path / "populated"
        populated.mkdir()
        (populated / "bin").mkdir()
        assert _dir_has_entries(populated) is True

    def test_a_file_is_not_a_directory(self, tmp_path):
        target = tmp_path / "rootfs_out"
        target.write_text("not a tree", encoding="utf-8")
        assert _dir_has_entries(target) is False


class TestResetDest:
    def test_missing_destination_is_a_no_op(self, tmp_path):
        _reset_dest(tmp_path / "absent", force=False, explicit=True)

    def test_non_empty_caller_directory_needs_force(self, tmp_path):
        dest = tmp_path / "rootfs_out"
        dest.mkdir()
        (dest / "keep.txt").write_text("user work", encoding="utf-8")

        with pytest.raises(ValueError, match="refusing to overwrite non-empty"):
            _reset_dest(dest, force=False, explicit=True)

        assert (dest / "keep.txt").exists(), "refusal must not have deleted anything"

    def test_force_replaces_the_caller_directory(self, tmp_path):
        dest = tmp_path / "rootfs_out"
        dest.mkdir()
        (dest / "stale.bin").write_text("old", encoding="utf-8")

        _reset_dest(dest, force=True, explicit=True)

        assert not dest.exists()

    def test_empty_caller_directory_is_reusable_without_force(self, tmp_path):
        dest = tmp_path / "rootfs_out"
        dest.mkdir()

        _reset_dest(dest, force=False, explicit=True)

        assert not dest.exists()

    def test_caller_path_that_is_a_file_is_rejected(self, tmp_path):
        dest = tmp_path / "rootfs_out"
        dest.write_text("not a directory", encoding="utf-8")

        with pytest.raises(ValueError, match="not a directory"):
            _reset_dest(dest, force=False, explicit=True)

    def test_scratch_derived_directory_is_cleared_without_force(self, tmp_path):
        """Backward compatibility: the default location stays IRIS-owned."""
        scratch = tmp_path / "scratch" / "fw-rootfs"
        scratch.mkdir(parents=True)
        (scratch / "leftover").write_text("previous run", encoding="utf-8")

        _reset_dest(scratch, force=False, explicit=False)

        assert not scratch.exists()


class TestDockerCommandAddressing:
    """Pin the real command string, since the truncation bug was invisible there."""

    @staticmethod
    def _run_with_fake_docker(monkeypatch, sqfs: Path, dest: Path) -> list[str]:
        captured: list[str] = {}

        def fake_run(cmd, **_kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "OK\n", "")

        monkeypatch.setattr(rx.subprocess, "run", fake_run)
        rx._docker_unsquashfs(sqfs, dest)
        return captured["cmd"]

    def test_command_writes_below_the_requested_destination(self, tmp_path, monkeypatch):
        scratch = tmp_path / "iris-home" / "scratch"
        scratch.mkdir(parents=True)
        sqfs = scratch / "fw.squashfs"
        sqfs.write_bytes(b"hsqs")
        dest = tmp_path / "project" / "rootfs_out"

        cmd = self._run_with_fake_docker(monkeypatch, sqfs, dest)

        volume = next(arg for arg in cmd if arg.endswith(":/work"))
        script = cmd[-1]
        assert volume == f"{tmp_path.resolve().as_posix()}:/work"
        assert "-d /work/project/rootfs_out" in script
        assert "-d /work/rootfs_out" not in script

    def test_missing_destination_parents_are_created_on_the_host(self, tmp_path, monkeypatch):
        """unsquashfs will not create parents for -d, and the bind mount hides
        anything that does not already exist on the host."""
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        sqfs = scratch / "fw.squashfs"
        sqfs.write_bytes(b"hsqs")
        dest = tmp_path / "a" / "b" / "c" / "rootfs_out"

        self._run_with_fake_docker(monkeypatch, sqfs, dest)

        assert dest.is_dir()

    def test_squashfs_slice_is_addressed_from_the_same_mount(self, tmp_path, monkeypatch):
        scratch = tmp_path / "iris-home" / "scratch"
        scratch.mkdir(parents=True)
        sqfs = scratch / "fw.squashfs"
        sqfs.write_bytes(b"hsqs")
        dest = tmp_path / "project" / "rootfs_out"

        cmd = self._run_with_fake_docker(monkeypatch, sqfs, dest)

        assert "/work/iris-home/scratch/fw.squashfs" in cmd[-1]