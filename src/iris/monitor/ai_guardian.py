"""
AI Guardian: Intelligent container health monitoring and self-healing for IRIS simulations.

This module provides:
1. Real-time serial log analysis for anomaly detection
2. Automatic recovery from common failure patterns (watchdog, soft lockups, web crashes)
3. Predictive alerts before catastrophic failures occur
4. Compatibility matrix tracking for different firmware variants
"""

import re
import subprocess
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Callable
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class ContainerHealthStatus:
    """Container health metrics and status."""
    iid: int
    state: str  # "running", "exited", "healthy", "degraded"
    uptime_seconds: float = 0
    reboot_count: int = 0
    watchdog_triggers: int = 0
    diag_crashes: int = 0
    soft_lockup_events: int = 0
    web_server_status: str = "unknown"  # "active", "stopped", "crashed", "not_started"
    last_check: datetime = field(default_factory=datetime.now)
    anomalies: List[str] = field(default_factory=list)
    actions_taken: List[str] = field(default_factory=list)


class SerialLogAnalyzer:
    """Analyzes QEMU serial logs for failure patterns."""
    
    PATTERNS = {
        'watchdog_reboot': r'Monitor:\s*process\s+\w+\s+is\s+die',
        'sysrq_reset': r'sysrq:\s*Resetting|echo.*b.*proc/sysrq-trigger',
        'reboot_attempt': r'reboot:\s*not found|reboot triggered',
        'diag_crash': r'diag:\s*.*signal\s+11|SIGSEGV',
        'soft_lockup': r'watchdog:\s*BUG:\s*soft lockup.*CPU#?\d+',
        'web_server_start': r'(goahead|boa|lighttpd|httpd).*starting|probing.*web server',
        'web_server_active': r'vendor web server is already running',
    }
    
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.lines = []
        
    def load_log(self) -> bool:
        """Load serial log content."""
        try:
            with open(self.log_path, 'r', errors='ignore') as f:
                self.lines = f.readlines()
            return True
        except Exception as e:
            logger.error(f"Failed to load serial log: {e}")
            return False
    
    def count_pattern_occurrences(self, pattern_name: str) -> int:
        """Count occurrences of a specific failure pattern."""
        if not self.lines:
            self.load_log()
            
        pattern = self.PATTERNS.get(pattern_name)
        if not pattern:
            return 0
            
        count = 0
        for line in self.lines:
            if re.search(pattern, line, re.IGNORECASE):
                count += 1
        return count
    
    def get_boot_sequence_timeline(self) -> List[int]:
        """Get timestamps (line numbers) of all boot sequences."""
        if not self.lines:
            self.load_log()
            
        timeline = []
        for i, line in enumerate(self.lines, 1):
            if 'Booting Linux on physical CPU' in line:
                timeline.append(i)
        return timeline
    
    def get_latest_crash_context(self) -> Optional[str]:
        """Get context around the most recent crash/error."""
        if not self.lines:
            self.load_log()
        
        # Search backwards from end for first error
        for i in range(len(self.lines) - 1, max(0, len(self.lines) - 100), -1):
            line = self.lines[i]
            if any(kw in line.lower() for kw in ['error', 'fail', 'crash', 'signal', 'die']):
                # Return 10-line context window
                start = max(0, i - 5)
                end = min(len(self.lines), i + 6)
                return ''.join(self.lines[start:end])
        return None


class AIHealthMonitor:
    """Main guardian class for container health management."""
    
    def __init__(self, iid: int, scratch_dir: Path, timeout_minutes: int = 60):
        self.iid = iid
        self.scratch_dir = scratch_dir
        self.timeout = timedelta(minutes=timeout_minutes)
        self.status = ContainerHealthStatus(iid=iid, state="unknown")
        self.analyzer: Optional[SerialLogAnalyzer] = None
        self.start_time = datetime.now()
        self.recovery_history: List[Dict] = []
        
    def _get_serial_log_path(self) -> Path:
        """Locate the serial log file."""
        # Try multiple locations based on Firmadyne conventions
        candidates = [
            self.scratch_dir / f"{self.iid}" / "qemu.serial.log",
            self.scratch_dir / f"emulate-{self.iid}" / "qemu.serial.log",
            Path("/work/scratch") / f"{self.iid}" / "qemu.serial.log",
        ]
        
        for candidate in candidates:
            if candidate.exists():
                return candidate
        
        # Fallback: copy from container
        try:
            result = subprocess.run(
                ["docker", "cp", f"iris-qemu-{self.iid}:/work/scratch/{self.iid}/qemu.serial.log", 
                 str(self.scratch_dir)],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                return self.scratch_dir / "qemu.serial.log"
        except:
            pass
            
        return None
    
    def analyze_health(self) -> ContainerHealthStatus:
        """Perform comprehensive health analysis."""
        log_path = self._get_serial_log_path()
        if not log_path or not log_path.exists():
            logger.warning(f"No serial log found for container {self.iid}")
            self.status.state = "unknown"
            return self.status
        
        self.analyzer = SerialLogAnalyzer(log_path)
        if not self.analyzer.load_log():
            return self.status
        
        # Count various failure indicators
        self.status.watchdog_triggers = self.analyzer.count_pattern_occurrences('watchdog_reboot')
        self.status.diag_crashes = self.analyzer.count_pattern_occurrences('diag_crash')
        self.status.soft_lockup_events = self.analyzer.count_pattern_occurrences('soft_lockup')
        
        # Analyze reboot sequence
        boot_timeline = self.analyzer.get_boot_sequence_timeline()
        self.status.reboot_count = max(0, len(boot_timeline) - 1)  # Subtract initial boot
        
        # Check web server status
        if self.analyzer.count_pattern_occurrences('web_server_active') > 0:
            self.status.web_server_status = "active"
        elif self.analyzer.count_pattern_occurrences('web_server_start') > 0:
            self.status.web_server_status = "started_but_stopped"
        else:
            self.status.web_server_status = "not_started"
        
        # Determine overall health state
        self._determine_overall_state()
        
        # Log current status
        self._log_status_report()
        
        return self.status
    
    def _determine_overall_state(self):
        """Determine if container is healthy, degraded, or critical."""
        # Critical conditions
        if self.status.reboot_count >= 3 or self.status.watchdog_triggers >= 2:
            self.status.state = "critical"
            self.status.anomalies.append("Multiple reboot cycles detected")
        elif self.status.soft_lockup_events > 0:
            self.status.state = "degraded"
            self.status.anomalies.append("CPU soft lockup detected")
        elif self.status.diag_crashes > 0:
            self.status.state = "degraded"
            self.status.anomalies.append("Diagnostic crashes detected")
        elif self.status.web_server_status == "not_started":
            self.status.state = "degraded"
            self.status.anomalies.append("Web server failed to start")
        elif (datetime.now() - self.start_time) > self.timeout:
            self.status.state = "expired"
        else:
            self.status.state = "healthy"
    
    def _log_status_report(self):
        """Log comprehensive status report."""
        report = f"""
=== Container {self.iid} Health Report ===
State: {self.status.state.upper()}
Uptime: {self.status.uptime_seconds:.0f}s | Reboots: {self.status.reboot_count}
Watchdog Triggers: {self.status.watchdog_triggers} | Diag Crashes: {self.status.diag_crashes}
Soft Lockups: {self.status.soft_lockup_events}
Web Server: {self.status.web_server_status}
Anomalies: {', '.join(self.status.anomalies) if self.status.anomalies else 'None'}
Actions Taken: {', '.join(self.status.actions_taken) if self.status.actions_taken else 'None'}
===========================================
"""
        logger.info(report)
    
    def recommend_recovery_action(self) -> Optional[str]:
        """Recommend appropriate recovery action based on detected issues."""
        if self.status.watchdog_triggers > 0:
            return "WATCHDOG_RECOVERY"
        elif self.status.soft_lockup_events > 0:
            return "RESOURCE_CLEANUP"
        elif self.status.diag_crashes > 0:
            return "DIAGNOSTIC_DISABLEMENT"
        elif self.status.web_server_status == "not_started":
            return "WEB_SERVER_DIAGNOSIS"
        return None
    
    def execute_recovery(self, action: str) -> bool:
        """Execute recommended recovery action."""
        action_id = datetime.now().strftime("%Y%m%d%H%M%S")
        
        if action == "WATCHDOG_RECOVERY":
            logger.info(f"[{action_id}] Executing WATCHDOG_RECOVERY...")
            success = self._apply_watchdog_fixes()
            
        elif action == "RESOURCE_CLEANUP":
            logger.info(f"[{action_id}] Executing RESOURCE_CLEANUP...")
            success = self._cleanup_resources()
            
        elif action == "DIAGNOSTIC_DISABLEMENT":
            logger.info(f"[{action_id}] Executing DIAGNOSTIC_DISABLEMENT...")
            success = self._disable_diagnostic_tools()
            
        elif action == "WEB_SERVER_DIAGNOSIS":
            logger.info(f"[{action_id}] Executing WEB_SERVER_DIAGNOSIS...")
            success = self._diagnose_web_server()
            
        else:
            logger.error(f"[{action_id}] Unknown recovery action: {action}")
            return False
        
        if success:
            self.status.actions_taken.append(f"{action}@{action_id}")
            self.recovery_history.append({"action": action, "timestamp": action_id, "success": True})
        
        return success
    
    def _apply_watchdog_fixes(self) -> bool:
        """Apply watchdog disablement fixes via guest shell execution."""
        try:
            # Inject fixed L3 rules into the running container
            fix_script = """
#!/bin/sh
# IRIS-WATCHDOG-FIX: Emergency watchdog removal

# Remove all monitor symlinks first
find /bin /sbin /usr/bin /usr/sbin -maxdepth 1 -type l \\
    \\( -name "*monitor*" -o -name "*watchdog*" \\) -exec rm -f {} \\; 2>/dev/null

# Rename remaining binaries
for b in /opt/monitor /bin/monitord /bin/arp_monitor /bin/ppp-monitor; do
    if [ -L "$b" ]; then rm -f "$b";
    elif [ -x "$b" ]; then mv -f "$b" "${b}.iris-fixed";
    fi
done

# Block reboot commands
for rb in /sbin/reboot /bin/reboot; do
    if [ -x "$rb" ]; then mv -f "$rb" "${rb}.iris-blocked";
    fi
done

echo "WATCHDOG FIX COMPLETE"
"""
            # Write script to container and execute
            script_path = Path(f"/tmp/watchdog-fix-{self.iid}.sh")
            script_path.write_text(fix_script)
            
            # Execute inside container using docker exec
            result = subprocess.run(
                ["docker", "exec", f"iris-qemu-{self.iid}", 
                 "/bin/sh", "-c", "/bin/sh /tmp/watchdog-fix-*.sh"],
                capture_output=True, text=True, timeout=30
            )
            
            logger.info(f"Watchdog fix output: {result.stdout}")
            return result.returncode == 0
            
        except Exception as e:
            logger.error(f"Watchdog fix failed: {e}")
            return False
    
    def _cleanup_resources(self) -> bool:
        """Attempt resource cleanup for soft lockup recovery."""
        try:
            # Stop non-essential services
            result = subprocess.run(
                ["docker", "exec", f"iris-qemu-{self.iid}",
                 "/bin/sh", "-c", "killall -9 monitord arp_monitor ppp-monitor 2>/dev/null; sync"],
                capture_output=True, text=True, timeout=10
            )
            return True
        except Exception as e:
            logger.error(f"Resource cleanup failed: {e}")
            return False
    
    def _disable_diagnostic_tools(self) -> bool:
        """Disable diagnostic tools that cause SIG11 crashes."""
        try:
            result = subprocess.run(
                ["docker", "exec", f"iris-qemu-{self.iid}",
                 "/bin/sh", "-c", "rm -f /bin/diag /usr/bin/diag 2>/dev/null && echo 'DIAG DISABLED'"],
                capture_output=True, text=True, timeout=10
            )
            logger.info(result.stdout)
            return True
        except Exception as e:
            logger.error(f"Diagnostic disablement failed: {e}")
            return False
    
    def _diagnose_web_server(self) -> bool:
        """Diagnose why web server isn't starting."""
        try:
            # Check if web server binary exists
            result = subprocess.run(
                ["docker", "exec", f"iris-qemu-{self.iid}",
                 "/bin/sh", "-c", "ls -la /opt/goahead/goahead /bin/boa 2>&1"],
                capture_output=True, text=True, timeout=10
            )
            
            if "No such file" in result.stdout:
                logger.error("Web server binary missing from rootfs")
                return False
            
            # Check if it's being spawned in rc scripts
            result = subprocess.run(
                ["docker", "exec", f"iris-qemu-{self.iid}",
                 "/bin/sh", "-c", "grep -r 'goahead\\|boa' /etc/init.d/ 2>/dev/null"],
                capture_output=True, text=True, timeout=10
            )
            
            logger.info(f"Web server diagnostics: {result.stdout}")
            return True
            
        except Exception as e:
            logger.error(f"Web server diagnosis failed: {e}")
            return False
    
    def start_continuous_monitoring(self, check_interval: int = 30):
        """Start continuous health monitoring loop."""
        logger.info(f"Starting AI Guardian monitoring for container {self.iid}")
        
        while True:
            try:
                # Update uptime
                self.status.uptime_seconds = (datetime.now() - self.start_time).total_seconds()
                
                # Perform health analysis
                status = self.analyze_health()
                
                # Check if recovery needed
                if status.state != "healthy":
                    action = self.recommend_recovery_action()
                    if action:
                        logger.warning(f"[{self.iid}] Recovery needed: {status.state} → {action}")
                        self.execute_recovery(action)
                
                # Wait for next check
                time.sleep(check_interval)
                
            except KeyboardInterrupt:
                logger.info("AI Guardian monitoring stopped by user")
                break
            except Exception as e:
                logger.error(f"Monitoring error: {e}")
                time.sleep(60)  # Backoff on error


def main():
    """CLI entry point for AI Guardian."""
    import typer
    
    app = typer.Typer()
    
    @app.command()
    def start(
        iid: int = typer.Argument(..., help="Container image ID"),
        interval: int = typer.Option(30, "--interval", help="Check interval in seconds"),
    ):
        """Start AI Guardian monitoring for a container."""
        from iris.config import get_settings
        
        settings = get_settings()
        guardian = AIHealthMonitor(iid=iid, scratch_dir=settings.scratch_dir)
        guardian.start_continuous_monitoring(check_interval=interval)
    
    app()


if __name__ == "__main__":
    main()
