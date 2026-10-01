#!/usr/bin/env python
"""
Quick Test Script - Demonstrate IRIS Usage Without System Installation
This shows you can use IRIS directly from source code
"""
import sys
from pathlib import Path

# Add IRIS source to path
iris_src = Path("D:/桌面资料/临时工作区/IRIS/src")
sys.path.insert(0, str(iris_src))

print("=" * 70)
print("IRIS - Manual Invocation Demo")
print("=" * 70)

# Test 1: Main CLI module
print("\n[Test 1] Loading main CLI...")
try:
    from iris.cli import app
    print("[PASS] Main CLI app loaded successfully")
except Exception as e:
    print(f"[FAIL] {e}")
    sys.exit(1)

# Test 2: L3 Rules
print("\n[Test 2] Loading L3 boot-fix rules...")
try:
    from iris.rules.engine import load_rules
    from iris.config import get_settings
    
    settings = get_settings()
    rules_dir = settings.rules_dir
    
    print(f"Rules directory: {rules_dir}")
    
    rules = load_rules(rules_dir)
    print(f"[PASS] Loaded {len(rules)} rules:")
    
    for rule in rules[:5]:  # Show first 5 rules
        desc = (rule.description.splitlines()[0][:50])
        print(f"  [OK] {rule.id:<30} -> {desc}...")
        
except Exception as e:
    print(f"[FAIL] {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 3: AI Guardian Module
print("\n[Test 3] Loading AI Guardian modules...")
try:
    from iris.monitor.ai_guardian import AIHealthMonitor, SerialLogAnalyzer
    
    print("[PASS] AI Health Monitor ready")
    print("  - AIHealthMonitor class")
    print("  - SerialLogAnalyzer class")
    
    # Quick demo of analyzer
    try:
        analyzer = SerialLogAnalyzer(iid=10000)
        print("  - Created analyzer for container 10000")
    except Exception as e:
        print(f"  ℹ Analyzer creation skipped (container may not exist): {e}")
        
except Exception as e:
    print(f"[FAIL] {e}")
    sys.exit(1)

# Test 4: Emulation Functions
print("\n[Test 4] Testing emulation functions...")
try:
    from iris.emulate.auto import prepare_from_firmware, prepare_from_rootfs
    from iris.emulate.orchestrator import emulate_firmware
    
    print("[PASS] Emulation functions loaded")
    print("  - prepare_from_firmware")
    print("  - prepare_from_rootfs")
    print("  - emulate_firmware")
    
except Exception as e:
    print(f"[FAIL] {e}")
    sys.exit(1)

# Test 5: Docker Integration
print("\n[Test 5] Checking Docker connectivity...")
try:
    import subprocess
    
    result = subprocess.run(
        ['docker', 'ps', '-a', '--filter', 'name=iris-qemu'],
        capture_output=True, text=True, timeout=5
    )
    
    if result.returncode == 0:
        containers = [line for line in result.stdout.strip().split('\n') 
                      if line and 'NAME' not in line]
        print(f"[PASS] Docker connected")
        print(f"  Found {len(containers)} active IRIS container(s)")
        
        if containers:
            for c in containers[:3]:
                print(f"    {c}")
    else:
        print("[WARN] Docker may not be running or accessible")
        
except FileNotFoundError:
    print("[INFO] Docker command not found (may need to install Docker)")
except Exception as e:
    print(f"[WARN] Docker check failed: {e}")

# Summary
print("\n" + "=" * 70)
print("Summary:")
print("  ✓ All modules successfully imported")
print("  ✓ IRIS is ready to use from source code")
print("  ✓ No system installation required")
print("\nNext Steps:")
print("  1. Set PYTHONPATH environment variable")
print("  2. Use: python -m iris.cli <command> [args]")
print("  3. Or run scripts with: PYTHONPATH=D:/path/to/IRIS/src python script.py")
print("=" * 70)
