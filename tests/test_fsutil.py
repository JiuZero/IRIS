"""Tests for iris.fsutil and the extraction hardening built on it.

The hazard under test is a firmware rootfs that is POSIX by construction and
Windows by accident: ``/sbin -> /bin`` recreated on an NTFS host becomes a
reparse point whose target is unresolvable, and every ``stat()`` on it raises
``OSError: [WinError 1920]`` instead of answering. The error cannot be produced
on demand on every platform, so it is injected: the behaviour under test is
"a query answers instead of raising", not any particular errno.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from iris.extract.rootfs_extract import (
    _copy_tree_tolerant,
    _link_target_within,
    _tree_has_content,
)
from iris.fsutil import (
    safe_exists,
    safe_is_dir,
    safe_is_file,
    safe_present,
    safe_read_text,
    safe_rmtree,
    safe_stat_size,
)


def _unresolvable(path: Path, calls: list[Path] | None = None) -> bool:
    """Stand in for a reparse point the host cannot resolve.

    Mirrors ``Path.exists``/``is_file``/``stat`` on a dead ``/sbin -> /bin``:
    raises ``OSError`` rather than returning False, while ``is_symlink`` stays
    quiet — which is why the caller has to guard every query, not just one.
    """
    if calls is not None:
        calls.append(path)
    raise OSError(1920, "The system cannot access this file")


def _link(path: Path, target: str, is_dir: bool = True) -> None:
    """Create a symlink, skipping the test where the host forbids one.

    Windows needs a privilege (or developer mode) for ``symlink_to``; without it
    the anchoring tests would fail for a reason that has nothing to do with the
    code under test.
    """
    try:
        path.symlink_to(target, target_is_directory=is_dir)
    except OSError:
        pytest.skip("symlink creation not permitted on this host")


class TestAnswersInsteadOfRaising:
    def test_exists_is_false_for_unresolvable_entry(self, tmp_path, monkeypatch):
        ghost = tmp_path / "sbin"
        monkeypatch.setattr(Path, "exists", lambda self: _unresolvable(self))
        assert safe_exists(ghost) is False

    def test_is_file_and_is_dir_agree(self, tmp_path, monkeypatch):
        ghost = tmp_path / "tmp"
        monkeypatch.setattr(Path, "is_file", lambda self: _unresolvable(self))
        monkeypatch.setattr(Path, "is_dir", lambda self: _unresolvable(self))
        assert safe_is_file(ghost) is False
        assert safe_is_dir(ghost) is False

    def test_stat_size_falls_back_to_zero(self, tmp_path, monkeypatch):
        ghost = tmp_path / "config"
        monkeypatch.setattr(Path, "stat", lambda self: _unresolvable(self))
        assert safe_stat_size(ghost) == 0

    def test_read_text_falls_back_to_empty(self, tmp_path, monkeypatch):
        ghost = tmp_path / "etc" / "passwd"
        monkeypatch.setattr(Path, "read_text", lambda self, **kw: _unresolvable(self))
        assert safe_read_text(ghost) == ""

    def test_real_entries_still_answer_truthfully(self, tmp_path):
        f = tmp_path / "busybox"
        f.write_bytes(b"\x7fELF")
        (tmp_path / "bin").mkdir()
        assert safe_exists(f) and safe_is_file(f) and safe_stat_size(f) == 4
        assert safe_is_dir(tmp_path / "bin")
        assert not safe_exists(tmp_path / "nope")

    def test_undecodable_bytes_come_back_replaced_not_lost(self, tmp_path):
        f = tmp_path / "binary"
        f.write_bytes(b"\x7fELF\x00\xff")
        assert safe_read_text(f) == "\x7fELF\x00\ufffd"


class TestSafePresent:
    def test_visible_reparse_point_counts_as_present(self, tmp_path, monkeypatch):
        (tmp_path / "sbin").mkdir()
        monkeypatch.setattr(Path, "exists", lambda self: _unresolvable(self))
        assert safe_present(tmp_path / "sbin") is True

    def test_absent_entry_is_not_present(self, tmp_path):
        assert safe_present(tmp_path / "never-created") is False

    def test_missing_parent_is_not_present(self, tmp_path):
        assert safe_present(tmp_path / "no" / "such" / "place") is False


class TestSafeRmtree:
    def test_removes_normal_tree(self, tmp_path):
        tree = tmp_path / "rootfs"
        (tree / "bin").mkdir(parents=True)
        (tree / "bin" / "sh").write_text("#!/bin/sh\n", encoding="utf-8")
        assert safe_rmtree(tree) is True
        assert not tree.exists()

    def test_absent_tree_is_already_clean(self, tmp_path):
        assert safe_rmtree(tmp_path / "nothing-here") is True

    def test_reports_failure_instead_of_leaving_a_remnant(self, tmp_path, monkeypatch):
        """A half-deleted cache makes every later run fail on the stale remnant."""
        tree = tmp_path / "rootfs"
        tree.mkdir()

        def refuse(path, *args, **kwargs):
            raise OSError(5, "Access is denied")

        monkeypatch.setattr("shutil.rmtree", refuse)
        assert safe_rmtree(tree) is False
        assert tree.exists()


class TestLinkAnchoring:
    def test_absolute_link_is_reanchored_into_the_tree(self, tmp_path):
        src = tmp_path / "parts"
        (src / "bin").mkdir(parents=True)
        (src / "bin" / "busybox").write_bytes(b"\x7fELF")
        _link(src / "sbin", "/bin")

        assert _link_target_within(src, src / "sbin") == (Path("bin"), True)

    def test_relative_link_is_kept_verbatim(self, tmp_path):
        src = tmp_path / "parts"
        (src / "bin").mkdir(parents=True)
        (src / "bin" / "busybox").write_bytes(b"\x7fELF")
        _link(src / "bin" / "ash", "busybox", is_dir=False)

        assert _link_target_within(src, src / "bin" / "ash") == (Path("busybox"), False)

    def test_dangling_relative_link_is_dropped(self, tmp_path):
        src = tmp_path / "parts"
        (src / "bin").mkdir(parents=True)
        _link(src / "bin" / "ash", "busybox", is_dir=False)
        assert _link_target_within(src, src / "bin" / "ash") is None

    def test_relative_link_keeps_the_directory_flag(self, tmp_path):
        src = tmp_path / "parts"
        (src / "etc").mkdir(parents=True)
        (src / "etc" / "rc.d").mkdir()
        _link(src / "etc" / "rc.d" / "S99x", "../init.d/rcS", is_dir=False)
        (src / "etc" / "init.d").mkdir()
        (src / "etc" / "init.d" / "rcS").write_text("#!/bin/sh\n", encoding="utf-8")

        target, is_dir = _link_target_within(src, src / "etc" / "rc.d" / "S99x")
        assert target == Path("../init.d/rcS")
        assert is_dir is False

    def test_absolute_link_to_missing_target_is_dropped(self, tmp_path):
        src = tmp_path / "parts"
        src.mkdir()
        _link(src / "config", "/etc/config-does-not-exist")
        assert _link_target_within(src, src / "config") is None

    def test_drive_lettered_link_is_treated_as_escaping(self, tmp_path):
        src = tmp_path / "parts"
        src.mkdir()
        _link(src / "win", "C:/Windows")
        assert _link_target_within(src, src / "win") is None

    def test_relative_link_climbing_out_of_the_tree_is_dropped(self, tmp_path):
        src = tmp_path / "parts"
        (src / "a" / "b" / "c").mkdir(parents=True)
        (src.parent / "passwd").write_text("root:x:0:0\n", encoding="utf-8")
        _link(src / "a" / "b" / "c" / "escape", "../../../../passwd", is_dir=False)
        assert _link_target_within(src, src / "a" / "b" / "c" / "escape") is None

    def test_absolute_link_from_a_subdirectory_climbs_back_in(self, tmp_path):
        src = tmp_path / "parts"
        (src / "usr" / "sbin").mkdir(parents=True)
        (src / "bin").mkdir()
        (src / "bin" / "httpd").write_bytes(b"\x7fELF")
        _link(src / "usr" / "sbin" / "httpd", "/bin/httpd", is_dir=False)

        assert _link_target_within(src, src / "usr" / "sbin" / "httpd") == (
            Path("..") / ".." / "bin" / "httpd", False)


class TestCopyTreeTolerant:
    def test_absolute_links_do_not_leave_the_tree(self, tmp_path):
        """The real defect: ``sbin -> /bin`` verbatim resolves against the *host*."""
        src = tmp_path / "parts"
        (src / "bin").mkdir(parents=True)
        (src / "bin" / "busybox").write_bytes(b"\x7fELF\x00" * 8)
        _link(src / "sbin", "/bin")
        dst = tmp_path / "rootfs"

        _copy_tree_tolerant(src, dst)

        copied = dst / "sbin"
        assert copied.is_symlink()
        assert not os.path.isabs(os.readlink(copied))
        assert (dst / "sbin" / "busybox").is_file()

    def test_regular_files_are_copied(self, tmp_path):
        src = tmp_path / "parts"
        (src / "etc").mkdir(parents=True)
        (src / "etc" / "passwd").write_text("root:x:0:0\n", encoding="utf-8")
        dst = tmp_path / "rootfs"

        _copy_tree_tolerant(src, dst)
        assert (dst / "etc" / "passwd").read_text(encoding="utf-8") == "root:x:0:0\n"

    def test_one_unreadable_entry_does_not_abort_the_copy(self, tmp_path, monkeypatch):
        src = tmp_path / "parts"
        (src / "bin").mkdir(parents=True)
        (src / "bin" / "busybox").write_bytes(b"\x7fELF")
        (src / "bin" / "hostile").write_bytes(b"\x7fELF")
        real_is_dir = Path.is_dir
        monkeypatch.setattr(
            Path, "is_dir",
            lambda self: _unresolvable(self) if self.name == "hostile" else real_is_dir(self),
        )
        dst = tmp_path / "rootfs"

        _copy_tree_tolerant(src, dst)
        assert (dst / "bin" / "busybox").is_file()


class TestTreeHasContent:
    def test_populated_tree(self, tmp_path):
        tree = tmp_path / "romfs"
        (tree / "bin").mkdir(parents=True)
        (tree / "bin" / "sh").write_bytes(b"x")
        assert _tree_has_content(tree) is True

    def test_empty_tree(self, tmp_path):
        tree = tmp_path / "romfs"
        tree.mkdir()
        assert _tree_has_content(tree) is False

    def test_missing_tree(self, tmp_path):
        assert _tree_has_content(tmp_path / "absent") is False

    def test_symlink_only_tree_is_visible_content(self, tmp_path):
        """``rglob`` drops a dead reparse point; a link-only tree must look empty.

        Treating it as populated would skip jefferson and hand the caller an empty
        rootfs while reporting success.
        """
        tree = tmp_path / "romfs"
        tree.mkdir()
        _link(tree / "sbin", "/bin")
        assert _tree_has_content(tree) is True

    def test_nested_entry_is_found_without_following_links(self, tmp_path):
        tree = tmp_path / "romfs"
        (tree / "bin").mkdir(parents=True)
        (tree / "bin" / "sh").write_bytes(b"x")
        assert _tree_has_content(tree) is True

    def test_unreadable_root_is_reported_empty_not_raised(self, tmp_path, monkeypatch):
        """A tree that cannot even be listed answers, it does not propagate."""
        tree = tmp_path / "romfs"
        tree.mkdir()
        monkeypatch.setattr(os, "scandir", lambda p: _unresolvable(Path(p)))
        assert _tree_has_content(tree) is False