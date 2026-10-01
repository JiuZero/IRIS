#!/usr/bin/env python
"""Test AI Guardian on container 10000"""
import sys
sys.path.insert(0, 'src')

from pathlib import Path
from iris.monitor.ai_guardian import AIHealthMonitor

# Test on the currently running container
m = AIHealthMonitor(iid=10000, scratch_dir=Path(r"D:\桌面资料\临时工作区\IRIS\iris-home\scratch"))

print("=" * 60)
print("AI Guardian Health Check - Container 10000")
print("=" * 60)

status = m.analyze_health()
print(f"\nHealth Status: {status.state}")

if status.anomalies:
    print("\nDetected Anomalies:")
    for a in status.anomalies:
        print(f"  * {a}")

print(f"\nMetrics:")
print(f"  Reboot count: {status.reboot_count}")
print(f"  Watchdog triggers: {status.watchdog_triggers}")
print(f"  Diag crashes: {status.diag_crashes}")
print(f"  Soft lockup events: {status.soft_lockup_events}")

recovery = m.recommend_recovery_action()
print(f"\nRecommended Action: {recovery or 'None needed'}")
print("=" * 60)
