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


class TestEngineHardening:
    def test_crlf_and_lf_endings_preserved(self, tmp_path):
        from iris.rules.engine import Rule, apply_rules

        root = tmp_path / "rf"
        root.mkdir()
        (root / "etc").mkdir()
        (root / "etc" / "crlf.sh").write_bytes(b"#!/bin/sh\r\necho AA\r\n")
        (root / "etc" / "lf.sh").write_bytes(b"#!/bin/sh\necho AA\n")
        rule = Rule(
            id="t", description="d", stage="boot",
            detect=[{"file_glob": "etc/*"}],
            actions=[{"edit": {"regex": "AA", "replacement": "BB", "within": "etc/*"}}],
        )
        apply_rules(root, [rule], dry_run=False)
        assert (root / "etc" / "crlf.sh").read_bytes() == b"#!/bin/sh\r\necho BB\r\n"
        assert (root / "etc" / "lf.sh").read_bytes() == b"#!/bin/sh\necho BB\n"

    def test_dir_nonempty_scopes_files_to_dir(self, tmp_path):
        from iris.rules.engine import Rule, apply_rules

        root = tmp_path / "rf"
        (root / "opt" / "app").mkdir(parents=True)
        (root / "etc").mkdir()
        (root / "opt" / "app" / "in.sh").write_text("PAT run\n")
        (root / "etc" / "out.sh").write_text("PAT run\n")
        rule = Rule(
            id="t", description="d", stage="boot",
            detect=[{"dir_nonempty": "opt/app"}],
            actions=[{"edit": {"regex": "PAT", "replacement": "X", "within": "*"}}],
        )
        reports = apply_rules(root, [rule], dry_run=False)
        assert (root / "opt" / "app" / "in.sh").read_text() == "X run\n"
        assert (root / "etc" / "out.sh").read_text() == "PAT run\n"
        assert reports[0].matched

    def test_write_action_rejects_parent_escape(self, tmp_path):
        from iris.rules.engine import Rule, apply_rules

        root = tmp_path / "rf"
        (root / "etc").mkdir(parents=True)
        rule = Rule(
            id="t", description="d", stage="boot",
            detect=[{"always": True}],
            actions=[{"write": {"path": "../evil", "content": "x"}}],
        )
        reports = apply_rules(root, [rule], dry_run=False)
        assert "refused" in reports[0].detail
        assert not (tmp_path / "evil").exists()

    def test_guest_script_written_with_lf(self, tmp_path):
        rootfs = _make_rootfs(tmp_path)
        apply_rules(rootfs, load_rules(PROJECT_RULES), dry_run=False)
        data = (rootfs / GUEST_SCRIPT_PATH).read_bytes()
        assert b"\r\n" not in data

    def test_vendor_watchdog_rule_disables_monitor(self, tmp_path):
        root = tmp_path / "olt"
        (root / "etc").mkdir(parents=True)
        (root / "etc" / "inittab").write_text(
            "::sysinit:/etc/init.d/rcS\n"
            "::shutdown:/etc/init.d/shutdown\n"
            "::once:-/bin/monitor\n"
            "#::respawn:-/sbin/watchdog\n"
        )
        reports = apply_rules(root, load_rules(PROJECT_RULES), dry_run=False)
        by_id = {r.rule_id: r for r in reports}
        assert by_id["vendor-watchdog-monitor"].matched

        inittab = (root / "etc" / "inittab").read_text()
        assert "#IRIS-watchdog: ::once:-/bin/monitor" in inittab
        assert "::sysinit:/etc/init.d/rcS" in inittab  # unrelated lines untouched
        assert inittab.count("#IRIS-watchdog: #") == 0  # already-commented stays as-is

        script = (root / GUEST_SCRIPT_PATH).read_text()
        assert "/opt/monitor" in script and "iris-disabled" in script
        # re-running must not comment the line twice
        apply_rules(root, load_rules(PROJECT_RULES), dry_run=False)
        assert (root / "etc" / "inittab").read_text() == inittab

    def test_watchdog_rule_ignores_rootfs_without_monitor(self, tmp_path):
        root = tmp_path / "plain"
        (root / "etc").mkdir(parents=True)
        (root / "etc" / "inittab").write_text("::sysinit:/etc/init.d/rcS\n")
        reports = apply_rules(root, load_rules(PROJECT_RULES), dry_run=False)
        assert not next(r for r in reports if r.rule_id == "vendor-watchdog-monitor").matched
        assert "iris-disabled" not in (root / GUEST_SCRIPT_PATH).read_text()

    def test_text_actions_never_rewrite_binaries(self, tmp_path):
        """A broad `within: *` must not corrupt a vendor ELF via lossy text decoding."""
        from iris.rules.engine import Rule, apply_rules

        root = tmp_path / "rf"
        (root / "bin").mkdir(parents=True)
        blob = b"\x7fELF\x02\x01\x01\x00SHELLSTRING\x00" + bytes(range(1, 64))
        (root / "bin" / "vendor").write_bytes(blob)
        rule = Rule(
            id="t", description="d", stage="boot",
            detect=[{"always": True}],
            actions=[{"edit": {"regex": "SHELLSTRING", "replacement": "X", "within": "*"}}],
        )
        apply_rules(root, [rule], dry_run=False)
        assert (root / "bin" / "vendor").read_bytes() == blob
