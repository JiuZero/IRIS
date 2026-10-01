# AI 值守与稳定性治理

本文档记录 IRIS（鸢尾）在 Tenda TES7002 GPON OLT 固件上的实战治理过程：厂商 watchdog
导致的整机重启循环、`diag` 诊断工具的 SIGSEGV 刷屏、Web 服务无法启动三类问题的证据链、
根因、修复措施，以及由此沉淀出的 AI 值守（AI Guardian）能力。

> 本文档由 `AI_GUARDIAN_SUMMARY.md`、`AI_GUARDIAN_DEPLOYMENT.md`、
> `TES7002_WEB_NOT_STARTING_ANALYSIS.md`、`FINAL_SOLUTION_SUMMARY.md`、
> `FINAL_STATUS.md` 五份过程文档归并去重而成。原文之间的矛盾实测数据已在
> [§6 实测数据口径](#6-实测数据口径) 中如实标注，未做粉饰。

---

## 1 问题背景

在 QEMU 全系统仿真（TESC 虚拟化）下跑真实厂商固件时，会出现一类在真实硬件上**不会发生**
的故障。原因是一致的：厂商固件深度绑定了自有的专有硬件（Realtek GPON MAC、硬件加密
加速器）与专有内核设施（`/proc/fc/*`），这些在 QEMU 中全部缺失，厂商守护进程因误判
"硬件子系统已死"而执行恢复动作，反过来把仿真打断。

具体表现为三个互相独立、但共享同一上游根因的问题。

### 1.1 问题 A：厂商 watchdog 触发整机重启循环

容器 8630 的串口日志原文：

```
Monitor: process gp8 is die.
sh: reboot: not found
sysrq: Resetting
[    0.000000] Booting Linux...
```

失效链条：

1. 厂商 `/bin/monitor` 守护进程周期性 `ps | grep -w` 检查子进程状态；
2. `gp8` / `swg` 依赖 Realtek GPON MAC 的 `/proc/fc/*` 接口，QEMU 下**永远起不来**；
3. monitor 判定 "process is die"，调用 `reboot`；
4. BusyBox 精简构建**没有 reboot 命令**，输出 `sh: reboot: not found`；
5. 脚本退化到 `echo b > /proc/sysrq-trigger`，触发内核硬复位 `sysrq: Resetting`；
6. 内核重新引导，`Booting Linux...` —— 设备完全重置，用户体验中断。

实测：容器 8630 在约 5~10 分钟内发生 **4 次完整重启循环**。

关键认知：这类现象**极易被误读为"web 服务崩溃"**。实际上 QEMU 进程、容器、端口转发
全部存活，重启的是 guest 内部的操作系统。若不去看串口日志，只检查 HTTP 探活，会把
问题归因到完全错误的方向。

### 1.2 问题 B：`diag` 诊断工具 SIGSEGV 死循环

```
diag: diag: potentially unexpected fatal signal 11.
```

`/bin/diag` 是厂商自带的诊断工具，在虚拟硬件环境下触发段错误（SIGSEGV）。内核的
"unexpected fatal signal" 崩溃处理器会尝试重启该进程，于是形成**自我拉起的无限循环**。

实测：容器 10001 的单次运行中该日志行累计出现 **约 3900 次**。

危害不在于崩溃本身，而在于它**耗尽 CPU 时间片**，导致其他进程（尤其是 init 链上的
启动脚本）无法获得调度机会。这是问题 C 的候选诱因之一。

### 1.3 问题 C：Web 服务无法启动

设备登录界面提示"系统没有启动完成"。已确认的事实是二进制**确实存在**：

```
/bin/boa                存在
/opt/goahead/goahead    存在
/home/httpd/boa.conf    存在
```

串口日志显示 init 系统启动 `/etc/init.d/rcS` 后出现异常停滞：

```
starting pid 398, tty '': '/etc/init.d/rcS'
[   19.538356] module smuxdev: .gnu.linkonce.this_module section size must match ...
[   22.853772] EXT4-fs (vda): re-mounted de97cfcd-75ca-425e-851b-fa392c1d040f ro.
[   71.114879] module xt_DSCPEXT: .gnu.linkonce.this_module section size must match ...
cgroup: Unknown subsys name 'debug'
module FastPassNF: .gnu.linkonce.this_module section size must match ...
sh (871): drop_caches: 3
```

日志中**再也没有任何 IRIS-NETFIX 网络探针输出**。两个细节值得注意：

- `cgroup: Unknown subsys name 'debug'` —— 内核缺少 cgroup debug 子系统，进一步印证
  专有内核设施缺失；
- 三条内核模块尺寸不匹配告警带时间戳（19.5s / 22.9s / 71.1s），是判断阻塞时机的
  **唯一时序证据**。

---

## 2 根因分析

三个问题共享同一上游根因：

> **QEMU 全系统仿真无法提供厂商固件依赖的专有硬件与专有内核设施，厂商守护进程因误判
> 而执行恢复动作。**

具体到问题 C，"卡在 rcS" 与"启动后立即退出"两种解释在过程文档中并存：

- **假设一（rcS 停滞）**：rcS 内部存在阻塞调用，后续 rc63（goahead 启动脚本）
  永远不会被执行。四个候选原因：rcS 中的阻塞操作、依赖硬件模块不可用、
  文件系统挂载点缺失（`/mnt/log` 不存在导致 goahead 拷贝失败）、权限或路径错误。
- **假设二（启动后退出）**：插入强制启动命令后，串口日志确实打印了
  `IRIS: Starting web server...`，IRIS-NETFIX 甚至报告
  `vendor web server is already running, leaving :80 to it`，但进程随即消失：

  ```
  curl http://localhost:8086/                          → HTTP 000 (timeout)
  docker exec iris-qemu-10006 ps aux | grep goahead    → No processes found
  ```

  根因排序为：① 二进制依赖缺失（Realtek 特定系统调用、硬件加密加速器、`/proc/fc`）；
  ② 启动超时被 shell 的超时机制 kill；③ 权限或 chroot 环境限制。

**两种假设的共同结论一致**：厂商 Web 服务器依赖不可用。分歧仅在于失效发生的具体
时点，这是[§5 遗留问题](#5-遗留问题与后续增强)中仍需验证的部分。

---

## 3 修复措施

### 3.1 L3 规则层：精准阉割 + 强制补偿

IRIS 的应对策略是把修复沉淀为 `rules/*.yaml`，让自动管道在每次仿真时无人值守地应用。
新增 4 条实证规则（连同既有的 `dev-extended-nodes`，共 7 条）：

| 规则文件 | 阶段 | 针对问题 | 机制 |
|---|---|---|---|
| `vendor-watchdog-monitor.yaml` | service | 问题 A | `comment_lines` 注释 `etc/inittab` 中拉起 monitor 的行（前缀 `#IRIS-watchdog: `）；`guest_shell` 将 `/bin/monitor` 等 11 个路径重命名为 `.iris-disabled`（注释 inittab 不够，rcS 链可能重新拉起） |
| `generic-watchdog-guard.yaml` | service | 问题 A（通用化） | 四阶段 `guest_shell`：删损坏符号链接 → 重命名 watchdog 二进制 → `sed` 注释 inittab → 清理 console 触发器并屏蔽 `reboot` 与 sysrq |
| `generic-diag-crash-fix.yaml` | service | 问题 B | 重命名 / 删除 `/bin/diag`、`/usr/bin/diag`、`/sbin/diag`；清理 `/etc/init.d/*diag*` 与 cron；把执行证据写入 `/etc/scripts/boot_fixes.log` |
| `tenda-web-server-forced-start.yaml` | service | 问题 C | 建 `/mnt/log/goahead` 与 `/opt/goahead/route.txt` → 复制 goahead 并 nohup 启动 → boa 兜底 → `pgrep` 验证并写 PID 到 `boot_fixes.log` |

**为什么 `guest_shell` 必须在 guest 内执行**：device node 无法在宿主文件系统创建，
sysrq/proc/sysfs 操作也只能在 chroot 内的真实 init 环境中进行。规则引擎把所有
`guest_shell` 行汇总写入 `<rootfs>/firmadyne/iris_rules.sh`，由 `make_image.sh` →
`fix_image.sh` 在 chroot 内以 `busybox sh` 执行。

### 3.2 脚本层：网络兜底与 Web 存活判定降级链

`scripts/emulate/iris_net_fix.sh` 被注入 guest 的 `/etc/init.d/iris_net_fix`
（`START=99`），承担网络兜底：等待 15s → 若 eth0 无 IP 则配置 `192.168.1.1` →
`iptables -F` 并放行 INPUT/OUTPUT/FORWARD → 若无 `/etc/rc.common` 则起 `telnetd:7002`
→ 再等 30s → 探测并拉起 web 服务。

Web 存活判定采用**三级降级链**：优先 `pidof` 进程判定（goahead / boa / lighttpd /
httpd / uhttpd / thttpd / apache2 / nginx 共 9 种），其次查 80 端口监听，最后才尝试
拉起实例。

这一改动修复了一个会导致故障放大的误判：busybox 的 `netstat` 在部分厂商 build 上
把服务名（`0.0.0.0:http`）而非数字端口打印出来，导致 `:0050` 匹配失败 → IRIS 误判
"web 已死" → 拉起第二个实例 → `Cannot bind to address *:80, errno 98`。改为优先看
进程后，判定依据与厂商自己的 supervisor 一致。

### 3.3 架构映射修复

`docs/07-架构映射修复.md` 记录了一个独立缺陷：ELF 普查得到的标准架构名 `aarch64`
需要映射到 QEMU 内核标签 `arm64`，否则 arm64 固件被误判为"未知架构"而无法自动选参。
TES7002 正是 aarch64 设备，此修复是后续治理的前提。

---

## 4 AI 值守（AI Guardian）

### 4.1 定位

把仿真容器从"被动运行"变成"主动值守"。唯一的输入信号是**串口日志**——因为串口日志
是 guest 内核与应用层的唯一全量可观测通道，HTTP 探活看不到被重启掩盖的故障。

代码位置：`src/iris/monitor/ai_guardian.py`。

### 4.2 串口日志分析（`SerialLogAnalyzer`）

7 类正则模式：

| 模式键 | 匹配内容 |
|---|---|
| `watchdog_reboot` | 厂商 watchdog 判定子进程死亡，如 `Monitor: process gp8 is die` |
| `sysrq_reset` | `sysrq: Resetting` 内核硬复位 |
| `reboot_attempt` | `reboot: not found` 等重启尝试痕迹 |
| `diag_crash` | `potentially unexpected fatal signal 11` / `SIGSEGV` |
| `soft_lockup` | `watchdog: BUG: soft lockup` |
| `web_server_start` | web 服务器进程启动痕迹 |
| `web_server_active` | web 服务器存活痕迹 |

配套能力：`load_log()` 加载日志、`count_pattern_occurrences(pattern)` 计数、
`get_boot_sequence_timeline()` 按 `Booting Linux on physical CPU` 切分启动周期时间线
（据此推算重启次数 = 启动次数 − 1）、`get_latest_crash_context()` 返回崩溃点前后
各 5 行的上下文窗口。

### 4.3 健康状态机

`ContainerHealthStatus` 承载状态与指标，状态取值：

| 状态 | 判定条件 | 含义 |
|---|---|---|
| `critical` | 重启次数 ≥ 3 或 watchdog 触发 ≥ 2 | 不可用 |
| `degraded` | soft lockup > 0 / diag 崩溃 > 0 / web 未启动 | 可用但有隐患 |
| `expired` | 超过 `timeout_minutes`（默认 60）仍未健康 | 超时 |
| `healthy` | 以上均不满足 | 正常 |
| `unknown` | 找不到串口日志 | 无法判定 |

指标字段：`state`、`uptime_seconds`、`reboot_count`、`watchdog_triggers`、
`diag_crashes`、`soft_lockup_events`、`web_server_status`、`anomalies`、`actions_taken`。

实测输出样本：

```
=== Container 10000 Health Report ===
State: DEGRADED
Uptime: 1800s | Reboots: 0
Watchdog Triggers: 0 | Diag Crashes: 47
Soft Lockups: 0
Web Server: not_started
Anomalies: Diagnostic crashes detected
Actions Taken: None
===========================================
```

### 4.4 四类恢复动作

`recommend_recovery_action()` 按优先级返回建议动作，`execute_recovery(action)` 执行：

| 动作 | 触发条件 | 实现 |
|---|---|---|
| `WATCHDOG_RECOVERY` | 检测到 watchdog 触发 | 注入并执行 watchdog 移除脚本 |
| `RESOURCE_CLEANUP` | soft lockup 事件 | `killall -9 monitord arp_monitor ppp-monitor; sync` |
| `DIAGNOSTIC_DISABLEMENT` | diag 崩溃循环 | `rm -f /bin/diag /usr/bin/diag` |
| `WEB_SERVER_DIAGNOSIS` | web 不可达 | 检查 goahead/boa 是否存在、grep init 脚本中的启动痕迹 |

每次成功执行会追加到 `actions_taken` 与 `recovery_history`，带时间戳以便审计。

### 4.5 持续监控循环

`start_continuous_monitoring(check_interval=30)` 每 30 秒：更新 uptime →
`analyze_health()` → 若状态非 `healthy` 则取建议动作并执行 → sleep。`Ctrl+C`
优雅退出；异常时退避 60 秒后重试，避免监控本身成为故障源。

### 4.6 CLI 集成

```bash
iris emulate guardian-start <iid> --interval 30
```

一次性健康检查（不经 CLI 循环）：

```python
import sys
sys.path.insert(0, 'src')
from pathlib import Path
from iris.monitor.ai_guardian import AIHealthMonitor

m = AIHealthMonitor(iid=10000, scratch_dir=Path("iris-home/scratch"))
status = m.analyze_health()
print(status.state, status.reboot_count, status.diag_crashes)
print(m.recommend_recovery_action())
```

串口日志按 Firmadyne 约定在三个候选位置探测：
`<scratch>/<iid>/qemu.serial.log`、`<scratch>/emulate-<iid>/qemu.serial.log`、
容器内 `/work/scratch/<iid>/qemu.serial.log`（最后一种通过 `docker cp` 回捞）。

### 4.7 已知限制

- 需要 Docker 访问权限，Windows 上可能遇到权限问题；
- 只分析串口日志，不做 HTTP 探活——串口日志在某个点后完全停止时，正则计数会**低估**
  真实故障量；
- `expired` 状态的优先级排在 web 未启动判定之后，长时间运行但 web 未启动的容器会被
  归为 `degraded` 而非 `expired`；
- `uptime_seconds` 仅在监控循环中更新，单次调用 `analyze_health()` 时恒为 0。

---

## 5 遗留问题与后续增强

### 5.1 未解决的核心技术问题

| 问题 | 状态 |
|---|---|
| Web 服务仍无法访问（goahead/boa 启动后立即退出，`curl` 返回 HTTP 000，进程消失） | 未解决，根本原因待 `ldd` / `strace` 验证 |
| goahead/boa 二进制依赖缺失（Realtek 硬件 / 系统调用 / `/proc/fc`） | 高度疑似，待验证 |
| IRIS-NETFIX 报告 `vendor web server is already running` 但实际无进程 | 未解释 |
| rcS 停滞的确切位置 | 未定位，需逐行加 `echo` 探针 |
| 与成功固件（Web 可达设备）的 init 结构差异对比分析 | 未执行 |

### 5.2 备选方案

**方案一（应急）**：改用 BusyBox 内置 httpd 兜底

```sh
if ! pgrep -x goahead >/dev/null 2>&1 && ! pgrep -x boa >/dev/null 2>&1; then
    mkdir -p /tmp/httpd
    cp -r /home/httpd/web/* /tmp/httpd/ 2>/dev/null || true
    nohup httpd -p 80 -h /tmp/httpd -w /tmp/httpd &>/dev/null &
fi
```

**方案二（定位）**：`strace` 追踪启动失败

```sh
exec 2>> /tmp/goahead_debug.log
nohup strace -f /opt/goahead/goahead --home /opt/goahead -d &>/tmp/goahead.log &
```

**验证命令清单**：

```bash
docker exec iris-qemu-<iid> sh -c "ldd /opt/goahead/goahead"
docker exec iris-qemu-<iid> sh -c "/opt/goahead/goahead --help" 2>&1
docker exec iris-qemu-<iid> strace -f /opt/goahead/goahead 2>&1 \
  | grep -E "EACCES|ENOENT|EPERM"
```

### 5.3 AI Guardian 后续增强

1. **自动恢复**：让 `execute_recovery()` 真正进入主循环自动触发（当前已接线，见
   `start_continuous_monitoring`，但需配合执行结果校验才能信任）；
2. **Web 探活**：在串口日志分析之外增加 HTTP 可达性检查；
3. **预测性告警**：在 `reboot_count > 0` 之前预测崩溃；
4. **健康度可视化**：容器健康状态随时间变化的仪表盘；
5. **规则自学习**：从新发现的失败模式自动生成 YAML 规则。

---

## 6 实测数据口径

过程文档之间存在互相矛盾的实测数据。本文档按"同容器优先、同口径优先、较晚记录优先"
原则裁决如下，**未删除任何原始数据，只是标注了差异来源**：

| 指标 | 数值 | 口径与差异说明 |
|---|---|---|
| 修复前重启次数 | 4 次 | 容器 8630，各文档一致 |
| 修复前至首次重启耗时 | 5 分钟 / 10 分钟 | 两个口径：按串口日志时间戳约为 5 分钟；按"用户可感知的服务中断"记为 10 分钟。**引用稳定性提升倍数时须注明采用哪个基线** |
| 修复后运行时长 | ≥51 分钟 / ≥60 分钟 | 容器 10000。≥51 分钟为 09-27 首次记录，≥60 分钟为 09-28 复测 |
| diag 崩溃次数 | 约 3900 次 | 容器 10001 单次运行**累计**出现次数 |
| diag 崩溃次数 | 47~49 次 | 容器 8630/10000 单份串口日志的**正则匹配计数**。与 3900 是不同统计口径，不构成矛盾 |
| L3 规则条数 | 7 条 | `rules/` 目录实测。其中 5 条为 boot-fix 核心规则，另 2 条（`mtd-name-lookup-guard`、`shadow-jffs2-opt`）为指纹标记型规则 |
| 涉及容器 | 8630 / 10000 / 10001 / 10003 / 10004 / 10006 | 各问题在不同容器上复现 |

> 早期文档曾给出"稳定性 +1200%"一类百分比指标。该指标依赖上述存疑的基线选择，
> 且百分比对"4 次 → 0 次"这类小基数无实际意义，故本归并文档不再引用。

---

## 7 治理经验

1. **串口日志是唯一可信的失败信号**。HTTP 探活为真的场景（QEMU 活着、端口转发正常）
   与 guest 内核反复重启的场景可以同时成立。只看探活会把问题归因到错误方向。

2. **"消失的命令"要顺着查下去**。`sh: reboot: not found` 不是无害噪声，而是脚本退化
   到 sysrq 硬复位的直接证据。厂商脚本里的每一处降级路径都值得读。

3. **证据要写进日志文件**。规则修复动作统一写入 `/etc/scripts/boot_fixes.log`，
   这是在 guest 已经跑起来后唯一还能取到的审计线索。

4. **修复要沉淀为规则，不要留在 shell 里**。散落在一次性脚本里的修补无法回归测试、
   无法复用到下一个固件。`rules/*.yaml` 是可插拔、可回归的策略库。

5. **守护进程的误判是可预期的**。凡是在 QEMU 下依赖硬件子系统的厂商守护进程，都需要
   默认怀疑。`generic-watchdog-guard.yaml` 就是把 TES7002 的教训推广到任意厂商。

---

## 8 相关文档

| 文档 | 内容 |
|---|---|
| `docs/05-崩溃归因.md` | 崩溃诊断方法论与 TES7002 案例的完整技术细节 |
| `docs/06-稳定性验证.md` | 稳定性验证方法与长时运行观察记录 |
| `docs/07-架构映射修复.md` | aarch64 → arm64 架构标签映射修复 |
| `rules/*.yaml` | L3 规则库（共 7 条） |
| `CHANGELOG.md` | 版本变更记录，含 Phase 1–4 里程碑与 git tag 对应关系 |