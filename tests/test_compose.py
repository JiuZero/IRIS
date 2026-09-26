"""Unit tests for container-side rootfs composition (no Docker required)."""

from pathlib import Path

import pytest

from iris.emulate import orchestrator
from iris.emulate.orchestrator import _compose_rootfs_from_slices, build_parts_mounts


@pytest.fixture()
def parts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "RP3V30-parts"
    d.mkdir()
    for name in ("romfs", "user", "custom", "slave", "vendor"):
        (d / f"{name}.jffs2").write_bytes(b"\x85\x19\x03\x20" + b"\x00" * 60)
    return d


class TestBuildPartsMounts:
    def test_orders_base_first_and_maps_known_partitions(self, parts_dir):
        mounts = build_parts_mounts(parts_dir)
        # ordered by mount point: base first, overlays by mount path
        assert mounts == [
            ("romfs", "/"),
            ("user", "/opt/app"),
            ("custom", "/opt/custom"),
            ("slave", "/opt/sav"),
            ("vendor", "/opt/vendor"),
        ]

    def test_overlays_sorted_by_mount(self, parts_dir):
        mounts = build_parts_mounts(parts_dir)
        overlay_mounts = [m for _s, m in mounts if m != "/"]
        assert overlay_mounts == sorted(overlay_mounts)

    def test_empty_dir(self, tmp_path):
        d = tmp_path / "empty-parts"
        d.mkdir()
        assert build_parts_mounts(d) == []


class TestComposeScript:
    @pytest.fixture()
    def captured(self, monkeypatch, tmp_path):
        calls = {}

        def fake_produce(volumes, image, script, container_out, host_out, timeout=240):
            calls["volumes"] = volumes
            calls["image"] = image
            calls["script"] = script
            host_out.parent.mkdir(parents=True, exist_ok=True)
            host_out.write_bytes(b"tar")
            return host_out

        monkeypatch.setattr(orchestrator, "_docker_produce", fake_produce)
        return calls

    def test_script_extracts_and_merges_each_slice(self, parts_dir, captured, tmp_path):
        mounts = build_parts_mounts(parts_dir)
        out = tmp_path / "composed.tar.gz"
        _compose_rootfs_from_slices(parts_dir, mounts, out)
        s = captured["script"]
        for stem, mount in mounts:
            assert f"jefferson -d /work/{stem} -f /in/{stem}.jffs2" in s
            target = "$BASE" if mount == "/" else f"$BASE{mount}"
            assert f"cp -a /work/{stem}/. {target}/" in s
        assert "tar -czf /work/rootfs.tar.gz -C $BASE ." in s

    def test_script_cleans_intermediate_trees(self, parts_dir, captured, tmp_path):
        mounts = build_parts_mounts(parts_dir)
        _compose_rootfs_from_slices(parts_dir, mounts, tmp_path / "c.tar.gz")
        s = captured["script"]
        cleanup = [line for line in s.split("; ") if line.startswith("for d in rootfs")]
        assert len(cleanup) == 1
        for stem, _m in mounts:
            assert stem in cleanup[0]
        assert "do rm -rf /work/$d; done" in s

    def test_shadow_mount_fix_targets_only_non_root_mounts(self, parts_dir, captured, tmp_path):
        mounts = build_parts_mounts(parts_dir)
        _compose_rootfs_from_slices(parts_dir, mounts, tmp_path / "c.tar.gz")
        s = captured["script"]
        for mp in ("/opt/app", "/opt/custom", "/opt/sav"):
            assert f"mount.*{mp}[[:space:]]" in s
        # the root overlay must never be commented
        assert "mount.*[[:space:]]" not in s

    def test_guest_script_injected_via_base64(self, parts_dir, captured, tmp_path):
        script_body = "#!/bin/sh\nmknod -m 666 /dev/mem c 1 1\n"
        _compose_rootfs_from_slices(
            parts_dir, build_parts_mounts(parts_dir), tmp_path / "c.tar.gz",
            guest_script=script_body,
        )
        s = captured["script"]
        assert "base64 -d > $BASE/firmadyne/iris_rules.sh" in s
        assert "chmod +x $BASE/firmadyne/iris_rules.sh" in s
        import base64

        b64 = base64.b64encode(script_body.encode()).decode()
        assert f"echo {b64} |" in s

    def test_volumes_mount_slices_readonly_and_work_writable(self, parts_dir, captured, tmp_path):
        out = tmp_path / "sub" / "composed.tar.gz"
        _compose_rootfs_from_slices(parts_dir, build_parts_mounts(parts_dir), out)
        vols = dict(captured["volumes"])
        assert vols[str(parts_dir.resolve())] == "/in:ro"
        assert vols[str(out.parent.resolve())] == "/work"
        assert out.parent.exists()


class TestDockerProduceArgOrder:
    def test_create_cmd_keeps_flags_after_subcommand(self, monkeypatch, tmp_path):
        seen = {}

        class FakeProc:
            returncode = 0
            stdout = "abc123containerid"
            stderr = ""

        def fake_run(cmd, **kwargs):
            seen.setdefault("cmds", []).append(cmd)
            return FakeProc()

        monkeypatch.setattr(orchestrator.subprocess, "run", fake_run)
        host_out = tmp_path / "out.bin"
        host_out.write_bytes(b"x")
        orchestrator._docker_produce(
            [("/host/in", "/in:ro"), ("/host/work", "/work")],
            "python:3.11-alpine",
            "echo hi",
            "/work/out.bin",
            host_out,
        )
        create = seen["cmds"][0]
        assert create[:2] == ["docker", "create"], "flags must follow the create subcommand"
        assert create[create.index("python:3.11-alpine") - 1] == "/host/work:/work"
        assert create[create.index("python:3.11-alpine") - 3] == "/host/in:/in:ro"

    def test_rejects_version_string_as_container_id(self, monkeypatch, tmp_path):
        class FakeProc:
            returncode = 0
            stdout = "Docker version 29.5.3, build d1c06ef\n"
            stderr = ""

        monkeypatch.setattr(orchestrator.subprocess, "run", lambda cmd, **k: FakeProc())
        with pytest.raises(RuntimeError, match="unexpected output"):
            orchestrator._docker_produce([], "img", "s", "/o", tmp_path / "o")
