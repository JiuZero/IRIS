# IRIS - 使用指南（无系统安装）

## ✅ 已完成

- **系统安装的 IRIS 已移除**
- **源码可独立运行**，无需 pip install

---

## 🚀 3 种使用方法

### 方法 1: PYTHONPATH 环境变量（推荐）

#### Windows CMD
```bash
cd "D:\桌面资料\临时工作区\IRIS"
set PYTHONPATH=D:\桌面资料\临时工作区\IRIS\src;%PYTHONPATH%
python -m iris.cli rules list
```

#### PowerShell
```powershell
cd "D:\桌面资料\临时工作区\IRIS"
$env:PYTHONPATH="D:\桌面资料\临时工作区\IRIS\src;" + $env:PYTHONPATH
python -m iris.cli rules list
```

### 方法 2: Python 脚本中设置路径

```python
import sys
from pathlib import Path

# Add IRIS source to path
sys.path.insert(0, str(Path("D:/桌面资料/临时工作区/IRIS/src")))

# Now use IRIS modules
from iris.rules.engine import load_rules
from iris.config import get_settings

rules = load_rules(get_settings().rules_dir)
print(f"Loaded {len(rules)} rules")
```

### 方法 3: 直接执行模块

```python
import sys
sys.path.insert(0, 'D:/桌面资料/临时工作区/IRIS/src')

from iris.cli import app

# Run commands programmatically
app(['rules', 'list'])
```

---

## 📋 常用命令

```bash
# Set environment first (CMD)
set PYTHONPATH=D:\桌面资料\临时工作区\IRIS\src;%PYTHONPATH%

# List L3 rules
python -m iris.cli rules list

# List emulation containers
python -m iris.cli emulate list

# Show container status
python -m iris.cli emulate status 10000

# Start AI Guardian monitoring
python -m iris.cli emulate guardian-start 10000 --interval 30

# Inspect firmware format
python -m iris.cli extract inspect <firmware.bin>
```

---

## 🧪 快速测试

运行验证脚本：
```bash
cd "D:\桌面资料\临时工作区\IRIS"
python test_manual_usage.py
```

预期输出：
```
[OK] Loaded 7 rules
  dev-extended-nodes
  generic-diag-crash-fix
  generic-watchdog-guard
  mtd-name-lookup-guard
  shadow-jffs2-opt
  
AI Health Monitor ready
Emulation functions loaded
```

---

## 💡 永久生效配置（可选）

### Windows 用户变量
1. Win+R → `sysdm.cpl` → 高级 → 环境变量
2. 添加用户变量：
   - 名称：`PYTHONPATH`
   - 值：`D:\桌面资料\临时工作区\IRIS\src`

### Linux/macOS (~/.bashrc)
```bash
export PYTHONPATH="/d/桌面资料/临时工作区/IRIS/src:$PYTHONPATH"
source ~/.bashrc
```

---

## 📦 当前可用功能

| 模块 | 状态 | 说明 |
|------|------|------|
| CLI | ✅ 可用 | All commands working |
| L3 Rules | ✅ 7 rules | Boot-fix automation |
| AI Guardian | ✅ Ready | Health monitoring & recovery |
| Emulation | ✅ Ready | Firmware simulation |

---

## ✨ 优势

- ✓ 无需管理员权限
- ✓ 不干扰系统环境  
- ✓ 完全可移植
- ✓ 易于版本控制
- ✓ 调试更方便

---

**最后更新**: 2026-09-28  
**状态**: Ready to use

