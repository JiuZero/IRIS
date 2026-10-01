# IRIS - 在工作目录下使用方法

## 🎯 前提条件

已完成：
- ✅ 系统安装的 IRIS 已移除
- ✅ 源代码位于：`D:\桌面资料\临时工作区\IRIS\src`

## 📖 方法一：推荐方式（设置 PYTHONPATH）

### Windows CMD
```bash
cd "D:\桌面资料\临时工作区\IRIS"
set PYTHONPATH=D:\桌面资料\临时工作区\IRIS\src;%PYTHONPATH%

# Now use iris commands
python -m iris.cli emulate list
python -m iris.cli rules list
```

### PowerShell
```powershell
cd "D:\桌面资料\临时工作区\IRIS"
$env:PYTHONPATH="D:\桌面资料\临时工作区\IRIS\src;" + $env:PYTHONPATH

# Use iris commands
python -m iris.cli emulate list
python -m iris.cli rules list
```

### Bash (WSL/GitBash)
```bash
cd "/d/桌面资料/临时工作区/IRIS"
export PYTHONPATH="/d/桌面资料/临时工作区/IRIS/src:$PYTHONPATH"

# Use iris commands
python3 -m iris.cli emulate list
python3 -m iris.cli rules list
```

---

## 📖 方法二：直接调用模块（无需环境变量）

### Option A: 在脚本中添加路径
创建测试脚本 `my_iris_test.py`:

```python
#!/usr/bin/env python
"""IRIS Manual Testing Script"""
import sys
from pathlib import Path

# Add IRIS source to path
iris_src = Path("D:/桌面资料/临时工作区/IRIS/src")
sys.path.insert(0, str(iris_src))

# Now import and use iris modules
print("=" * 60)
print("IRIS - Manual Invocation Test")
print("=" * 60)

# Test 1: Import main app
from iris.cli import app
print("[OK] Main CLI app imported")

# Test 2: List L3 rules
from iris.rules.engine import load_rules
from iris.config import get_settings

settings = get_settings()
rules = load_rules(settings.rules_dir)
print(f"\n[OK] Loaded {len(rules)} L3 rules:")
for rule in rules:
    first_line = (rule.description.splitlines() or [""])[0][:60]
    print(f"  - {rule.id}: {first_line}...")

# Test 3: AI Guardian module
from iris.monitor.ai_guardian import AIHealthMonitor, SerialLogAnalyzer
print("\n[OK] AI Guardian modules ready:")
print("  - AIHealthMonitor class")
print("  - SerialLogAnalyzer class")

# Test 4: Emulate functions
from iris.emulate.auto import prepare_from_firmware, prepare_from_rootfs
print("\n[OK] Emulation functions ready:")
print("  - prepare_from_firmware")
print("  - prepare_from_rootfs")

print("\n" + "=" * 60)
print("All systems operational!")
print("=" * 60)
```

运行：
```bash
cd "D:\桌面资料\临时工作区\IRIS"
python my_iris_test.py
```

---

## 📖 方法三：Python REPL 交互使用

### 启动 Python 并导入模块
```python
>>> import sys
>>> from pathlib import Path
>>> sys.path.insert(0, 'D:/桌面资料/临时工作区/IRIS/src')

>>> # Test imports
>>> from iris.cli import app
>>> print('✓ CLI module loaded')

>>> from iris.monitor.ai_guardian import AIHealthMonitor
>>> print('✓ AI Guardian module loaded')

>>> from iris.rules.engine import load_rules
>>> settings = __import__('iris.config').config.get_settings()
>>> rules = load_rules(settings.rules_dir)
>>> print(f'✓ {len(rules)} rules loaded')

>>> # List containers (if running)
>>> from subprocess import run, PIPE
>>> result = run(['docker', 'ps', '-a', '--filter', 'name=iris-qemu'], capture_output=True, text=True)
>>> print(result.stdout)

>>> # Exit when done
>>> exit()
```

---

## 📖 方法四：创建便捷函数封装

创建 `iris_cli_wrapper.py`:

```python
#!/usr/bin/env python
"""
IRIS CLI Wrapper - Easy access to IRIS commands
Usage:
    python iris_cli_wrapper.py <command> [args...]
    
Examples:
    python iris_cli_wrapper.py rules list
    python iris_cli_wrapper.py emulate list
    python iris_cli_wrapper.py guardian-start 10001 --interval 30
"""
import sys
from pathlib import Path

# Add IRIS to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

def main():
    """Parse command line and execute iris command"""
    if len(sys.argv) < 2:
        print("Usage: python iris_cli_wrapper.py <command> [args...]")
        print("\nAvailable commands:")
        print("  rules list           - List all L3 boot-fix rules")
        print("  emulate list         - List IRIS emulation containers")
        print("  emulate status <iid> - Show detailed container status")
        print("  guardian-start <iid> - Start AI Guardian monitoring")
        return
    
    from click.testing import CliRunner
    from iris.cli import app
    
    runner = CliRunner()
    result = runner.invoke(app, sys.argv[1:])
    
    if result.exit_code == 0:
        print(result.output)
    else:
        print(f"Error: {result.output}")
        sys.exit(result.exit_code)

if __name__ == "__main__":
    main()
```

使用：
```bash
cd "D:\桌面资料\临时工作区\IRIS"
python iris_cli_wrapper.py rules list
python iris_cli_wrapper.py emulate list
```

---

## 📖 完整示例：启动仿真并监控

创建 `run_simulation.py`:

```python
#!/usr/bin/env python
"""
Complete simulation workflow example
"""
import sys
import time
from pathlib import Path
from datetime import datetime

# Add IRIS to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

def main():
    """Run complete simulation with AI Guardian"""
    
    firmware_path = "D:/桌面资料/临时工作区/IRIS/iris-home/corpus/pending/tes7002/US_TES7002V1.0re_v1.0.0.86_en_plus_cn_TD.bin"
    iid = 10011
    port = 8999
    
    print("=" * 70)
    print("IRIS Simulation Workflow")
    print("=" * 70)
    print(f"Firmware: {Path(firmware_path).name}")
    print(f"IID: {iid}")
    print(f"Port: {port}")
    print("=" * 70)
    
    # Step 1: Check firmware exists
    fw_path = Path(firmware_path)
    if not fw_path.exists():
        print(f"\n[ERROR] Firmware not found: {firmware_path}")
        return
    
    print(f"\n[INFO] Firmware found: {fw_path.resolve()}")
    
    # Step 2: Inspect firmware format
    print("\n[STEP 1] Inspecting firmware format...")
    from iris.cli import app
    from click.testing import CliRunner
    
    runner = CliRunner()
    result = runner.invoke(app, ['extract', 'inspect', str(fw_path)])
    if result.exception:
        print(f"Warning: Inspection failed: {result.exception}")
        print(result.output)
    else:
        print(result.output)
    
    # Step 3: Run emulation (this will block until complete)
    print("\n[STEP 2] Starting emulation...")
    print("Note: This may take several minutes...")
    
    result = runner.invoke(app, [
        'emulate', 'run',
        str(fw_path),
        '--iid', str(iid),
        '--port', str(port),
        '--timeout', '180'
    ], catch_exceptions=False)
    
    if result.exit_code != 0:
        print(f"\n[WARNING] Emulation completed with warnings/errors:")
        print(result.output[:500])  # Show first 500 chars
    
    print("\n" + result.output[-1000:])  # Show last 1000 chars
    
    # Step 4: Check container status
    print("\n[STEP 3] Checking container status...")
    result = runner.invoke(app, ['emulate', 'status', str(iid)])
    if result.exit_code == 0:
        print(result.output)
    
    # Step 5: Analyze health (if container exists)
    print("\n[STEP 4] Analyzing container health...")
    from iris.monitor.ai_guardian import AIHealthMonitor
    
    try:
        monitor = AIHealthMonitor(
            iid=iid,
            scratch_dir=Path("D:/桌面资料/临时工作区/IRIS/iris-home/scratch")
        )
        
        status = monitor.analyze_health()
        print("\nContainer Health Report:")
        print(f"  Status: {status.state}")
        print(f"  Reboots: {status.reboot_count}")
        print(f"  Watchdog triggers: {status.watchdog_triggers}")
        print(f"  Diag crashes: {status.diag_crashes}")
        
        recovery = monitor.recommend_recovery_action()
        if recovery:
            print(f"  Recommended action: {recovery}")
        else:
            print("  Recommended action: None needed ✓")
            
    except Exception as e:
        print(f"[INFO] Could not analyze container {iid}: {e}")
    
    print("\n" + "=" * 70)
    print("Simulation workflow complete!")
    print("=" * 70)

if __name__ == "__main__":
    main()
```

运行：
```bash
cd "D:\桌面资料\临时工作区\IRIS"
python run_simulation.py
```

---

## 🧪 快速测试命令

### 测试 1: 验证基本功能
```python
import sys
sys.path.insert(0, 'D:/桌面资料/临时工作区/IRIS/src')

from iris.rules.engine import load_rules
from iris.config import get_settings

rules = load_rules(get_settings().rules_dir)
print(f"Loaded {len(rules)} rules")

for rule in rules:
    print(f"  {rule.id:<26} stage={rule.stage}")
```

### 测试 2: 测试 AI Guardian
```python
import sys
sys.path.insert(0, 'D:/桌面资料/临时工作区/IRIS/src')

from iris.monitor.ai_guardian import SerialLogAnalyzer

# Create analyzer for a specific container
analyzer = SerialLogAnalyzer(iid=10000)

print("Serial Log Analysis Results:")
print(f"  Boot cycles: {len(analyzer.get_boot_sequence_timeline())}")
print(f"  Watchdog reboots: {analyzer.count_pattern_occurrences('watchdog_reboot')}")
print(f"  Soft lockups: {analyzer.count_pattern_occurrences('soft_lockup')}")
print(f"  Diag crashes: {analyzer.count_pattern_occurrences('diag_crash')}")
```

### 测试 3: 检查容器列表
```python
import sys
import subprocess
sys.path.insert(0, 'D:/桌面资料/临时工作区/IRIS/src')

result = subprocess.run(
    ['docker', 'ps', '-a', '--filter', 'name=iris-qemu', 
     '--format', '{{.Names}}|{{.Status}}|{{.Ports}}'],
    capture_output=True, text=True
)

print("Active IRIS Containers:")
print(result.stdout)
```

---

## 📚 常用命令参考

| 命令 | 说明 | 示例 |
|------|------|------|
| **L3 Rules** | | |
| `rules list` | 列出所有修复规则 | `python -m iris.cli rules list` |
| `rules apply <rootfs>` | 应用修复到 rootfs | `python -m iris.cli rules apply /path/to/rootfs` |
| **Emulation** | | |
| `emulate run <firmware>` | 运行仿真 | `python -m iris.cli emulate run firmware.bin` |
| `emulate list` | 列出容器 | `python -m iris.cli emulate list` |
| `emulate status <iid>` | 查看状态 | `python -m iris.cli emulate status 10001` |
| `emulate stop <iid>` | 停止容器 | `python -m iris.cli emulate stop 10001` |
| **AI Guardian** | | |
| `guardian-start <iid>` | 启动监控 | `python -m iris.cli emulate guardian-start 10001` |

---

## 💡 提示与技巧

### 1. 持久化环境变量（Windows）
右键"此电脑" → 属性 → 高级系统设置 → 环境变量  
在"用户变量"中添加：
- 变量名：`PYTHONPATH`
- 变量值：`D:\桌面资料\临时工作区\IRIS\src`

### 2. 创建批处理文件
创建 `start_iris.bat`:
```batch
@echo off
cd /d "D:\桌面资料\临时工作区\IRIS"
set PYTHONPATH=%CD%\src;%PYTHONPATH%
python -m iris.cli %*
```

然后可以这样使用：
```bash
start_iris.bat rules list
start_iris.bat emulate list
```

### 3. 使用 Python IDE
在 VSCode/PyCharm 中配置 Python Interpreter:
- 将项目目录添加到 PYTHONPATH
- 或直接使用工作目录作为终端默认目录

---

## 🔍 故障排查

### 问题：ModuleNotFoundError: No module named 'iris'
**解决**:
```python
import sys
sys.path.insert(0, 'D:/桌面资料/临时工作区/IRIS/src')
from iris.cli import app  # Should work now
```

### 问题：无法找到固件文件
**解决**:
```bash
# List available firmware
ls D:/桌面资料/临时工作区/IRIS/iris-home/corpus/*.bin

# Or use full absolute path
python -m iris.cli emulate run "D:/绝对/路径/to/firmware.bin"
```

### 问题：Permission denied when copying files
**解决**:
```bash
# Run as administrator or adjust permissions
net start docker
```

---

**最后更新**: 2026-09-28  
**版本**: v1.0  
**状态**: Ready to use without system installation

