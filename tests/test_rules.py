"""Tests for iris.rules.engine — YAML rule loading, detection and repair actions."""

from __future__ import annotations

from pathlib import Path

from iris.rules.engine import GUEST_SCRIPT_PATH, apply_rules, load_rules

PROJECT_RULES = Path(__file__).resolve().parents[1] / "rules"

_RCS = """\
#!/bin/sh
mount -t jffs2 /dev/mtdblock2 /cfg
mount -t jffs2 /dev/mtdblock4 /opt/app
partition_no=$(grep -i picture /proc/mtd | head -1)
echo done
"""


def _make_rootfs(tmp_path: Path) -> Path:
    (tmp_path / "etc" / "init.d").mkdir(parents=True)
    (tmp_path / "opt" / "app" / "bin").mkdir(parents=True)
    (tmp_path / "etc" / "init.d" / "rcS").write_text(_RCS)
    (tmp_path / "opt" / "app" / "bin" / "Kylin").write_bytes(b"\x7fELF" + b"\x00" * 60)
    return tmp_path


class TestBundledRules:
    def test_load_project_rules(self):
        rules = load_rules(PROJECT_RULES)
        ids = {r.id for r in rules}
        assert {"dev-extended-nodes", "shadow-jffs2-opt", "mtd-name-lookup-guard"} <= ids
        for r in rules:
            assert r.description.strip()
            assert r.stage

    def test_actions_parse(self):
        rules = load_rules(PROJECT_RULES)
        nodes = next(r for r in rules if r.id == "dev-extended-nodes")
        shell = "\n".join(nodes.actions[0]["guest_shell"])
        assert "/dev/mtdblock$i" in shell
        assert "/dev/watchdog" in shell


class TestApplyRules:
    def test_dry_run_changes_nothing(self, tmp_path):
        rootfs = _make_rootfs(tmp_path)
        before = (rootfs / "etc" / "init.d" / "rcS").read_text()
        reports = apply_rules(rootfs, load_rules(PROJECT_RULES), dry_run=True)
        assert (rootfs / "etc" / "init.d" / "rcS").read_text() == before
        assert not (rootfs / GUEST_SCRIPT_PATH).exists()
        by_id = {r.rule_id: r for r in reports}
        assert by_id["shadow-jffs2-opt"].matched
        assert by_id["dev-extended-nodes"].matched
        assert by_id["mtd-name-lookup-guard"].matched

    def test_apply_comments_shadow_mount_only(self, tmp_path):
        rootfs = _make_rootfs(tmp_path)
        apply_rules(rootfs, load_rules(PROJECT_RULES), dry_run=False)
        rcS = (rootfs / "etc" / "init.d" / "rcS").read_text()
        assert "#IRIS-shadow-fix: mount -t jffs2 /dev/mtdblock4 /opt/app" in rcS
        assert "mount -t jffs2 /dev/mtdblock2 /cfg" in rcS  # only /opt/* is shadowed
        assert "grep -i picture" in rcS  # fingerprint rule never edits

    def test_apply_writes_guest_script(self, tmp_path):
        rootfs = _make_rootfs(tmp_path)
        apply_rules(rootfs, load_rules(PROJECT_RULES), dry_run=False)
        script = (rootfs / GUEST_SCRIPT_PATH).read_text()
        assert script.startswith("#!/bin/sh")
        assert "13 14 15" in script and "watchdog" in script

    def test_apply_is_idempotent(self, tmp_path):
        rootfs = _make_rootfs(tmp_path)
        rules = load_rules(PROJECT_RULES)
        apply_rules(rootfs, rules, dry_run=False)
        once = (rootfs / "etc" / "init.d" / "rcS").read_text()
        apply_rules(rootfs, rules, dry_run=False)
        assert (rootfs / "etc" / "init.d" / "rcS").read_text() == once

    def test_clean_rootfs_only_always_rules(self, tmp_path):
        (tmp_path / "etc").mkdir()
        (tmp_path / "etc" / "passwd").write_text("root:x:0:0\n")
        reports = apply_rules(tmp_path, load_rules(PROJECT_RULES), dry_run=True)
        matched = {r.rule_id for r in reports if r.matched}
        assert matched == {"dev-extended-nodes"}
