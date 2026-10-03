"""Unit tests for the baked emulation image cache (no Docker required).

``Dockerfile.baked`` copies ``scripts/emulate/*.sh`` into the image, which makes
the image the compiled form of those scripts. Tagging it ``:latest`` and reusing
it whenever it exists made every script edit silently inert: the run reported
success while the container executed the previous revision of ``make_image.sh`` —
indistinguishable from the fix not working. The tag therefore carries a digest of
the sources baked into it.

These tests drive ``_build_baked_image``/``_drop_other_baked_tags`` with only the
``docker`` calls stubbed, so the reuse-vs-rebuild decision and the command lines
are the real ones.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from iris.emulate import orchestrator
from iris.emulate.orchestrator import (
    _baked_scripts_fingerprint,
    _build_baked_image,
    _drop_other_baked_tags,
)


class FakeDocker:
    """Records docker command lines and answers image inspect / images."""

    def __init__(self, existing_images: tuple[str, ...] = ()) -> None:
        self.existing = set(existing_images)
        self.commands: list[list[str]] = []
        self.build_ok = True

    def __call__(self, cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
        self.commands.append(list(cmd))
        verb = cmd[1] if len(cmd) > 1 else ""
        if verb == "image":
            name = cmd[3] if len(cmd) > 3 else ""
            code = 0 if name in self.existing else 1
            return subprocess.CompletedProcess(cmd, code, "", "" if code == 0 else "No such image")
        if verb == "build":
            tag = cmd[cmd.index("-t") + 1]
            if self.build_ok:
                self.existing.add(tag)
            code = 0 if self.build_ok else 1
            return subprocess.CompletedProcess(cmd, code, "", "" if code == 0 else "boom")
        if verb == "images":
            listing = "\n".join(sorted(self.existing))
            return subprocess.CompletedProcess(cmd, 0, listing, "")
        if verb == "rmi":
            name = cmd[-1]
            self.existing.discard(name)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture()
def fake_docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(orchestrator, "_run", fake)
    return fake


def test_fingerprint_is_stable_for_unchanged_sources():
    assert _baked_scripts_fingerprint() == _baked_scripts_fingerprint()


def test_fingerprint_is_twelve_hex_chars():
    fingerprint = _baked_scripts_fingerprint()
    assert len(fingerprint) == 12
    assert all(c in "0123456789abcdef" for c in fingerprint)


def test_fingerprint_covers_the_real_emulation_scripts():
    """A digest that missed the scripts would reproduce the original bug."""
    project_root = Path(orchestrator.__file__).parent.parent.parent.parent
    assert (project_root / "scripts" / "emulate" / "make_image.sh").is_file()
    assert (project_root / "docker" / "emulate" / "Dockerfile.baked").is_file()
    assert _baked_scripts_fingerprint()


def test_fingerprint_changes_when_a_script_changes(tmp_path):
    project = _fake_project(tmp_path)
    before = _baked_scripts_fingerprint(project)
    (project / "scripts" / "emulate" / "make_image.sh").write_text("echo two\n", encoding="utf-8")
    assert _baked_scripts_fingerprint(project) != before


def test_fingerprint_changes_when_a_new_script_is_added(tmp_path):
    project = _fake_project(tmp_path)
    before = _baked_scripts_fingerprint(project)
    # The inittab injection lives in a script created after the image was first
    # built; its arrival must invalidate the tag or it never reaches a container.
    (project / "scripts" / "emulate" / "iris_net_fix_bg.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    assert _baked_scripts_fingerprint(project) != before


def test_fingerprint_changes_when_the_dockerfile_changes(tmp_path):
    project = _fake_project(tmp_path)
    before = _baked_scripts_fingerprint(project)
    (project / "docker" / "emulate" / "Dockerfile.baked").write_text("FROM y\n", encoding="utf-8")
    assert _baked_scripts_fingerprint(project) != before


def test_fingerprint_ignores_an_unreadable_script_but_still_registered(tmp_path, monkeypatch):
    """A file that cannot be read mid-glob must not raise; its name still counts."""
    project = _fake_project(tmp_path)
    ghost = project / "scripts" / "emulate" / "vanishes.sh"
    ghost.write_text("#!/bin/sh\n", encoding="utf-8")
    real_read = Path.read_bytes

    def refuse(self):
        if self.name == "vanishes.sh":
            # The WinError 1920 shape: a reparse point the host cannot read.
            raise OSError(1920, "The system cannot access this file")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", refuse)
    with_ghost = _baked_scripts_fingerprint(project)
    monkeypatch.undo()

    ghost.unlink()
    assert with_ghost != _baked_scripts_fingerprint(project)


def test_fingerprint_survives_an_empty_scripts_dir(tmp_path):
    project = _fake_project(tmp_path)
    for path in (project / "scripts" / "emulate").iterdir():
        path.unlink()
    assert len(_baked_scripts_fingerprint(project)) == 12


def _fake_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "docker" / "emulate").mkdir(parents=True)
    (project / "scripts" / "emulate").mkdir(parents=True)
    (project / "docker" / "emulate" / "Dockerfile.baked").write_text("FROM x\n", encoding="utf-8")
    (project / "scripts" / "emulate" / "make_image.sh").write_text("echo one\n", encoding="utf-8")
    return project


class TestBuildBakedImage:
    def test_missing_image_is_built_and_tagged_with_the_fingerprint(self, fake_docker):
        tag = _build_baked_image()
        assert tag == f"iris-emulate-baked:{_baked_scripts_fingerprint()}"
        build_cmds = [c for c in fake_docker.commands if c[1] == "build"]
        assert len(build_cmds) == 1
        assert build_cmds[0][build_cmds[0].index("-t") + 1] == tag
        assert build_cmds[0][-1] == str(Path(orchestrator.__file__).parent.parent.parent.parent)

    def test_existing_image_is_reused_without_rebuilding(self, fake_docker, monkeypatch):
        monkeypatch.setattr(
            orchestrator,
            "_baked_scripts_fingerprint",
            lambda: "deadbeefcafe",
        )
        fake_docker.existing.add("iris-emulate-baked:deadbeefcafe")
        assert _build_baked_image() == "iris-emulate-baked:deadbeefcafe"
        assert not [c for c in fake_docker.commands if c[1] == "build"]

    def test_a_script_edit_forces_a_rebuild_of_the_same_tag(self, fake_docker, monkeypatch):
        """The exact regression: make_image.sh changed, so :latest must not do."""
        monkeypatch.setattr(orchestrator, "_baked_scripts_fingerprint", lambda: "aaaa1111bbbb")
        monkeypatch.setattr(orchestrator, "_drop_other_baked_tags", lambda keep: None)
        fake_docker.existing.add("iris-emulate-baked:aaaa1111bbbb")
        assert _build_baked_image() == "iris-emulate-baked:aaaa1111bbbb"

        monkeypatch.setattr(orchestrator, "_baked_scripts_fingerprint", lambda: "cccc2222dddd")
        assert _build_baked_image() == "iris-emulate-baked:cccc2222dddd"
        assert len([c for c in fake_docker.commands if c[1] == "build"]) == 1

    def test_failed_build_raises_with_docker_stderr(self, fake_docker):
        fake_docker.build_ok = False
        with pytest.raises(RuntimeError, match="Docker build failed: boom"):
            _build_baked_image()

    def test_build_never_contacts_the_registry_for_the_base_image(self, fake_docker):
        """The base is our own iris-emulate, already on the daemon.

        With buildkit's default pull behaviour a mirror outage (403 from a
        registry-mirror) fails the build, so a shell fix cannot be rebuilt at all —
        which looks like "the fix did not work" rather than "the mirror is down".
        """
        _build_baked_image()
        build_cmd = next(c for c in fake_docker.commands if c[1] == "build")
        assert "--pull=false" in build_cmd


class TestDropOtherBakedTags:
    def test_removes_other_tags_and_keeps_the_current_one(self, fake_docker):
        fake_docker.existing.update(
            {
                "iris-emulate-baked:111111111111",
                "iris-emulate-baked:222222222222",
                "ubuntu:24.04",
                "redis:7-alpine",
            }
        )
        _drop_other_baked_tags(keep="iris-emulate-baked:222222222222")
        assert fake_docker.existing == {"iris-emulate-baked:222222222222", "ubuntu:24.04", "redis:7-alpine"}

    def test_does_not_remove_anything_when_it_is_the_only_tag(self, fake_docker):
        fake_docker.existing.add("iris-emulate-baked:222222222222")
        _drop_other_baked_tags(keep="iris-emulate-baked:222222222222")
        assert not [c for c in fake_docker.commands if c[1] == "rmi"]

    def test_build_triggers_the_cleanup(self, fake_docker, monkeypatch):
        monkeypatch.setattr(orchestrator, "_baked_scripts_fingerprint", lambda: "222222222222")
        fake_docker.existing.add("iris-emulate-baked:111111111111")
        _build_baked_image()
        assert "iris-emulate-baked:111111111111" not in fake_docker.existing
        assert "iris-emulate-baked:222222222222" in fake_docker.existing