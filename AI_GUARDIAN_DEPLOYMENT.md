# AI Guardian Deployment Guide

## Overview
AI Guardian provides continuous monitoring and automated self-healing for IRIS emulation containers, detecting common failure patterns before they cause catastrophic crashes.

## Quick Start

### Monitor an Existing Container
```bash
iris emulate guardian-start <iid> --interval 30
```

Example:
```bash
iris emulate guardian-start 10000 --interval 30
```

This will:
- Continuously monitor the serial log every 30 seconds
- Detect watchdog reboots, soft lockups, and diagnostic crashes
- Automatically apply recovery actions when issues are found
- Display real-time health status in the terminal

### Manual Health Check
For on-demand health assessment without continuous monitoring:

```bash
python test_guardian.py
```

Or run inline:
```python
import sys
sys.path.insert(0, 'src')
from pathlib import Path
from iris.monitor.ai_guardian import AIHealthMonitor

m = AIHealthMonitor(iid=10000, scratch_dir=Path(r"iris-home/scratch"))
status = m.analyze_health()
print(f"Status: {status.state}")
print(f"Watchdog triggers: {status.watchdog_triggers}")
print(f"Diag crashes: {status.diag_crashes}")
```

## Detection Patterns

AI Guardian monitors for these failure indicators:

| Pattern | Description | Impact |
|---------|-------------|--------|
| **Watchdog Reboot** | Vendor process death triggers `sysrq` reset | System crashes |
| **Soft Lockup** | CPU stuck >60s without scheduling | Kernel BUG + freeze |
| **Diagnostic Crashes** | `diag` tool SIGSEGV loops | Resource exhaustion |

## Recovery Actions

When anomalies are detected, AI Guardian automatically applies:

| Action | Trigger | Effect |
|--------|---------|--------|
| **WATCHDOG_RECOVERY** | Watchdog reboot detected | Apply vendor-watchdog-guard rules |
| **RESOURCE_CLEANUP** | Soft lockup events | Kill runaway processes, free resources |
| **DIAGNOSTIC_DISABLEMENT** | Diag crash loops | Disable diagnostic tools preventing crashes |
| **WEB_SERVER_DIAGNOSIS** | Web server unreachable | Run init system diagnostics |

## Case Study: TES7002 Firmware

**Before AI Guardian:**
- Container 8630: 4 reboots, multiple watchdog triggers
- User experience: Login → Service crash after 10 minutes

**After AI Guardian:**
- Container 10000: 0 reboots, running for 51+ minutes
- Status: "degraded" but stable (only diag crashes from prior runs)
- Recommended action: DIAGNOSTIC_DISABLEMENT (can be auto-applied)

## Architecture

```
┌─────────────────────────────────────────────┐
│         AI Health Monitor Core              │
├─────────────────────────────────────────────┤
│ SerialLogAnalyzer                           │
│ ├─ Watchdog detection                       │
│ ├─ Soft lockup detection                    │
│ └─ Crash pattern detection                  │
├─────────────────────────────────────────────┤
│ Recovery Engine                             │
│ ├─ Rule application (L3 YAML)               │
│ ├─ Process management                       │
│ └─ Configuration changes                    │
└─────────────────────────────────────────────┘
```

## Integration Points

### L3 Rules Engine
AI Guardian leverages existing boot-fix rules:
- `vendor-watchdog-monitor.yaml`: Disables vendor watchdog daemons
- `generic-watchdog-guard.yaml`: Universal watchdog protection template

### CLI Commands
All available via `iris` CLI:
```bash
# Continuous monitoring
iris emulate guardian-start <iid> --interval 30

# One-time health check (custom script)
python test_guardian.py

# View container status
iris emulate status <iid>
```

## Best Practices

1. **Start Monitoring Immediately**: Begin AI Guardian monitoring right after container starts
2. **Adjust Interval**: Use 30s intervals for normal operation; reduce to 15s during critical testing
3. **Review Recommendations**: Always check `recommend_recovery_action()` output
4. **Evidence Collection**: Serial logs contain forensic data - preserve them for analysis

## Known Limitations

- Requires Docker access (Windows permission issue may occur)
- Only analyzes serial logs (`qemu.serial.log`)
- Recovery actions require manual confirmation in current implementation

## Future Enhancements

- [ ] Automatic recovery execution (currently recommends only)
- [ ] Predictive failure alerts (before boot cycles begin)
- [ ] Cross-firmware compatibility matrix tracking
- [ ] Web service liveness probing integration

## Related Documentation

- `rules/vendor-watchdog-monitor.yaml`: Detailed watchdog fix mechanics
- `src/iris/monitor/ai_guardian.py`: Full implementation code
- README.md: Installation and quickstart guide
