"""AI 值守：仿真容器的串口日志健康监控与自愈。

串口日志是唯一全量信号——HTTP 探活看不到被重启掩盖的故障：一个 QEMU 容器可以
端口转发正常、ping 得通，而 guest 内核正在被厂商 watchdog 反复硬复位。

能力：
1. 串口日志模式分析（watchdog 重启 / sysrq / reboot 尝试 / diag 崩溃 /
   soft lockup / Web 启动与存活）
2. 四态健康判定（healthy / degraded / critical / expired）与结构化异常清单
3. 四类自愈动作（watchdog 移除 / 资源回收 / 诊断工具禁用 / Web 排查）

设计约束：自愈动作一律通过 ``docker exec -i`` 把脚本从 stdin 送进容器，
不落任何中间文件——写宿主再让容器去找，是取不到文件的经典写法。
动作的成败以容器内命令的退出码为准，而不是"跑过了就算"；只做排查、不做修复的
结论单独记在 ``diagnoses``，不混进 ``actions_taken``。

CLI 入口是 ``iris emulate guardian-start``，本模块不自带命令行。
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

from iris.log import get_logger

logger = get_logger(__name__)

CONTAINER_PREFIX = "iris-qemu-"

#: Recovery actions, in the order ``recommend_recovery_action`` prefers them.
ACTION_WATCHDOG = "WATCHDOG_RECOVERY"
ACTION_RESOURCE = "RESOURCE_CLEANUP"
ACTION_DIAGNOSTIC = "DIAGNOSTIC_DISABLEMENT"
ACTION_WEB_DIAGNOSIS = "WEB_SERVER_DIAGNOSIS"


@dataclass
class ContainerHealthStatus:
    """Container health metrics and state."""

    iid: int
    #: healthy | degraded | critical | expired | unknown
    state: str = "unknown"
    uptime_seconds: float = 0.0
    reboot_count: int = 0
    watchdog_triggers: int = 0
    diag_crashes: int = 0
    soft_lockup_events: int = 0
    #: active | started_but_stopped | not_started | unknown
    web_server_status: str = "unknown"
    last_check: datetime = field(default_factory=lambda: datetime.now(UTC))
    anomalies: list[str] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    diagnoses: list[str] = field(default_factory=list)


class SerialLogAnalyzer:
    """Analyzes QEMU serial logs for failure patterns."""

    PATTERNS: ClassVar[dict[str, str]] = {
        'watchdog_reboot': r'Monitor:\s*process\s+\w+\s+is\s+die',
        'sysrq_reset': r'sysrq:\s*Resetting|echo.*b.*proc/sysrq-trigger',
        'reboot_attempt': r'reboot:\s*not found|reboot triggered',
        'diag_crash': r'diag:\s*.*signal\s+11|SIGSEGV',
        'soft_lockup': r'watchdog:\s*BUG:\s*soft lockup.*CPU#?\d+',
        'web_server_start': r'(goahead|boa|lighttpd|uhttpd|thttpd|httpd)[:\s].*'
                            r'(starting|launch|start)|launching\s+(goahead|boa)|'
                            r'probing for a web server',
        # A live web server shows up either as IRIS's own hand-off line or as the
        # vendor process actually claiming :80. Matching only the hand-off line
        # reported "no web server" on guests that booted their own server.
        'web_server_active': r'vendor web server is already running|'
                             r'listening on[^\n]*:80|'
                             r'(goahead|boa|lighttpd|uhttpd|thttpd|httpd)[:\s]'
                             r'[^\n]*(listen|bind)',
    }

    def __init__(self, log_path: Path):
        self.log_path = Path(log_path)
        self.lines: list[str] = []

    def load_log(self) -> bool:
        """Load serial log content.

        Serial output is raw bytes off a UART: UTF-8 with ``replace`` keeps the
        readable part instead of raising on the first stray byte, which is what a
        bare ``readlines()`` under a Windows locale would do.
        """
        try:
            self.lines = self.log_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines(keepends=True)
            return True
        except OSError as exc:
            logger.warning(f"failed to read serial log {self.log_path}: {exc}")
            return False

    def _ensure_loaded(self) -> None:
        if not self.lines:
            self.load_log()

    def count_pattern_occurrences(self, pattern_name: str) -> int:
        """Count occurrences of a specific failure pattern."""
        pattern = self.PATTERNS.get(pattern_name)
        if not pattern:
            return 0
        self._ensure_loaded()
        flags = re.IGNORECASE | re.MULTILINE
        return sum(1 for line in self.lines if re.search(pattern, line, flags))

    def get_boot_sequence_timeline(self) -> list[int]:
        """Line numbers of every kernel boot banner in the log."""
        self._ensure_loaded()
        return [i for i, line in enumerate(self.lines, 1)
                if 'Booting Linux on physical CPU' in line]

    def get_latest_crash_context(self, window: int = 5) -> str | None:
        """Text around the most recent error-looking line, for the health report.

        The counts say *how many*; this says *what it looked like*, which is the
        part that makes a report actionable rather than merely alarming.
        """
        self._ensure_loaded()
        needles = ('error', 'fail', 'crash', 'signal', 'die', 'not found')
        for i in range(len(self.lines) - 1, max(-1, len(self.lines) - 100), -1):
            if any(kw in self.lines[i].lower() for kw in needles):
                start, end = max(0, i - window), min(len(self.lines), i + window + 1)
                return "".join(self.lines[start:end]).rstrip()
        return None


class AIHealthMonitor:
    """Health analysis and self-healing for one emulation container."""

    def __init__(self, iid: int, scratch_dir: Path, timeout_minutes: int = 60):
        self.iid = iid
        self.scratch_dir = Path(scratch_dir)
        self.timeout = timedelta(minutes=timeout_minutes)
        self.status = ContainerHealthStatus(iid=iid)
        self.analyzer: SerialLogAnalyzer | None = None
        self.start_time = datetime.now(UTC)
        self.recovery_history: list[dict] = []

    # ------------------------------------------------------------------ logs

    def _get_serial_log_path(self) -> Path | None:
        """Locate the serial log, falling back to a copy-out of the container."""
        candidates = [
            self.scratch_dir / str(self.iid) / "qemu.serial.log",
            self.scratch_dir / f"emulate-{self.iid}" / "qemu.serial.log",
            self.scratch_dir / "qemu.serial.log",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate

        # The guest writes it, but the orchestration also pulls it back on exit;
        # if the container is still up the copy-out is the freshest source.
        try:
            proc = subprocess.run(
                ["docker", "cp",
                 f"{CONTAINER_PREFIX}{self.iid}:/work/scratch/{self.iid}/qemu.serial.log",
                 str(self.scratch_dir)],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning(f"serial log copy-out failed for {self.iid}: {exc}")
            return None
        if proc.returncode != 0:
            return None
        copied = self.scratch_dir / "qemu.serial.log"
        return copied if copied.is_file() else None

    # --------------------------------------------------------------- analysis

    def analyze_health(self) -> ContainerHealthStatus:
        """Perform one full health analysis pass."""
        self.status.uptime_seconds = (datetime.now(UTC) - self.start_time).total_seconds()

        log_path = self._get_serial_log_path()
        if not log_path or not log_path.is_file():
            logger.warning(f"no serial log found for container {self.iid}")
            self.status.state = "unknown"
            self.status.web_server_status = "unknown"
            return self.status

        self.analyzer = SerialLogAnalyzer(log_path)
        if not self.analyzer.load_log():
            self.status.state = "unknown"
            return self.status

        self.status.watchdog_triggers = self.analyzer.count_pattern_occurrences('watchdog_reboot')
        self.status.diag_crashes = self.analyzer.count_pattern_occurrences('diag_crash')
        self.status.soft_lockup_events = self.analyzer.count_pattern_occurrences('soft_lockup')

        boots = self.analyzer.get_boot_sequence_timeline()
        # One banner is the initial boot; every extra one is the guest restarting.
        self.status.reboot_count = max(0, len(boots) - 1)

        if self.analyzer.count_pattern_occurrences('web_server_active') > 0:
            self.status.web_server_status = "active"
        elif self.analyzer.count_pattern_occurrences('web_server_start') > 0:
            self.status.web_server_status = "started_but_stopped"
        else:
            self.status.web_server_status = "not_started"

        self.status.anomalies.clear()
        self._determine_overall_state()
        self._log_status_report()
        return self.status

    def _determine_overall_state(self) -> None:
        """Fold the metrics into one state.

        ``expired`` is checked first, and it outranks *every* counter, critical
        included. The watchdog/reboot counters are recomputed from the whole log
        on each pass, so they are history, not current state: a guest that hit
        its watchdog once, got fixed, and then sat quietly until the window
        closed would otherwise report critical forever. Past the window,
        "still not healthy" is the verdict — whether the last stretch was
        degraded or merely quiet.
        """
        self.status.anomalies.clear()
        s = self.status
        if s.uptime_seconds > self.timeout.total_seconds():
            s.state = "expired"
            s.anomalies.append(
                f"no healthy state within {int(self.timeout.total_seconds() // 60)} minutes"
            )
        elif s.reboot_count >= 3 or s.watchdog_triggers >= 2:
            s.state = "critical"
            s.anomalies.append(
                f"repeated restarts: {s.reboot_count} reboot(s), "
                f"{s.watchdog_triggers} watchdog trigger(s)"
            )
        elif s.soft_lockup_events > 0:
            s.state = "degraded"
            s.anomalies.append(f"CPU soft lockup x{s.soft_lockup_events}")
        elif s.diag_crashes > 0:
            s.state = "degraded"
            s.anomalies.append(f"diagnostic tool crash x{s.diag_crashes}")
        elif s.web_server_status == "not_started":
            s.state = "degraded"
            s.anomalies.append("web server never started")
        else:
            s.state = "healthy"

    def _log_status_report(self) -> None:
        s = self.status
        logger.info(
            f"container {s.iid} health: {s.state.upper()} | "
            f"uptime {s.uptime_seconds:.0f}s reboots {s.reboot_count} "
            f"watchdog {s.watchdog_triggers} diag {s.diag_crashes} "
            f"lockup {s.soft_lockup_events} web {s.web_server_status}",
            anomalies=s.anomalies or ["none"],
        )
        if s.anomalies and self.analyzer:
            context = self.analyzer.get_latest_crash_context()
            if context:
                logger.info(f"container {s.iid} latest failure context:\n{context}")
        for entry in self.recovery_history[-3:]:
            logger.info(f"container {s.iid} recovery: {entry}")

    def recommend_recovery_action(self) -> str | None:
        """Recommend the action matching the strongest detected signal."""
        s = self.status
        if s.watchdog_triggers > 0:
            return ACTION_WATCHDOG
        if s.soft_lockup_events > 0:
            return ACTION_RESOURCE
        if s.diag_crashes > 0:
            return ACTION_DIAGNOSTIC
        if s.web_server_status == "not_started":
            return ACTION_WEB_DIAGNOSIS
        return None

    # ---------------------------------------------------------------- recovery

    def _exec_in_guest(self, script: str, timeout: int = 30) -> tuple[bool, str]:
        """Run a shell snippet inside the running container.

        The snippet is piped in over stdin (``docker exec -i ... sh -s``), so
        nothing has to be copied first and nothing can end up in the wrong
        filesystem. Returns the guest's own exit status: a repair that silently
        failed must not be recorded as a repair that succeeded.
        """
        try:
            proc = subprocess.run(
                ["docker", "exec", "-i", f"{CONTAINER_PREFIX}{self.iid}", "/bin/sh", "-s"],
                input=script, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error(f"container {self.iid}: guest exec failed: {exc}")
            return False, str(exc)
        return proc.returncode == 0, ((proc.stdout or "") + (proc.stderr or "")).strip()

    def execute_recovery(self, action: str) -> bool:
        """Run a recommended recovery action and record the outcome."""
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        handlers = {
            ACTION_WATCHDOG: self._apply_watchdog_fixes,
            ACTION_RESOURCE: self._cleanup_resources,
            ACTION_DIAGNOSTIC: self._disable_diagnostic_tools,
        }
        if action == ACTION_WEB_DIAGNOSIS:
            # Diagnosis only: nothing is repaired, so it must not be logged as a
            # repair — otherwise the report claims a fix that never happened.
            ok, summary = self._diagnose_web_server()
            entry = f"{ACTION_WEB_DIAGNOSIS}@{stamp}"
            self.status.diagnoses.append(entry)
            self.recovery_history.append(
                {"action": action, "timestamp": stamp, "success": ok, "detail": summary}
            )
            logger.info(f"[{stamp}] {ACTION_WEB_DIAGNOSIS}: {summary or 'no findings'}")
            return ok

        handler = handlers.get(action)
        if handler is None:
            logger.error(f"[{stamp}] unknown recovery action: {action}")
            return False

        logger.info(f"[{stamp}] executing {action} on container {self.iid}")
        success = handler()
        if success:
            entry = f"{action}@{stamp}"
            self.status.actions_taken.append(entry)
            self.recovery_history.append(
                {"action": action, "timestamp": stamp, "success": True}
            )
        else:
            self.recovery_history.append(
                {"action": action, "timestamp": stamp, "success": False}
            )
            logger.warning(f"[{stamp}] {action} did not take effect on {self.iid}")
        return success

    _WATCHDOG_SCRIPT = """\
for b in /bin/monitor /sbin/monitor /usr/bin/monitor /usr/sbin/monitor \\
        /opt/monitor /opt/bin/monitor /bin/watchdog /sbin/watchdog \\
        /bin/monitord /bin/arp_monitor /bin/ppp-monitor; do
  [ -e "$b" ] || [ -L "$b" ] || continue
  if [ -L "$b" ]; then rm -f "$b"; else mv -f "$b" "$b.iris-disabled"; fi
done
for d in /bin /sbin /usr/bin /usr/sbin /opt/bin; do
  [ -d "$d" ] || continue
  find "$d" -maxdepth 1 -type l \\
    \\( -name '*monitor*' -o -name '*watchdog*' \\) -exec rm -f {} \\; 2>/dev/null
done
echo WATCHDOG-FIX-APPLIED
exit 0
"""

    def _apply_watchdog_fixes(self) -> bool:
        """Rename supervision daemons out of the way inside the guest."""
        ok, out = self._exec_in_guest(self._WATCHDOG_SCRIPT, timeout=45)
        applied = "WATCHDOG-FIX-APPLIED" in out
        if ok and applied:
            logger.info(f"container {self.iid}: watchdog binaries disabled")
            return True
        logger.warning(
            f"container {self.iid}: watchdog fix not confirmed "
            f"(rc={'0' if ok else 'nonzero'}, applied={applied})"
        )
        return False

    _CLEANUP_SCRIPT = """\
for p in monitord arp_monitor ppp-monitor; do
  pidof "$p" >/dev/null 2>&1 && kill -9 $(pidof "$p") 2>/dev/null
done
sync
echo RESOURCE-CLEANUP-APPLIED
exit 0
"""

    def _cleanup_resources(self) -> bool:
        """Free CPU held by runaway monitor processes."""
        ok, out = self._exec_in_guest(self._CLEANUP_SCRIPT, timeout=20)
        return ok and "RESOURCE-CLEANUP-APPLIED" in out

    _DIAG_SCRIPT = """\
rc=1
for d in /bin/diag /usr/bin/diag /sbin/diag; do
  [ -e "$d" ] || continue
  mv -f "$d" "$d.iris-disabled" 2>/dev/null && rc=0
done
sync
[ "$rc" = 0 ] && echo DIAG-DISABLED
exit "$rc"
"""

    def _disable_diagnostic_tools(self) -> bool:
        """Rename the crashing diagnostic tool.

        Exits non-zero when nothing was there to disable, so a guest without a
        diag binary is reported as "nothing to do" rather than a silent success.
        """
        ok, out = self._exec_in_guest(self._DIAG_SCRIPT, timeout=20)
        if ok and "DIAG-DISABLED" in out:
            logger.info(f"container {self.iid}: diag disabled")
            return True
        logger.info(f"container {self.iid}: no diag binary needed disabling")
        return False

    def _diagnose_web_server(self) -> tuple[bool, str]:
        """Collect why the web server is not serving. Reports; repairs nothing."""
        script = """\
for b in /opt/goahead/goahead /usr/bin/boa /bin/boa /usr/sbin/lighttpd \\
         /usr/sbin/uhttpd /usr/sbin/thttpd; do
  [ -e "$b" ] && echo "binary: $b"
done
pidof goahead >/dev/null 2>&1 && echo "running: goahead"
pidof boa >/dev/null 2>&1 && echo "running: boa"
netstat -lnt 2>/dev/null | grep -q ':80 ' && echo "listening: 80"
echo "init references:"
grep -rIl -e goahead -e boa /etc/init.d /etc/rc.d 2>/dev/null
echo "prerequisites:"
[ -f /opt/goahead/route.txt ] && echo "  ok: /opt/goahead/route.txt"
[ -f /etc/boa/boa.conf ] && echo "  ok: /etc/boa/boa.conf"
exit 0
"""
        ok, out = self._exec_in_guest(script, timeout=20)
        summary = out or "no diagnostics collected"
        if "listening: 80" in out and "running:" in out:
            summary = f"web server is up after all: {summary}"
        elif "binary:" not in out:
            summary = f"no vendor web binary found in this guest: {summary}"
        return ok, summary

    # ------------------------------------------------------------------- loop

    def start_continuous_monitoring(self, check_interval: int = 30) -> None:
        """Analyse, recover, repeat until interrupted."""
        logger.info(
            f"AI Guardian watching container {self.iid} every {check_interval}s "
            f"(timeout {int(self.timeout.total_seconds() // 60)}m)"
        )
        while True:
            try:
                self.status.uptime_seconds = (datetime.now(UTC) - self.start_time).total_seconds()
                status = self.analyze_health()
                if status.state != "healthy":
                    action = self.recommend_recovery_action()
                    if action:
                        logger.warning(
                            f"container {self.iid}: {status.state} -> attempting {action}"
                        )
                        self.execute_recovery(action)
                time.sleep(check_interval)
            except KeyboardInterrupt:
                logger.info(f"AI Guardian stopped monitoring container {self.iid}")
                return
            except Exception as exc:  # noqa: BLE001 - a watch loop must outlive any single failure
                # A transient failure must not end the watch; back off and retry.
                logger.error(f"monitoring error for container {self.iid}: {exc}")
                time.sleep(60)
