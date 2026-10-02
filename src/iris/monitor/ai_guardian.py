"""AI 值守：仿真容器的串口日志健康监控与自愈。

串口日志是唯一全量信号——HTTP 探活看不到被重启掩盖的故障：一个 QEMU 仿真容器可以
端口转发正常、ping 得通，而 guest 内核正在被厂商 watchdog 反复硬复位。
反过来串口日志也会说谎：它记录的是启动时刻的状态（"vendor web server is already
running"），不代表此刻 :80 还有人应答。所以监控期补了第二信号源——对转发端口的
HTTP 探活，探活失败会推翻串口日志的乐观结论。

能力：
1. 串口日志模式分析（watchdog 重启 / sysrq / reboot 尝试 / diag 崩溃 /
   soft lockup / Web 启动与存活），按增量窗口计数——只统计上次分析之后
   新增的行，历史告警不会永远钉住状态
2. 五态健康判定（healthy / degraded / critical / expired / unknown）与结构化异常清单
3. 五类恢复动作（watchdog 移除 / 资源回收 / 诊断工具禁用 / Web 排查 / Web 重启）

设计约束：自愈动作一律通过 ``docker exec -i`` 把脚本从 stdin 送进容器，
不落任何中间文件——写宿主再让容器去找，是取不到文件的经典写法。
动作的成败以命令自身的退出码为准，而不是"跑过了就算"；只做排查、不做修复的
结论单独记在 ``diagnoses``，不混进 ``actions_taken``。

执行通道有两层，修复必须落在对的层：
- guest 内（docker exec）：guest 的 rootfs 在 image.raw 里，容器内**没有**它的
  挂载，所以容器内 exec 看不到 guest 的进程和文件——它只对镜像构建期
  （chroot）有效；
- 容器层（docker restart）：运行期唯一真正触达 guest 的修复是重启整个仿真
  容器，QEMU 与 guest 一并重来。``WEB_SERVER_RESTART`` 即此，且必须以探活
  复验收尾——重启了但 Web 没回来不算修复。

CLI 入口是 ``iris emulate guardian-start``，本模块不自带命令行。
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from iris.log import get_logger

if TYPE_CHECKING:
    from iris.monitor.ledger import GuardianLedger

logger = get_logger(__name__)

CONTAINER_PREFIX = "iris-qemu-"
#: Where the guardian ledger lives by default, relative to the scratch dir.
LEDGER_FILENAME = "guardian_ledger.sqlite3"

#: Recovery actions, in the order ``recommend_recovery_action`` prefers them.
ACTION_WATCHDOG = "WATCHDOG_RECOVERY"
ACTION_RESOURCE = "RESOURCE_CLEANUP"
ACTION_DIAGNOSTIC = "DIAGNOSTIC_DISABLEMENT"
ACTION_WEB_DIAGNOSIS = "WEB_SERVER_DIAGNOSIS"
#: The container-level restart: the one repair that actually reaches a running
#: guest, because the guest rootfs is inside image.raw and nothing mounted in
#: the container can touch its processes or filesystem.
ACTION_WEB_RESTART = "WEB_SERVER_RESTART"


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
        # Three ways a reboot reaches the log, and the original pattern matched
        # none of them on the firmware that actually reboots: `rm /sbin/reboot`
        # only removes the symlink, so a vendor daemon calling reboot(2) still
        # reaches the kernel, and the FirmAE kernel's own printk is the only
        # evidence left. Missing that line is what let a 28-deep reboot loop run
        # to the full boot timeout undiagnosed.
        'reboot_attempt': r'reboot:\s*not found|reboot triggered|'
                           r'firmadyne:\s*sys_reboot|Sent SIGTERM to all processes',
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
        self._loaded = False

    def load_log(self, start_line: int = 0) -> bool:
        """Load serial log content, optionally skipping an already-read prefix.

        Serial output is raw bytes off a UART: UTF-8 with ``replace`` keeps the
        readable part instead of raising on the first stray byte, which is what a
        bare ``readlines()`` under a Windows locale would do. ``start_line`` lets
        a continuous watcher count only the *new* tail of an appending log —
        the full-log counts are history, and a repaired guest must not stay
        pinned at critical by them.
        """
        try:
            self.lines = self.log_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines(keepends=True)[max(0, start_line):]
            self._loaded = True
            return True
        except OSError as exc:
            logger.warning(f"failed to read serial log {self.log_path}: {exc}")
            return False

    def total_line_count(self) -> int:
        """Number of lines the log currently holds, without caching content."""
        try:
            with self.log_path.open(encoding="utf-8", errors="replace") as fh:
                return sum(1 for _ in fh)
        except OSError:
            return 0

    def _ensure_loaded(self) -> None:
        # An empty window is a *consumed* log, not an unread one: reloading
        # from line 0 here would silently restore full-history counting.
        if not self._loaded:
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
        """Line numbers of every kernel boot banner in the loaded window."""
        self._ensure_loaded()
        return [i for i, line in enumerate(self.lines, 1)
                if 'Booting Linux on physical CPU' in line]

    def get_latest_crash_context(self, window: int = 5) -> str | None:
        """Text around the most recent error-looking line in the loaded window.

        The counts say *how many*; this says *what it looked like*, which is the
        part that makes a report actionable rather than merely alarming. Under
        incremental watching the window only holds the new tail, so this reports
        what just went wrong — not whatever failed days ago.
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

    def __init__(self, iid: int, scratch_dir: Path, timeout_minutes: int = 60,
                 http_probe_port: int = 0, restart_verify_seconds: int = 120,
                 restart_cooldown_seconds: int = 600,
                 ledger_path: Path | None = None):
        self.iid = iid
        self.scratch_dir = Path(scratch_dir)
        self.timeout = timedelta(minutes=timeout_minutes)
        self.status = ContainerHealthStatus(iid=iid)
        self.analyzer: SerialLogAnalyzer | None = None
        self.start_time = datetime.now(UTC)
        self.recovery_history: list[dict] = []
        #: lines of the serial log already folded into the counters; the next
        #: pass counts only what the guest printed after this point.
        self._log_cursor: int = 0
        #: monotonically growing totals since monitoring started (not per-pass)
        self.cumulative: dict[str, int] = {
            "reboots": 0, "watchdog": 0, "diag_crashes": 0, "soft_lockups": 0,
        }
        #: port to HTTP-probe for the web server; 0 disables probing entirely.
        self.http_probe_port = http_probe_port
        #: how long a container restart gets to bring the web port back up.
        self.restart_verify_seconds = restart_verify_seconds
        #: minimum seconds between two WEB_SERVER_RESTART attempts.
        self.restart_cooldown_seconds = restart_cooldown_seconds
        self._last_web_restart: str | None = None
        #: action -> why it cannot be performed from here. Set when a repair's own
        #: probe reports it disabled nothing, which for the guest-scoped scripts is
        #: structural rather than incidental: they run inside the container and the
        #: guest rootfs is in image.raw. Recommending such an action again every
        #: check interval would fill the ledger with failures that no code change
        #: can fix, and bury the ones a person could.
        self._unreachable_actions: dict[str, str] = {}
        #: append-only action ledger (None in tests that don't ask for one)
        self.ledger: GuardianLedger | None = None
        if ledger_path is not None:
            from iris.monitor.ledger import GuardianLedger as _Ledger

            try:
                self.ledger = _Ledger(ledger_path)
            except OSError as exc:
                logger.warning(f"ledger unavailable at {ledger_path}: {exc}")

    def _ledger_record(self, action: str, kind: str, success: bool,
                       detail: str = "", evidence: str = "") -> None:
        """Best-effort ledger write; never let bookkeeping break the watch."""
        if self.ledger is None:
            return
        self.ledger.record(
            iid=self.iid, action=action, kind=kind, success=success,
            detail=detail, evidence=evidence,
        )

    def _web_restart_cooldown_active(self) -> bool:
        """True while the previous restart is still inside its cooldown window."""
        if not self._last_web_restart:
            return False
        try:
            last = datetime.strptime(self._last_web_restart, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            return False
        return (datetime.now(UTC) - last).total_seconds() < self.restart_cooldown_seconds

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
        """Perform one full health analysis pass.

        Counters reflect only lines appended since the previous pass: a
        continuously watched log would otherwise re-report every historical
        watchdog hit on every wake-up, pinning a repaired guest at critical.
        ``cumulative`` keeps the since-start totals for the report.
        """
        self.status.uptime_seconds = (datetime.now(UTC) - self.start_time).total_seconds()

        log_path = self._get_serial_log_path()
        if not log_path or not log_path.is_file():
            logger.warning(f"no serial log found for container {self.iid}")
            self.status.state = "unknown"
            self.status.web_server_status = "unknown"
            return self.status

        total_lines = SerialLogAnalyzer(log_path).total_line_count()
        if total_lines < self._log_cursor:
            # The log was truncated/recreated (fresh QEMU run): start over.
            self._log_cursor = 0

        self.analyzer = SerialLogAnalyzer(log_path)
        if not self.analyzer.load_log(start_line=self._log_cursor):
            self.status.state = "unknown"
            return self.status

        new_watchdog = self.analyzer.count_pattern_occurrences('watchdog_reboot')
        new_diag = self.analyzer.count_pattern_occurrences('diag_crash')
        new_lockup = self.analyzer.count_pattern_occurrences('soft_lockup')
        new_boots = len(self.analyzer.get_boot_sequence_timeline())

        self.cumulative["watchdog"] += new_watchdog
        self.cumulative["diag_crashes"] += new_diag
        self.cumulative["soft_lockups"] += new_lockup
        # Each boot banner inside the new window is a restart; the very first
        # line ever consumed belongs to the initial boot, not a reboot.
        first_window = self._log_cursor == 0
        self.cumulative["reboots"] += max(0, new_boots - (1 if first_window else 0))

        self._log_cursor = total_lines

        self.status.watchdog_triggers = new_watchdog
        self.status.diag_crashes = new_diag
        self.status.soft_lockup_events = new_lockup
        self.status.reboot_count = self.cumulative["reboots"]

        if self.analyzer.count_pattern_occurrences('web_server_active') > 0:
            self.status.web_server_status = "active"
        elif self.analyzer.count_pattern_occurrences('web_server_start') > 0:
            self.status.web_server_status = "started_but_stopped"
        else:
            self.status.web_server_status = "not_started"

        # The serial log only shows what the guest printed; an HTTP probe is
        # the ground truth for "is it serving *right now*". A probe failure
        # overrides the optimistic log reading: "already running" lines
        # describe boot time, not the current state of :80.
        if (self.http_probe_port and self.status.web_server_status != "not_started"
                and not self.probe_http(self.http_probe_port)):
            self.status.web_server_status = "started_but_stopped"
            logger.info(
                f"container {self.iid}: serial log suggests a web server but "
                f"port {self.http_probe_port} does not answer"
            )

        self.status.anomalies.clear()
        self._determine_overall_state()
        self._log_status_report()
        return self.status

    def _determine_overall_state(self) -> None:
        """Fold the metrics into one state.

        ``expired`` is checked first, and it outranks *every* counter, critical
        included. The watchdog counters are per-pass increments (lines appended
        since the previous pass), so they describe what happened *now*;
        ``reboot_count`` is the since-start total, because restarts compound —
        three restarts spread over an hour is critical even when each pass saw
        only one. A guest that hit its watchdog once, got fixed, and then sat
        quietly reports no new watchdog signal and correctly falls through
        instead of being pinned at critical by history. Past the window,
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
                f"repeated restarts: {s.reboot_count} reboot(s) total, "
                f"{s.watchdog_triggers} new watchdog trigger(s) this pass"
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
        elif s.web_server_status == "started_but_stopped":
            s.state = "degraded"
            s.anomalies.append("web server started but is no longer serving (unexpected exit)")
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
        """Recommend the action matching the strongest detected signal.

        Actions already known to be unreachable from here are skipped in favour of
        the next candidate, and when nothing reachable is left the reason is
        logged. Repeating them anyway would spend a ledger entry every interval on
        an outcome no code change can produce, which is the opposite of what an
        append-only audit is for.
        """
        s = self.status
        candidates: list[str] = []
        if s.watchdog_triggers > 0:
            candidates.append(ACTION_WATCHDOG)
        if s.soft_lockup_events > 0:
            candidates.append(ACTION_RESOURCE)
        if s.diag_crashes > 0:
            candidates.append(ACTION_DIAGNOSTIC)
        if s.web_server_status == "not_started":
            candidates.append(ACTION_WEB_DIAGNOSIS)
        if s.web_server_status == "started_but_stopped":
            candidates.append(ACTION_WEB_RESTART)

        reachable = [a for a in candidates if a not in self._unreachable_actions]
        if reachable:
            return reachable[0]
        if candidates:
            logger.warning(
                f"container {self.iid}: {s.state} detected, but every matching action "
                f"is unreachable from here and none was attempted: "
                + "; ".join(f"{a} ({self._unreachable_actions[a]})" for a in candidates)
            )
        return None

    # ---------------------------------------------------------------- recovery

    def _docker(self, args: list[str], timeout: int = 30) -> tuple[bool, str]:
        """Run a container-level docker command against the emulation container.

        Container-level repairs (restart) are the only ones that reach a running
        guest: the guest rootfs lives inside image.raw, so no docker exec can
        touch its processes. Success is the command's own exit code.
        """
        try:
            proc = subprocess.run(
                ["docker", *args], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error(f"container {self.iid}: docker {' '.join(args[:2])} failed: {exc}")
            return False, str(exc)
        return proc.returncode == 0, ((proc.stdout or "") + (proc.stderr or "")).strip()

    def probe_http(self, host_port: int, timeout: int = 5) -> bool:
        """True when the forwarded web port answers with any HTTP status.

        Reuses the orchestrator's verdict rule: curl writes ``000`` when the
        connection itself fails; any other code (even 404/500) means a server
        is accepting connections on :80.
        """
        ok, out = self._docker(
            ["exec", f"{CONTAINER_PREFIX}{self.iid}", "curl", "-s", "-o", "/dev/null",
             "-w", "%{http_code}", "--max-time", str(timeout), f"http://127.0.0.1:{host_port}"],
            timeout=timeout + 15,
        )
        code = out.strip().splitlines()[-1].strip() if out.strip() else ""
        return ok and code != "" and code != "000"

    def _read_launch_arch(self) -> str:
        """The arch ``make_image.sh`` recorded next to the image, or "" if absent.

        The container's PID 1 is ``sleep 3600`` and QEMU is launched afterwards
        with ``docker exec -d``, so the arch is recorded nowhere else -- without
        this marker a restart cannot re-run QEMU at all.
        """
        ok, out = self._docker(
            ["exec", f"{CONTAINER_PREFIX}{self.iid}", "cat", f"/work/scratch/{self.iid}/arch"],
            timeout=15,
        )
        if not ok:
            return ""
        lines = out.strip().splitlines()
        return lines[-1].strip() if lines else ""

    def _restart_container(self, verify_port: int | None, verify_seconds: int) -> bool:
        """Restart the emulation, then require the web port to come back.

        ``docker restart`` on its own brings back only the container: PID 1 is
        ``sleep 3600`` and QEMU was launched separately, so a plain restart
        leaves no emulation running and the probe can only ever time out. The
        image built by ``make_image.sh`` does survive in the container's
        writable layer, so relaunching QEMU is the only missing step -- and
        ``run_qemu.sh`` clears any TAP/bridge left behind before recreating it.

        A restart that is not followed by a serving port is not a repair; the
        verification window gives the guest time to boot (the orchestration
        path itself allows minutes, so a short window here can only over-report
        failure, never success).
        """
        name = f"{CONTAINER_PREFIX}{self.iid}"


        # Both preconditions are settled *before* the restart: it kills whatever
        # QEMU is running, so discovering afterwards that the relaunch cannot be
        # attempted would trade a guest that is alive but not serving for no
        # emulation at all -- strictly harder to diagnose than what we were called
        # on. Refusing up front leaves the evidence in place.
        arch = self._read_launch_arch()
        if not arch:
            logger.error(
                f"container {self.iid}: no arch recorded at /work/scratch/{self.iid}/arch, "
                "so run_qemu.sh cannot be re-run; leaving the container untouched"
            )
            return False
        if not verify_port:
            logger.error(
                f"container {self.iid}: no probe port configured, so run_qemu.sh has no host "
                "port to forward; leaving the container untouched"
            )
            return False

        ok, out = self._docker(["restart", "-t", "10", name], timeout=60)
        if not ok:
            logger.error(f"container {self.iid}: restart failed: {out}")
            return False

        ok, out = self._docker(
            ["exec", "-d", name, "bash", "/work/scripts/run_qemu.sh",
             str(self.iid), arch, str(verify_port)],
            timeout=15,
        )
        if not ok:
            logger.error(f"container {self.iid}: relaunching QEMU (arch={arch}) failed: {out}")
            return False
        logger.info(
            f"container {self.iid}: relaunched QEMU with arch={arch} port={verify_port}"
        )

        deadline = time.time() + verify_seconds
        while True:
            if self.probe_http(verify_port, timeout=5):
                logger.info(f"container {self.iid}: web serving again on port {verify_port}")
                return True
            if time.time() >= deadline:
                break
            time.sleep(5)
        logger.warning(
            f"container {self.iid}: restarted but port {verify_port} never answered "
            f"within {verify_seconds}s"
        )
        return False

    def _exec_in_container(self, script: str, timeout: int = 30) -> tuple[bool, str]:
        """Run a shell snippet inside the emulation **container**.

        Not the guest. The guest's root filesystem is ``image.raw``, so this sees
        the container's own binaries and processes and none of the guest's -- the
        same boundary ``_docker`` documents. Named for where it runs because the
        earlier name said "guest" in both the docstring and its return value, and
        every script below it makes a claim about the guest.

        The snippet is piped in over stdin (``docker exec -i ... sh -s``), so
        nothing has to be copied first and nothing can end up in the wrong
        filesystem. Returns the container shell's exit status.
        """
        try:
            proc = subprocess.run(
                ["docker", "exec", "-i", f"{CONTAINER_PREFIX}{self.iid}", "/bin/sh", "-s"],
                input=script, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error(f"container {self.iid}: container exec failed: {exc}")
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
        if action == ACTION_WEB_RESTART:
            if self._web_restart_cooldown_active():
                logger.info(
                    f"container {self.iid}: {ACTION_WEB_RESTART} skipped, "
                    f"restart cooldown active"
                )
                return False
            ok = self._restart_container(
                verify_port=self.http_probe_port,
                verify_seconds=self.restart_verify_seconds,
            )
            if ok:
                self._last_web_restart = stamp
                entry = f"{ACTION_WEB_RESTART}@{stamp}"
                self.status.actions_taken.append(entry)
            self.recovery_history.append(
                {"action": action, "timestamp": stamp, "success": ok}
            )
            self._ledger_record(action, "repair", ok)
            if not ok:
                logger.warning(f"[{stamp}] {ACTION_WEB_RESTART} did not take effect on {self.iid}")
            return ok

        if action == ACTION_WEB_DIAGNOSIS:
            # Diagnosis only: nothing is repaired, so it must not be logged as a
            # repair — otherwise the report claims a fix that never happened.
            ok, summary = self._diagnose_web_server()
            entry = f"{ACTION_WEB_DIAGNOSIS}@{stamp}"
            self.status.diagnoses.append(entry)
            self.recovery_history.append(
                {"action": action, "timestamp": stamp, "success": ok, "detail": summary}
            )
            self._ledger_record(action, "diagnosis", ok, detail=summary)
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
        self._ledger_record(action, "repair", success)
        return success

    #: Every repair script below ends by printing a *count* of what it changed, not a
    #: marker that only the script itself could have printed. Two of them used to print
    #: an unconditional ``echo ...-APPLIED; exit 0`` while searching a container for
    #: binaries that live in the guest -- so a boot where the guardian could not touch
    #: a single thing was recorded as two successful repairs. A count is checkable: it
    #: is zero on the boot that found nothing, and no exit code can hide that.
    #:
    #: The paths are overridable so the test suite can run these against a fixture
    #: rootfs. That is not for convenience: with a mocked ``docker exec`` in the way,
    #: a script that always claimed success passes every assertion, and the whole point
    #: of the change is that the script's own output is what is being trusted.
    _WATCHDOG_SCRIPT = """\
: "${IRIS_WATCHDOG_BINARIES:=/bin/monitor /sbin/monitor /usr/bin/monitor \
/usr/sbin/monitor /opt/monitor /opt/bin/monitor /bin/watchdog /sbin/watchdog \
/bin/monitord /bin/arp_monitor /bin/ppp-monitor}"
: "${IRIS_WATCHDOG_DIRS:=/bin /sbin /usr/bin /usr/sbin /opt/bin}"

_disabled=0
for b in ${IRIS_WATCHDOG_BINARIES}; do
  [ -e "$b" ] || [ -L "$b" ] || continue
  if [ -L "$b" ]; then
    rm -f "$b" && _disabled=$((_disabled + 1))
  else
    mv -f "$b" "$b.iris-disabled" && _disabled=$((_disabled + 1))
  fi
done
for d in ${IRIS_WATCHDOG_DIRS}; do
  [ -d "$d" ] || continue
  _links=$(find "$d" -maxdepth 1 -type l \\
    \\( -name '*monitor*' -o -name '*watchdog*' \\) 2>/dev/null)
  # grep -c, not wc -l: an empty result is a count of zero rather than one blank.
  _n=$(echo "${_links}" | grep -c . 2>/dev/null)
  [ "${_n}" -gt 0 ] 2>/dev/null || continue
  echo "${_links}" | while IFS= read -r _l; do rm -f "${_l}"; done
  _disabled=$((_disabled + _n))
done
sync
echo "WATCHDOG-FIX-APPLIED n=${_disabled}"
exit 0
"""

    _CLEANUP_SCRIPT = """\
: "${IRIS_GUARDIAN_PROCESSES:=monitord arp_monitor ppp-monitor}"

_killed=0
for p in ${IRIS_GUARDIAN_PROCESSES}; do
  _pids=$(pidof "$p" 2>/dev/null)
  [ -n "${_pids}" ] || continue
  # Counted per pid, not per name: "monitord" owning three processes is three
  # things killed, and a number that under-reports is the same kind of lie as one
  # that reports success for nothing.
  for _pid in ${_pids}; do
    kill -9 "${_pid}" 2>/dev/null && _killed=$((_killed + 1))
  done
done
sync
echo "RESOURCE-CLEANUP-APPLIED n=${_killed}"
exit 0
"""

    _DIAG_SCRIPT = """\
: "${IRIS_DIAG_BINARIES:=/bin/diag /usr/bin/diag /sbin/diag}"

_disabled=0
for d in ${IRIS_DIAG_BINARIES}; do
  [ -e "$d" ] || continue
  mv -f "$d" "$d.iris-disabled" 2>/dev/null && _disabled=$((_disabled + 1))
done
sync
echo "DIAG-DISABLED n=${_disabled}"
exit 0
"""

    #: Word boundaries on both ends on purpose: a digit run right after ``n=`` is the
    #: count. ``_exec_in_container`` concatenates stderr into the same string, so an
    #: unrelated ``mon=12`` in a warning must not be read as a count of twelve.
    _ACTION_COUNT_RE = re.compile(r"\bn=(\d+)\b")

    def _action_count(self, output: str) -> int | None:
        """How many things a repair script actually changed, or None if it did not
        say. None is the answer for a probe that produced nothing parseable, and it
        is not zero: zero means "looked, found nothing", while None means the probe
        never got far enough to look.
        """
        match = self._ACTION_COUNT_RE.search(output or "")
        return int(match.group(1)) if match else None

    def _apply_container_scoped_repair(
        self, action: str, script: str, timeout: int, what: str
    ) -> bool:
        """Run one repair script and report only what it can actually vouch for.

        True means something the probe could see was changed. False covers two
        different situations that must not be recorded the same way: the probe ran
        and found nothing, and the probe did not run usefully at all. The first is
        not evidence of a healthy guest -- this runs inside the container and the
        guest rootfs is in image.raw, so nothing found means the guest's state is
        unknown, never clean -- which is why the action stops being recommended.
        """
        ok, out = self._exec_in_container(script, timeout=timeout)
        count = self._action_count(out)
        if not ok:
            logger.warning(
                f"container {self.iid}: {action} probe failed: {out or 'no output'}"
            )
            return False
        if count is None:
            # A script that exits zero without naming a count is the shape this
            # whole change exists to remove, so it is not read as "nothing to do".
            logger.warning(
                f"container {self.iid}: {action} probe reported no count and is not "
                f"credited with a repair (output: {out!r})"
            )
            return False
        if count > 0:
            logger.info(f"container {self.iid}: {action} disabled {count} {what}")
            self._unreachable_actions.pop(action, None)
            return True
        logger.warning(
            f"container {self.iid}: {action} changed nothing. That is not a clean "
            f"guest: this probe runs inside the container while the guest rootfs "
            f"lives in image.raw, so the guest's {what} remain UNKNOWN. "
            f"Not recommending {action} again."
        )
        self._unreachable_actions[action] = (
            "probe runs in the container; the guest rootfs is in image.raw"
        )
        return False

    def _apply_watchdog_fixes(self) -> bool:
        """Rename supervision daemons out of the way, where the probe can see them."""
        return self._apply_container_scoped_repair(
            ACTION_WATCHDOG, self._WATCHDOG_SCRIPT, 45, "watchdog binaries"
        )

    def _cleanup_resources(self) -> bool:
        """Kill runaway monitor processes, where the probe can see them."""
        return self._apply_container_scoped_repair(
            ACTION_RESOURCE, self._CLEANUP_SCRIPT, 20, "monitor processes"
        )

    def _disable_diagnostic_tools(self) -> bool:
        """Rename the crashing diagnostic tool, where the probe can see it."""
        return self._apply_container_scoped_repair(
            ACTION_DIAGNOSTIC, self._DIAG_SCRIPT, 20, "diag binaries"
        )

    def _diagnose_web_server(self) -> tuple[bool, str]:
        """Collect why the web server is not serving. Reports; repairs nothing.

        The probes run inside the *container*, not the guest: the guest rootfs
        lives in image.raw, so a missing binary here is expected and only the
        HTTP probe from outside (``probe_http``) says anything about the guest's
        actual web server.
        """
        script = """\
for b in /opt/goahead/goahead /usr/bin/boa /bin/boa /usr/sbin/lighttpd \\
         /usr/sbin/uhttpd /usr/sbin/thttpd; do
  [ -e "$b" ] && echo "binary: $b"
done
pidof goahead >/dev/null 2>&1 && echo "running: goahead"
pidof boa >/dev/null 2>&1 && echo "running: boa"
pidof qemu-system-mipsel >/dev/null 2>&1 && echo "qemu: mipsel running"
pidof qemu-system-mipseb >/dev/null 2>&1 && echo "qemu: mipseb running"
pidof qemu-system-arm >/dev/null 2>&1 && echo "qemu: armel running"
pidof qemu-system-aarch64 >/dev/null 2>&1 && echo "qemu: arm64 running"
netstat -lnt 2>/dev/null | grep -q ':80 ' && echo "listening: 80"
exit 0
"""
        ok, out = self._exec_in_container(script, timeout=20)
        summary = out or "no diagnostics collected"
        if "listening: 80" in out and "running:" in out:
            summary = f"web server is up after all: {summary}"
        elif "qemu:" not in out:
            summary = f"no QEMU process in container — emulation is down: {summary}"
        elif "binary:" not in out:
            summary = (
                "guest filesystem is not visible from the container (expected; "
                "guest lives in image.raw) — use the serial log and the HTTP probe: "
                + summary
            )
        if self.http_probe_port:
            serving = self.probe_http(self.http_probe_port)
            summary = f"http probe on {self.http_probe_port}: {'serving' if serving else 'no answer'}; {summary}"
        return ok, summary

    # ------------------------------------------------------------------- loop

    def start_continuous_monitoring(self, check_interval: int = 30) -> None:
        """Analyse, recover, repeat until interrupted."""
        probe = f", http probe :{self.http_probe_port}" if self.http_probe_port else ""
        logger.info(
            f"AI Guardian watching container {self.iid} every {check_interval}s "
            f"(timeout {int(self.timeout.total_seconds() // 60)}m{probe})"
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
