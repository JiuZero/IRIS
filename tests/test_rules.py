"""Tests for iris.rules.engine — YAML rule loading, detection and repair actions."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from iris.rules.engine import (
    GUEST_SCRIPT_PATH,
    VERIFY_LOG_DIR,
    VERIFY_LOG_PATH,
    Rule,
    _can_judge_executable,
    _verify_lines,
    apply_rules,
    load_rules,
)

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
    (tmp_path / "etc" / "init.d" / "rcS").write_text(_RCS, encoding="utf-8")
    (tmp_path / "opt" / "app" / "bin" / "Kylin").write_bytes(b"\x7fELF" + b"\x00" * 60)
    return tmp_path


def _write_rule_dir(parent: Path, doc: dict) -> Path:
    rules_dir = parent / "rules"
    rules_dir.mkdir(exist_ok=True)
    (rules_dir / "probe.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return rules_dir


class TestBundledRules:
    def test_load_project_rules(self):
        rules = load_rules(PROJECT_RULES)
        assert rules
        assert all(r.id for r in rules)
        assert all(r.description for r in rules)

    def test_clean_rootfs_only_always_rules(self, tmp_path):
        rootfs = tmp_path / "rf"
        (rootfs / "etc" / "init.d").mkdir(parents=True)
        (rootfs / "etc" / "passwd").write_text("root:x:0:0\n", encoding="utf-8")
        reports = apply_rules(rootfs, load_rules(PROJECT_RULES), dry_run=True)
        matched = {r.rule_id for r in reports if r.matched}
        assert matched == {"dev-extended-nodes"}

    def test_guest_script_is_lf_only(self, tmp_path):
        rootfs = _make_rootfs(tmp_path)
        apply_rules(rootfs, load_rules(PROJECT_RULES), dry_run=False)
        assert b"\r\n" not in (rootfs / GUEST_SCRIPT_PATH).read_bytes()


class TestApplyRules:
    def test_apply_writes_guest_script(self, tmp_path):
        rootfs = _make_rootfs(tmp_path)
        apply_rules(rootfs, load_rules(PROJECT_RULES), dry_run=False)
        script = (rootfs / GUEST_SCRIPT_PATH).read_text(encoding="utf-8")
        assert script.startswith("#!/bin/sh")

    def test_dry_run_touches_nothing(self, tmp_path):
        rootfs = _make_rootfs(tmp_path)
        before = (rootfs / "etc" / "init.d" / "rcS").read_text(encoding="utf-8")
        apply_rules(rootfs, load_rules(PROJECT_RULES), dry_run=True)
        assert (rootfs / "etc" / "init.d" / "rcS").read_text(encoding="utf-8") == before
        assert not (rootfs / GUEST_SCRIPT_PATH).exists()

    def test_comment_lines_is_idempotent(self, tmp_path):
        rootfs = _make_rootfs(tmp_path)
        apply_rules(rootfs, load_rules(PROJECT_RULES), dry_run=False)
        rcS = (rootfs / "etc" / "init.d" / "rcS").read_text(encoding="utf-8")
        apply_rules(rootfs, load_rules(PROJECT_RULES), dry_run=False)
        assert (rootfs / "etc" / "init.d" / "rcS").read_text(encoding="utf-8") == rcS

    def test_edit_preserves_line_endings(self, tmp_path):
        from iris.rules.engine import apply_rules as ar

        root = tmp_path / "rf"
        (root / "etc").mkdir(parents=True)
        (root / "etc" / "crlf.sh").write_bytes(b"#!/bin/sh\r\necho AA\r\n")
        (root / "etc" / "lf.sh").write_bytes(b"#!/bin/sh\necho AA\n")
        ar(root, [Rule(id="t", description="d", stage="boot",
                       detect=[{"dir_nonempty": "etc"}],
                       actions=[{"edit": {"regex": "AA", "replacement": "BB", "within": "etc/*"}}])],
            dry_run=False)
        assert (root / "etc" / "crlf.sh").read_bytes() == b"#!/bin/sh\r\necho BB\r\n"
        assert (root / "etc" / "lf.sh").read_bytes() == b"#!/bin/sh\necho BB\n"

    def test_dir_nonempty_scopes_files_to_dir(self, tmp_path):
        root = tmp_path / "rf"
        (root / "opt" / "app").mkdir(parents=True)
        (root / "etc").mkdir()
        (root / "opt" / "app" / "in.sh").write_text("PAT run\n", encoding="utf-8")
        (root / "etc" / "out.sh").write_text("PAT run\n", encoding="utf-8")
        rule = Rule(
            id="t", description="d", stage="boot",
            detect=[{"dir_nonempty": "opt/app"}],
            actions=[{"edit": {"regex": "PAT", "replacement": "X", "within": "*"}}],
        )
        reports = apply_rules(root, [rule], dry_run=False)
        assert (root / "opt" / "app" / "in.sh").read_text(encoding="utf-8") == "X run\n"
        assert (root / "etc" / "out.sh").read_text(encoding="utf-8") == "PAT run\n"
        assert reports[0].matched

    def test_write_action_rejects_parent_escape(self, tmp_path):
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
            "#::respawn:-/sbin/watchdog\n",
            encoding="utf-8",
        )
        reports = apply_rules(root, load_rules(PROJECT_RULES), dry_run=False)
        by_id = {r.rule_id: r for r in reports}
        assert by_id["vendor-watchdog-monitor"].matched

        inittab = (root / "etc" / "inittab").read_text(encoding="utf-8")
        assert "#IRIS-watchdog: ::once:-/bin/monitor" in inittab
        assert "::sysinit:/etc/init.d/rcS" in inittab  # unrelated lines untouched
        assert inittab.count("#IRIS-watchdog: #") == 0  # already-commented stays as-is

        script = (root / GUEST_SCRIPT_PATH).read_text(encoding="utf-8")
        assert "/opt/monitor" in script and "iris-disabled" in script
        # re-running must not comment the line twice
        apply_rules(root, load_rules(PROJECT_RULES), dry_run=False)
        assert (root / "etc" / "inittab").read_text(encoding="utf-8") == inittab

    def test_watchdog_rule_ignores_rootfs_without_monitor(self, tmp_path):
        root = tmp_path / "plain"
        (root / "etc").mkdir(parents=True)
        (root / "etc" / "inittab").write_text("::sysinit:/etc/init.d/rcS\n", encoding="utf-8")
        reports = apply_rules(root, load_rules(PROJECT_RULES), dry_run=False)
        assert not next(r for r in reports if r.rule_id == "vendor-watchdog-monitor").matched
        assert "iris-disabled" not in (root / GUEST_SCRIPT_PATH).read_text(encoding="utf-8")

    def test_watchdog_rule_matches_binary_without_inittab_entry(self, tmp_path):
        """A supervisor started from an rc script leaves no inittab fingerprint."""
        root = tmp_path / "olt2"
        (root / "etc").mkdir(parents=True)
        (root / "opt" / "monitor").mkdir(parents=True)
        (root / "etc" / "inittab").write_text("::sysinit:/etc/init.d/rcS\n", encoding="utf-8")
        reports = apply_rules(root, load_rules(PROJECT_RULES), dry_run=False)
        assert next(r for r in reports if r.rule_id == "vendor-watchdog-monitor").matched

    def test_diag_rule_keys_on_the_binary_not_on_a_script(self, tmp_path):
        root = tmp_path / "diag"
        (root / "bin").mkdir(parents=True)
        (root / "bin" / "diag").write_text("stub", encoding="utf-8")
        reports = apply_rules(root, load_rules(PROJECT_RULES), dry_run=False)
        assert next(r for r in reports if r.rule_id == "generic-diag-crash-fix").matched

    def test_web_rule_needs_both_rcs_and_a_web_binary(self, tmp_path):
        rules = load_rules(PROJECT_RULES)
        rcs_only = tmp_path / "a"
        (rcs_only / "etc" / "init.d").mkdir(parents=True)
        (rcs_only / "etc" / "init.d" / "rcS").write_text("#!/bin/sh\n", encoding="utf-8")
        assert not next(
            r for r in apply_rules(rcs_only, rules, dry_run=True)
            if r.rule_id == "tenda-web-server-forced-start"
        ).matched

        both = tmp_path / "b"
        (both / "etc" / "init.d").mkdir(parents=True)
        (both / "etc" / "init.d" / "rcS").write_text("#!/bin/sh\n", encoding="utf-8")
        (both / "opt" / "goahead").mkdir(parents=True)
        (both / "opt" / "goahead" / "goahead").write_text("stub", encoding="utf-8")
        assert next(
            r for r in apply_rules(both, rules, dry_run=True)
            if r.rule_id == "tenda-web-server-forced-start"
        ).matched

    def test_text_actions_never_rewrite_binaries(self, tmp_path):
        """A broad `within: *` must not corrupt a vendor ELF via lossy text decoding."""
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


class TestDetectConditions:
    """path_exists plus all/any nesting: a rule may need several facts at once."""

    @staticmethod
    def _rules(parent: Path, detect: list[dict]) -> Path:
        return _write_rule_dir(parent, {
            "id": "probe", "description": "probe", "detect": detect,
            "actions": [{"guest_shell": ["true"]}],
        })

    @staticmethod
    def _fixture(tmp_path: Path, with_monitor: bool = True) -> Path:
        rootfs = tmp_path / "rootfs"
        (rootfs / "etc").mkdir(parents=True)
        (rootfs / "etc" / "inittab").write_text("::sysinit:/etc/init.d/rcS\n", encoding="utf-8")
        if with_monitor:
            (rootfs / "opt" / "monitor").mkdir(parents=True)
            (rootfs / "opt" / "monitor" / "run").write_text("x", encoding="utf-8")
        return rootfs

    def test_path_exists_matches_file_and_directory(self, tmp_path):
        rootfs = self._fixture(tmp_path)
        by_file = apply_rules(rootfs, load_rules(self._rules(tmp_path, [{"path_exists": "opt/monitor/run"}])))
        assert by_file[0].matched
        by_dir = apply_rules(rootfs, load_rules(self._rules(tmp_path, [{"path_exists": "opt/monitor"}])))
        assert by_dir[0].matched

    def test_path_exists_misses_absent_path(self, tmp_path):
        rootfs = self._fixture(tmp_path, with_monitor=False)
        reports = apply_rules(rootfs, load_rules(self._rules(tmp_path, [{"path_exists": "opt/monitor"}])))
        assert not reports[0].matched

    def test_path_exists_executable_modifier(self, tmp_path):
        """Off POSIX the modifier cannot be judged, so it must not reject the file."""
        from iris.rules.engine import _can_judge_executable

        rootfs = self._fixture(tmp_path)
        plain = load_rules(self._rules(tmp_path, [{"path_exists": "etc/inittab"}]))
        strict = load_rules(self._rules(tmp_path, [{"path_exists": "etc/inittab", "executable": True}]))
        assert apply_rules(rootfs, plain)[0].matched
        assert apply_rules(rootfs, strict)[0].matched
        if not _can_judge_executable():
            assert any("executable" in w for w in strict[0].warnings)

    @pytest.mark.skipif(not _can_judge_executable(), reason="POSIX execute bit required")
    def test_path_exists_executable_modifier_on_posix(self, tmp_path):
        rootfs = self._fixture(tmp_path)
        strict = load_rules(self._rules(tmp_path, [{"path_exists": "etc/inittab", "executable": True}]))
        os.chmod(rootfs / "etc" / "inittab", 0o644)
        assert not apply_rules(rootfs, strict)[0].matched
        os.chmod(rootfs / "etc" / "inittab", 0o755)
        assert apply_rules(rootfs, strict)[0].matched

    def test_path_exists_ignores_symlinks(self, tmp_path):
        rootfs = self._fixture(tmp_path, with_monitor=False)
        os.symlink("/nowhere", rootfs / "etc" / "dangling")
        reports = apply_rules(rootfs, load_rules(self._rules(tmp_path, [{"path_exists": "etc/dangling"}])))
        assert not reports[0].matched

    def test_all_requires_every_fact(self, tmp_path):
        rootfs = self._fixture(tmp_path)
        satisfied = apply_rules(rootfs, load_rules(self._rules(tmp_path, [{
            "all": [{"path_exists": "etc/inittab"}, {"path_exists": "opt/monitor"}],
        }])))
        assert satisfied[0].matched
        unsatisfied = apply_rules(rootfs, load_rules(self._rules(tmp_path, [{
            "all": [{"path_exists": "etc/inittab"}, {"path_exists": "nope/missing"}],
        }])))
        assert not unsatisfied[0].matched

    def test_any_accepts_one_fact(self, tmp_path):
        rootfs = self._fixture(tmp_path, with_monitor=False)
        reports = apply_rules(rootfs, load_rules(self._rules(tmp_path, [{
            "any": [{"path_exists": "opt/monitor"}, {"path_exists": "etc/inittab"}],
        }])))
        assert reports[0].matched

    def test_disjoint_all_scope_still_matches(self, tmp_path):
        """Two independent facts share no file; the rule must still fire."""
        rootfs = self._fixture(tmp_path)
        reports = apply_rules(rootfs, load_rules(self._rules(tmp_path, [{
            "all": [{"path_exists": "etc/inittab"}, {"path_exists": "opt/monitor"}],
        }])), dry_run=False)
        assert reports[0].matched
        assert (rootfs / GUEST_SCRIPT_PATH).exists()


class TestSchemaValidation:
    """Unsupported keys must surface; a rule that quietly does nothing is a trap."""

    def test_unknown_top_level_key_warns(self, tmp_path):
        rules_dir = _write_rule_dir(tmp_path, {
            "id": "a", "description": "", "detect": [{"always": True}],
            "actions": [{"guest_shell": ["true"]}], "retries": 3,
        })
        assert any("retries" in w for w in load_rules(rules_dir)[0].warnings)

    def test_unknown_detect_and_action_keys_warn(self, tmp_path):
        rules_dir = _write_rule_dir(tmp_path, {
            "id": "a", "description": "",
            "detect": [{"file_pattern": "**/monitor", "condition": "executable"}],
            "actions": [{"chmod": {"path": "x"}}],
        })
        warnings = load_rules(rules_dir)[0].warnings
        assert any("file_pattern" in w and "condition" in w for w in warnings)
        assert any("chmod" in w for w in warnings)

    def test_within_is_a_legal_detect_modifier(self, tmp_path):
        rules_dir = _write_rule_dir(tmp_path, {
            "id": "a", "description": "",
            "detect": [{"file_regex": "monitor", "within": "etc/inittab"}],
            "actions": [{"guest_shell": ["true"]}],
        })
        assert load_rules(rules_dir)[0].warnings == []

    def test_project_rules_load_without_warnings(self):
        for rule in load_rules(PROJECT_RULES):
            assert rule.warnings == [], f"{rule.id}: {rule.warnings}"


class TestPostActionVerify:
    def _rule(self, verify: dict) -> Rule:
        return Rule(id="v", description="", stage="service", post_action_verify=verify)

    def test_check_files_reports_missing_required_file(self, tmp_path):
        rootfs = tmp_path / "rootfs"
        rootfs.mkdir()
        results = apply_rules(rootfs, [self._rule(
            {"check_files": [{"path": "opt/goahead/route.txt", "must_exist": True}]})],
            dry_run=False)[0].verify
        assert any("MISSING" in r for r in results)

    def test_check_files_reports_forbidden_file_still_present(self, tmp_path):
        rootfs = tmp_path / "rootfs"
        (rootfs / "bin").mkdir(parents=True)
        (rootfs / "bin" / "diag").write_text("x", encoding="utf-8")
        results = apply_rules(rootfs, [self._rule(
            {"check_files": [{"path": "bin/diag", "must_not_exist": True}]})],
            dry_run=False)[0].verify
        assert any("STILL PRESENT" in r for r in results)

    def test_forbidden_pattern_flags_surviving_line(self, tmp_path):
        rootfs = tmp_path / "rootfs"
        (rootfs / "etc").mkdir(parents=True)
        (rootfs / "etc" / "inittab").write_text("::once:-/bin/monitor\n", encoding="utf-8")
        results = apply_rules(rootfs, [self._rule({"forbidden_patterns": [
            {"pattern": "^[^#]*::once:.*monitor", "in_file": "etc/inittab"}]})],
            dry_run=False)[0].verify
        assert any(r.startswith("FORBIDDEN") for r in results)

    def test_forbidden_pattern_clear_after_fix(self, tmp_path):
        rootfs = tmp_path / "rootfs"
        (rootfs / "etc").mkdir(parents=True)
        (rootfs / "etc" / "inittab").write_text("::once:-/bin/monitor\n", encoding="utf-8")
        rule = Rule(
            id="v", description="", stage="service",
            detect=[{"file_regex": "::once:-/bin/monitor", "within": "etc/inittab"}],
            actions=[{"comment_lines": {"regex": "::once:-/bin/monitor", "prefix": "#IRIS: "}}],
            post_action_verify={"forbidden_patterns": [
                {"pattern": "^[^#]*::once:.*monitor", "in_file": "etc/inittab"}]},
        )
        results = apply_rules(rootfs, [rule], dry_run=False)[0].verify
        assert any("clear in" in r for r in results)

    def test_health_check_is_deferred_into_the_guest_script(self, tmp_path):
        rootfs = tmp_path / "rootfs"
        rootfs.mkdir()
        report = apply_rules(rootfs, [self._rule({"health_check": {
            "command": "ps w | grep monitor", "expected_exit_code": 1}})], dry_run=False)[0]
        assert any("deferred to guest" in r for r in report.verify)
        script = (rootfs / GUEST_SCRIPT_PATH).read_text(encoding="utf-8")
        assert VERIFY_LOG_PATH in script
        assert "IRIS-VERIFY OK" in script and "IRIS-VERIFY FAIL" in script

    def test_verify_is_skipped_in_dry_run(self, tmp_path):
        rootfs = tmp_path / "rootfs"
        rootfs.mkdir()
        report = apply_rules(rootfs, [self._rule(
            {"check_files": [{"path": "x", "must_exist": True}]})], dry_run=True)[0]
        assert report.verify == ["skipped (dry-run)"]

    def test_project_watchdog_rule_verifies_its_own_repair(self, tmp_path):
        """The shipped rule must not report success it cannot back up."""
        root = tmp_path / "olt"
        (root / "etc").mkdir(parents=True)
        (root / "etc" / "inittab").write_text("::once:-/bin/monitor\n", encoding="utf-8")
        report = next(r for r in apply_rules(root, load_rules(PROJECT_RULES), dry_run=False)
                      if r.rule_id == "vendor-watchdog-monitor")
        assert any("clear in etc/inittab" in v for v in report.verify)
        assert not any(v.startswith("FORBIDDEN") for v in report.verify)


class TestGuestScriptIsAscii:
    def test_non_ascii_guest_lines_are_folded(self, tmp_path):
        """A stray arrow or emoji makes the whole script unreadable to a GBK host."""
        rootfs = tmp_path / "rootfs"
        rootfs.mkdir()
        rule = Rule(id="v", description="", stage="service", detect=[{"always": True}],
                    actions=[{"guest_shell": ['echo "renaming -> .iris-disabled"',
                                              'echo "OK done"']}])
        apply_rules(rootfs, [rule], dry_run=False)
        script = (rootfs / GUEST_SCRIPT_PATH).read_text(encoding="utf-8")
        assert script.isascii()
        assert "-> .iris-disabled" in script

class TestGuestScriptIsPosix:
    """The script runs in a Linux chroot; host-OS separators must never reach it.

    Deriving a guest path with ``Path(...)`` renders backslashes on Windows, and
    POSIX sh reads a backslash as an escape — so ``mkdir -p /etc/scripts`` with
    host separators silently creates ``./etcscripts`` and the verdict log lands
    nowhere.
    """

    def test_verify_lines_use_posix_paths(self, tmp_path):
        rule = Rule(id="v", description="", stage="service", detect=[{"always": True}],
                    actions=[], post_action_verify={"health_check": {"command": "true"}})
        lines = _verify_lines(rule, rule.post_action_verify)
        assert f"mkdir -p {VERIFY_LOG_DIR} " in "\n".join(lines)
        assert VERIFY_LOG_DIR == "/etc/scripts"

    def test_generated_script_has_no_backslashes(self, tmp_path):
        rootfs = tmp_path / "rootfs"
        rootfs.mkdir()
        rule = Rule(id="v", description="", stage="service", detect=[{"always": True}],
                    actions=[{"guest_shell": ["mkdir -p /etc/scripts"]}],
                    post_action_verify={"health_check": {"command": "true"}})
        apply_rules(rootfs, [rule], dry_run=False)
        script = (rootfs / GUEST_SCRIPT_PATH).read_text(encoding="utf-8")
        assert "\\" not in script
