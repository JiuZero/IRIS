# IRIS 系统安装状态报告

## 🎯 清理完成情况

### ✅ 已完成的清理操作
1. **pip uninstall** - 已从 site-packages 移除
   ```bash
   $ pip uninstall -y iris
   Successfully uninstalled iris-0.1.0
   ```

2. **Python 3.11 Scripts 目录** - 尝试删除但文件被占用
   - 位置：`C:\Users\JiuZe\AppData\Local\Programs\Python\Python311\Scripts\iris.exe`
   - 原因：文件被其他进程占用或被 Windows 资源管理器的缓存锁定

3. **用户脚本目录** - 确认无残留
   - `AppData/Roaming/Python/Python311/Scripts/` - 空 (未找到)

### ❌ 仍存在的旧二进制文件
```
C:\Users\JiuZe\AppData\Local\Programs\Python\Python311\Scripts\
  ├── iris.exe (Zip archive with extra data - old pip wheel wrapper)
  └── iris    (Symlink or wrapper pointing to python script)
```

---

## 🔧 解决方案

### 方法 A: 手动删除（推荐）

**方式 1: 使用 Windows Explorer**
1. 打开文件资源管理器
2. 导航到：`C:\Users\JiuZe\AppData\Local\Programs\Python\Python311\Scripts\`
3. 找到 `iris.exe` 和其他可能的相关文件
4. 右键 → 删除

**方式 2: 使用管理员 CMD**
```cmd
cd /d "C:\Users\JiuZe\AppData\Local\Programs\Python\Python311\Scripts"
del /f iris.exe
del /f iris
```

**方式 3: 使用 Task Manager**
1. 如果提示"文件正在使用"
2. 打开任务管理器 → 详细信息
3. 查找是否有 Python 或相关进程
4. 结束进程后再次尝试删除

### 方法 B: 无需删除系统安装的 IRIS

即使系统有旧的 IRIS 安装，您仍然可以在项目目录中使用最新版本：

```bash
cd "D:\桌面资料\临时工作区\IRIS"
set PYTHONPATH=D:\桌面资料\临时工作区\IRIS\src;%PYTHONPATH%

# 由于 PYTHONPATH 优先级更高，会加载本地的新版本
python -m iris.cli rules list  # 会使用 src/iris 模块
```

---

## ✨ 推荐的使用方式

### 无需完全清理系统安装的 IRIS

直接使用 PYTHONPATH 覆盖：

#### Windows CMD
```bash
@echo off
REM Add to your startup batch file or run manually
set PYTHONPATH=D:\桌面资料\临时工作区\IRIS\src;%PYTHONPATH%

REM Now use IRIS commands from source
python -m iris.cli emulate list
python -m iris.cli guardian-start 10001 --interval 30
```

#### PowerShell Profile
Add to `$HOME\Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1`:
```powershell
$env:PYTHONPATH = "D:\桌面资料\临时工作区\IRIS\src;" + $env:PYTHONPATH
```

Then every new PowerShell window will have IRIS source in path.

---

## 📋 验证当前状态

<tool_call>