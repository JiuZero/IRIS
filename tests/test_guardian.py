"""Tests for iris.monitor.ai_guardian — serial-log analysis and guest recovery.

Recovery actions shell out to Docker, so the exec layer is stubbed: what matters
is that the script reaches the guest as stdin and that the guest's own exit status
decides success, never "we ran something and assumed it worked".
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from iris.monitor.ai_guardian import (
    ACTION_DIAGNOSTIC,
    ACTION_RESOURCE,
    ACTION_WATCHDOG,
    ACTION_WEB_DIAGNOSIS,
    AIHealthMonitor,
    SerialLogAnalyzer,
)

# A trimmed capture of the real TES7002 failure cycle: monitor notices the
# hardware-bound process is gone, reboot is missing, sysrq hard-resets, and the
# diag tool segfaults in a loop.
TES702_LOG = """\
[    0.000000] Linux version 6.1.0
[    0.010000] Booting Linux on physical CPU 0x0000000000 [0000] fp:0d:...
/opt/goahead/goahead --home /opt/goahead
IRIS-NETFIX: probing for a web server on :80
IRIS-NETFIX: vendor web server is already running, leaving :80 to it
Monitor: process gp8 is die.
sh: reboot: not found
sysrq: Resetting
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0000] fp:0d:...
diag: diag: potentially unexpected fatal signal 11.
Monitor: process swg is die.
sh: reboot: not found
sysrq: Resetting
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0000] fp:0d:...
diag: diag: potentially unexpected fatal signal 11.
diag: diag: potentially unexpected fatal signal 11.
"""


@pytest.fixture
def serial_log(tmp_path: Path) -> Path:
    log = tmp_path / "qemu.serial.log"
    log.write_text(TES702_LOG, encoding="utf-8")
    return log


@pytest.fixture
def monitor(tmp_path: Path, serial_log: Path) -> AIHealthMonitor:
    (tmp_path / "10001").mkdir()
    (tmp_path / "10001" / "qemu.serial.log").write_text(
        TES702_LOG, encoding="utf-8")
    return AIHealthMonitor(iid=10001, scratch_dir=tmp_path)


class FakeExec:
    """Stand-in for ``docker exec`` recording what the guardian tried to run."""

    def __init__(self, stdout: str = "", returncode: int = 0, raise_exc: bool = False):
        self.stdout = stdout
        self.returncode = returncode
        self.raise_exc = raise_exc
        self.calls: list[list[str]] = []
        self.scripts: list[str] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        self.scripts.append(kwargs.get("input", ""))
        if self.raise_exc:
            raise subprocess.TimeoutExpired(cmd, 30)
        if isinstance(cmd, list) and cmd[0] == "cp":
            return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, "")
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, "")

    @property
    def exec_call(self) -> list[str]:
        return next(c for c in self.calls if "exec" in c)


class TestSerialLogAnalyzer:
    def test_counts_the_real_failure_signatures(self, serial_log):
        a = SerialLogAnalyzer(serial_log)
        assert a.load_log()
        assert a.count_pattern_occurrences("watchdog_reboot") == 2
        assert a.count_pattern_occurrences("reboot_attempt") == 2
        assert a.count_pattern_occurrences("sysrq_reset") == 2
        assert a.count_pattern_occurrences("diag_crash") == 3

    def test_boot_timeline_minus_one_is_the_reboot_count(self, serial_log):
        a = SerialLogAnalyzer(serial_log)
        a.load_log()
        timeline = a.get_boot_sequence_timeline()
        assert len(timeline) == 3
        assert timeline == sorted(timeline)

    def test_single_boot_means_no_reboots(self, tmp_path):
        log = tmp_path / "clean.log"
        log.write_text("[ 0.0] Booting Linux on physical CPU 0x0 [0000]\n",
                       encoding="utf-8")
        a = SerialLogAnalyzer(log)
        a.load_log()
        assert len(a.get_boot_sequence_timeline()) == 1

    def test_counts_are_case_insensitive(self, tmp_path):
        log = tmp_path / "l.log"
        log.write_text("MONITOR: PROCESS gp8 IS DIE.\n", encoding="utf-8")
        a = SerialLogAnalyzer(log)
        a.load_log()
        assert a.count_pattern_occurrences("watchdog_reboot") == 1

    def test_unknown_pattern_counts_zero(self, serial_log):
        a = SerialLogAnalyzer(serial_log)
        a.load_log()
        assert a.count_pattern_occurrences("nonexistent") == 0

    def test_soft_lockup_detected(self, tmp_path):
        log = tmp_path / "l.log"
        log.write_text(
            "watchdog: BUG: soft lockup - CPU#0 stuck for 22s\n", encoding="utf-8")
        a = SerialLogAnalyzer(log)
        a.load_log()
        assert a.count_pattern_occurrences("soft_lockup") == 1

    def test_web_server_active_sees_vendor_owned_server(self, tmp_path, serial_log):
        """Must not read the IRIS hand-off line as the only evidence of a web server."""
        a = SerialLogAnalyzer(serial_log)
        a.load_log()
        assert a.count_pattern_occurrences("web_server_active") == 1

        vendor_only = tmp_path / "v.log"
        vendor_only.write_text("goahead: listening on port 80\n", encoding="utf-8")
        b = SerialLogAnalyzer(vendor_only)
        b.load_log()
        assert b.count_pattern_occurrences("web_server_active") == 1
        assert b.count_pattern_occurrences("web_server_start") == 0

    def test_crash_context_returns_the_offending_lines(self, serial_log):
        a = SerialLogAnalyzer(serial_log)
        a.load_log()
        context = a.get_latest_crash_context(window=2)
        assert context is not None
        assert "signal 11" in context

    def test_crash_context_none_when_log_is_clean(self, tmp_path):
        log = tmp_path / "clean.log"
        log.write_text("all quiet\nnothing to see\n", encoding="utf-8")
        a = SerialLogAnalyzer(log)
        a.load_log()
        assert a.get_latest_crash_context() is None

    def test_non_utf8_bytes_do_not_break_loading(self, tmp_path):
        """Serial output is raw UART bytes; a stray one must not abort the read."""
        log = tmp_path / "l.log"
        log.write_bytes(b"Monitor: process gp8 is die.\n\xff\xfe garbage\n")
        a = SerialLogAnalyzer(log)
        assert a.load_log()
        assert a.count_pattern_occurrences("watchdog_reboot") == 1

    def test_missing_log_reports_failure(self, tmp_path):
        a = SerialLogAnalyzer(tmp_path / "nope.log")
        assert not a.load_log()
        assert a.count_pattern_occurrences("diag_crash") == 0


class TestAnalyzeHealth:
    def test_watchdog_cycle_is_critical(self, monitor):
        status = monitor.analyze_health()
        assert status.state == "critical"
        assert status.reboot_count == 2
        assert status.watchdog_triggers == 2
        assert status.web_server_status == "active"
        assert status.anomalies

    def test_missing_log_yields_unknown_not_healthy(self, tmp_path):
        m = AIHealthMonitor(iid=4242, scratch_dir=tmp_path)
        status = m.analyze_health()
        assert status.state == "unknown"
        assert status.web_server_status == "unknown"

    def test_uptime_is_measured_outside_the_loop(self, monitor):
        from datetime import timedelta

        monitor.start_time = datetime.now() - timedelta(seconds=90)
        monitor.analyze_health()
        assert monitor.status.uptime_seconds >= 90

    def test_anomalies_do_not_accumulate_across_passes(self, monitor):
        monitor.analyze_health()
        first = list(monitor.status.anomalies)
        monitor.analyze_health()
        assert monitor.status.anomalies == first

    def test_soft_lockup_is_degraded(self, tmp_path, monkeypatch):
        (tmp_path / "1").mkdir()
        (tmp_path / "1" / "qemu.serial.log").write_text(
            "[ 0.0] Booting Linux on physical CPU 0x0\n"
            "watchdog: BUG: soft lockup - CPU#0 stuck for 22s\n",
            encoding="utf-8")
        status = AIHealthMonitor(iid=1, scratch_dir=tmp_path).analyze_health()
        assert status.state == "degraded"
        assert "soft lockup" in status.anomalies[0]

    def test_diag_crashes_are_degraded(self, tmp_path):
        (tmp_path / "1").mkdir()
        (tmp_path / "1" / "qemu.serial.log").write_text(
            "[ 0.0] Booting Linux on physical CPU 0x0\n"
            "diag: diag: potentially unexpected fatal signal 11.\n"
            "IRIS-NETFIX: probing for a web server on :80\n",
            encoding="utf-8")
        status = AIHealthMonitor(iid=1, scratch_dir=tmp_path).analyze_health()
        assert status.state == "degraded"
        assert status.diag_crashes == 1
        assert status.web_server_status == "started_but_stopped"

    def test_healthy_when_nothing_wrong(self, tmp_path):
        (tmp_path / "1").mkdir()
        (tmp_path / "1" / "qemu.serial.log").write_text(
            "[ 0.0] Booting Linux on physical CPU 0x0\n"
            "IRIS-NETFIX: vendor web server is already running, leaving :80 to it\n",
            encoding="utf-8")
        status = AIHealthMonitor(iid=1, scratch_dir=tmp_path).analyze_health()
        assert status.state == "healthy"
        assert status.anomalies == []

    def test_expired_wins_over_degraded_once_the_window_passes(self, tmp_path):
        """Past the window, "never came up" is a verdict, not a lingering warning."""
        (tmp_path / "1").mkdir()
        (tmp_path / "1" / "qemu.serial.log").write_text(
            "[ 0.0] Booting Linux on physical CPU 0x0\n", encoding="utf-8")
        m = AIHealthMonitor(iid=1, scratch_dir=tmp_path, timeout_minutes=60)
        m.status.uptime_seconds = 61 * 60
        m._determine_overall_state()
        assert m.status.state == "expired"
        assert "web server never started" not in m.status.anomalies


class TestRecommendAction:
    @pytest.mark.parametrize("attr,expected", [
        ("watchdog_triggers", ACTION_WATCHDOG),
        ("soft_lockup_events", ACTION_RESOURCE),
        ("diag_crashes", ACTION_DIAGNOSTIC),
        ("web_server_status", ACTION_WEB_DIAGNOSIS),
    ])
    def test_priority_order(self, tmp_path, attr, expected):
        m = AIHealthMonitor(iid=1, scratch_dir=tmp_path)
        setattr(m.status, attr, 1 if attr != "web_server_status" else "not_started")
        assert m.recommend_recovery_action() == expected

    def test_healthy_needs_no_action(self, tmp_path):
        m = AIHealthMonitor(iid=1, scratch_dir=tmp_path)
        m.status.web_server_status = "active"
        assert m.recommend_recovery_action() is None


class TestGuestExec:
    def test_script_is_piped_to_stdin_not_written_to_disk(self, monitor, monkeypatch):
        """The earlier version wrote to the host's /tmp, then the guest never saw it."""
        fake = FakeExec(stdout="WATCHDOG-FIX-APPLIED\n")
        monkeypatch.setattr(subprocess, "run", fake)
        monitor._apply_watchdog_fixes()
        assert fake.exec_call[:4] == ["docker", "exec", "-i", "iris-qemu-10001"]
        assert fake.scripts[0].strip().startswith("for b in")
        assert "/tmp" not in fake.exec_call

    def test_docker_failure_is_reported_not_raised(self, monitor, monkeypatch):
        monkeypatch.setattr(subprocess, "run", FakeExec(raise_exc=True))
        assert monitor._apply_watchdog_fixes() is False

    def test_script_running_but_not_confirming_counts_as_failure(self, monitor, monkeypatch):
        """A zero exit code alone must not be mistaken for a working repair."""
        monkeypatch.setattr(subprocess, "run", FakeExec(stdout="", returncode=0))
        assert monitor._apply_watchdog_fixes() is False

    def test_watchdog_fix_confirmed_by_marker(self, monitor, monkeypatch):
        monkeypatch.setattr(subprocess, "run",
                            FakeExec(stdout="WATCHDOG-FIX-APPLIED\n"))
        assert monitor._apply_watchdog_fixes() is True

    def test_diag_disabled_confirmed_by_marker(self, monitor, monkeypatch):
        monkeypatch.setattr(subprocess, "run", FakeExec(stdout="DIAG-DISABLED\n"))
        assert monitor._disable_diagnostic_tools() is True

    def test_diag_absent_is_not_a_silent_success(self, monitor, monkeypatch):
        """Nothing to disable is 'no-op', not 'repaired'."""
        monkeypatch.setattr(subprocess, "run",
                            FakeExec(stdout="", returncode=1))
        assert monitor._disable_diagnostic_tools() is False

    def test_cleanup_confirmed_by_marker(self, monitor, monkeypatch):
        monkeypatch.setattr(subprocess, "run",
                            FakeExec(stdout="RESOURCE-CLEANUP-APPLIED\n"))
        assert monitor._cleanup_resources() is True


class TestExecuteRecovery:
    def test_successful_repair_is_recorded(self, monitor, monkeypatch):
        monkeypatch.setattr(subprocess, "run",
                            FakeExec(stdout="WATCHDOG-FIX-APPLIED\n"))
        assert monitor.execute_recovery(ACTION_WATCHDOG)
        assert len(monitor.status.actions_taken) == 1
        assert monitor.status.actions_taken[0].startswith(ACTION_WATCHDOG)
        assert monitor.recovery_history[-1]["success"] is True

    def test_failed_repair_is_not_recorded_as_taken(self, monitor, monkeypatch):
        monkeypatch.setattr(subprocess, "run", FakeExec(stdout="", returncode=1))
        assert not monitor.execute_recovery(ACTION_WATCHDOG)
        assert monitor.status.actions_taken == []
        assert monitor.recovery_history[-1]["success"] is False

    def test_web_diagnosis_does_not_claim_a_repair(self, monitor, monkeypatch):
        """It repairs nothing, so it must not land in actions_taken."""
        monkeypatch.setattr(subprocess, "run", FakeExec(stdout="binary: /opt/goahead/goahead\n"))
        assert monitor.execute_recovery(ACTION_WEB_DIAGNOSIS)
        assert monitor.status.actions_taken == []
        assert len(monitor.status.diagnoses) == 1
        assert "goahead" in monitor.recovery_history[-1]["detail"]

    def test_diagnosis_notes_a_missing_binary(self, monitor, monkeypatch):
        monkeypatch.setattr(subprocess, "run", FakeExec(stdout="init references:\n"))
        monitor.execute_recovery(ACTION_WEB_DIAGNOSIS)
        assert "no vendor web binary" in monitor.recovery_history[-1]["detail"]

    def test_unknown_action_is_rejected(self, monitor, monkeypatch):
        monkeypatch.setattr(subprocess, "run", FakeExec())
        assert monitor.execute_recovery("NOT_AN_ACTION") is False


class TestMonitoringLoop:
    def test_recovers_then_stops_on_keyboard_interrupt(self, monitor, monkeypatch):
        import iris.monitor.ai_guardian as mod

        seen: list[str] = []

        def fake_analyze():
            seen.append(monitor.status.state)
            if len(seen) == 1:
                monitor.status.state = "degraded"
                monitor.status.watchdog_triggers = 1
            else:
                raise KeyboardInterrupt
            return monitor.status

        monkeypatch.setattr(monitor, "analyze_health", fake_analyze)
        monkeypatch.setattr(subprocess, "run",
                            FakeExec(stdout="WATCHDOG-FIX-APPLIED\n"))
        monkeypatch.setattr(mod.time, "sleep", lambda _: None)

        monitor.start_continuous_monitoring(check_interval=0)
        assert seen == ["unknown", "degraded"]
        assert monitor.status.actions_taken

    def test_transient_error_does_not_end_the_watch(self, monitor, monkeypatch):
        import iris.monitor.ai_guardian as mod

        calls = {"n": 0}

        def flaky_analyze():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            raise KeyboardInterrupt

        monkeypatch.setattr(monitor, "analyze_health", flaky_analyze)
        monkeypatch.setattr(mod.time, "sleep", lambda _: None)

        monitor.start_continuous_monitoring(check_interval=0)
        assert calls["n"] == 2