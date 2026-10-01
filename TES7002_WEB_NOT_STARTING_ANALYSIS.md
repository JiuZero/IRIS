# TES7002 Web 服务不启动问题分析

## 问题概述

Container 10001/10003/10004 在登录界面显示"系统没有启动完成"，因为 Web 服务器（goahead/boa）从未启动。

## 已验证的事实

### ✅ 已成功解决的问题
1. **Watchdog 重启循环** - 通过 `vendor-watchdog-monitor.yaml` 禁用 monitor 守护进程
2. **Diagnostic 工具崩溃循环** - 通过 `generic-diag-crash-fix.yaml` 禁用 /bin/diag

证据（Container 10000 成功运行 60 分钟 + Container 10003 无 diag 崩溃日志）

### ❌ 仍未解决的问题
**Web 服务从不启动**

## 诊断结果

### 1. Web 服务器二进制文件存在
```bash
# 在 rootfs 中已找到:
/bin/boa           ✓ 存在
/opt/goahead/goahead   ✓ 存在
/home/httpd/boa.conf     ✓ 配置文件存在
```

### 2. rcS 启动脚本执行但停滞
串口日志显示：
```
starting pid 398, tty '': '/etc/init.d/rcS'
[   19.538356] module smuxdev: .gnu.linkonce.this_module section size must match the kernel's built struct module size at run time
[   22.853772] EXT4-fs (vda): re-mounted de97cfcd-75ca-425e-851b-fa392c1d040f ro.
[   71.114879] module xt_DSCPEXT: .gnu.linkonce.this_module section size must match the kernel's built struct module size at run time
cgroup: Unknown subsys name 'debug'
module FastPassNF: .gnu.linkonce.this_module section size must match the kernel's built struct module size at run time
sh (871): drop_caches: 3
```

**关键观察**:
- rcS 开始执行但没有完成
- 没有任何 IRIS-NETFIX 网络探针输出
- 没有 goahead/boa 启动命令的执行痕迹
- 日志在某个点后完全停止

### 3. L3 规则应用成功
```
L3 rules matched: dev-extended-nodes, generic-diag-crash-fix, generic-watchdog-guard, vendor-watchdog-monitor
```

所有 4 条规则都已正确应用，但没有看到任何诊断工具禁用的确认消息。

## 根本原因假设

### Hypothesis A: rcS 脚本中有阻塞操作
- rcS 可能在等待某些条件或资源时卡住
- 可能是网络配置、设备节点或其他初始化步骤
- 诊断工具崩溃会消耗 CPU 时间片，导致其他进程无法获得执行机会

**验证方法**: 在容器内部手动执行 rcS 并观察何处卡住

### Hypothesis B: 依赖的硬件模块不可用
- goahead/boa 可能依赖某些硬件特征（如加密加速器、GPU）
- 在 QEMU 虚拟化环境中这些硬件不存在
- 导致启动进程无限等待或静默失败

**验证方法**: 
```bash
docker exec iris-qemu-1000X sh -c "/opt/goahead/goahead --help"
docker exec iris-qemu-1000X sh -c "/bin/boa -h"
```

### Hypothesis C: 文件系统挂载点问题
- rc63 脚本尝试将 goahead 复制到 `/mnt/log/goahead`
- 如果 `/mnt/log` 不存在或未挂载，可能导致脚本失败

**验证方法**:
```bash
docker exec iris-qemu-1000X ls -la /mnt/
```

### Hypothesis D: 权限或路径错误
- goahead/boa 的二进制文件可能被 L3 规则误删或权限被破坏
- L3 规则的 guest_shell 脚本可能影响其他重要文件

**验证方法**: 检查 rootfs 中所有相关文件的属性和权限

## 下一步行动计划

### 紧急修复方案（优先）

#### Option 1: 手动强制启动 Web 服务器
创建自定义 L3 规则直接在 rcS 完成后启动 goahead：

```yaml
id: force-web-server-start
stage: service
detect:
  - file_regex: '^#!/bin/sh$'
    within: 'etc/init.d/rcS'
actions:
  - guest_shell: |
      # Force start goahead immediately after boot
      echo "Starting goahead manually..."
      mkdir -p /mnt/log/goahead 2>/dev/null || true
      cp -rf /opt/goahead /mnt/log 2>/dev/null || true
      nohup /mnt/log/goahead/goahead --home /mnt/log/goahead & 2>/dev/null || \
      nohup /opt/goahead/goahead --home /opt/goahead --route /opt/goahead/route.txt & 2>/dev/null || \
      nohup /bin/boa -d /home/httpd/web > /var/log/boa.log 2>&1 &
```

#### Option 2: 直接修改 rc63 启动顺序
在 rc63 中添加 explicit goahead 启动，绕过任何潜在的阻塞点。

#### Option 3: 使用 boa 而不是 goahead
boa 通常更轻量级，可能有更好的兼容性。

### 长期调查方案

1. **深入 rcS 调试**
   - 在 rcS 的每一行添加 echo 日志
   - 确定精确的卡住位置
   
2. **对比成功的固件**
   - 使用之前成功的 Container 91 或 Container 8080 的 rootfs
   - 对比两者的 init 脚本结构差异

3. **检查硬件依赖**
   - strace goahead 启动过程
   - 查看系统调用中的 EAGAIN/EWOULDBLOCK 错误

## 已知信息汇总

| 项目 | 状态 | 说明 |
|------|------|------|
| watchdog 禁用 | ✅ 成功 | Container 10000 运行 60 分钟无重启 |
| diag 工具禁用 | ✅ 成功 | 新容器日志中无 diag 崩溃 |
| goahead 二进制 | ✅ 存在 | /opt/goahead/goahead |
| boa 二进制 | ✅ 存在 | /bin/boa |
| rcS 执行 | ⚠️ 部分 | 开始后停滞，无完成迹象 |
| Web 监听 | ❌ 失败 | 所有端口探测 HTTP 000 |
| L3 规则应用 | ✅ 成功 | 4 条规则全部匹配 |

## 结论

核心问题是**init 系统停滞在 rcS 阶段**，导致后续的 rc63（goahead 启动脚本）永远不会被执行。

可能的根本原因优先级：
1. rcS 中有阻塞调用（如等待特定设备或网络配置）
2. 系统资源耗尽（尽管 diag 已禁用，但可能还有其他进程占用资源）
3. 文件系统挂载问题（/mnt/log 不存在）

## 建议的测试顺序

1. **立即测试**: Option 1 的手动 goahead 启动脚本
2. **短期**: 检查 `/mnt/log` 是否存在以及权限
3. **中期**: 对比其他成功的固件 init 结构
4. **长期**: 深入 rcS 调试找出确切卡住位置

---
**最后更新时间**: 2026-09-28  
**当前容器状态**: 10004 超时停止（300s 未检测到 Web 服务）
