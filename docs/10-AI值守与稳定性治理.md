# AI 值守与稳定性治理

本文档记录 IRIS（鸢尾）在 Tenda TES7002 GPON OLT 固件上的实战治理过程：厂商 watchdog
导致的整机重启循环、`diag` 诊断工具的 SIGSEGV 刷屏、Web 服务无法启动三类问题的证据链、
根因、修复措施，以及由此沉淀出的 AI 值守（AI Guardian）能力。

> **命名说明**：这里的「AI 值守」是历史沿用的叫法。`src/iris/monitor/ai_guardian.py`
> 的实现是**正则 + 状态机 + 规则表**，仓库内没有任何模型调用、也没有任何网络推理。
> 下文的「智能」「自动恢复」等描述均指规则命中后的自动处置，不指模型推断。
> `emulate guardian-start` 这个命令名与文件名一并保留，以免既有脚本失效。

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
boot-fix 核心规则 3 条（连同 `dev-extended-nodes` 等既有用例，共 6 条）：

| 规则文件 | 阶段 | 针对问题 | 机制 |
|---|---|---|---|
| `vendor-watchdog-monitor.yaml` | service | 问题 A | 双指纹（inittab 正则 + 9 类命名变体的二进制存在性，AND 后再 OR 任一）；`comment_lines` 注释 `etc/inittab` 中拉起 monitor 的行（前缀 `#IRIS-watchdog: `）；`guest_shell` 清理符号链接、把 watchdog 二进制重命名为 `.iris-disabled`、切断 `reboot` 与 sysrq 触发路径（注释 inittab 不够，rcS 链可能重新拉起） |
| `generic-diag-crash-fix.yaml` | service | 问题 B | 按 `diag` 二进制存在性做指纹（不绑定某一条启动路径）；重命名 / 删除 `/bin/diag`、`/usr/bin/diag`、`/sbin/diag`；`sed` 注释 init 脚本与 cron 中的启动项；证据写入 `/etc/scripts/boot_fixes.log` |
| `tenda-web-server-forced-start.yaml` | service | 问题 C | 只创建 `iris_net_fix.sh` 启动所需的前置（`/opt/goahead/route.txt`、`/etc/boa/boa.conf`）；**不在 chroot 内拉起进程**，启动交给 boot 期的 `iris_net_fix.sh` |

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

把仿真容器从"被动运行"变成"主动值守"。主输入信号是**串口日志**——因为串口日志
是 guest 内核与应用层的唯一全量可观测通道，HTTP 探活看不到被重启掩盖的故障。
但串口日志也会说谎：它记录的是启动时刻的状态（如"vendor web server is already
running"），不代表此刻 :80 还有人应答，所以监控期补了**第二信号源**——对转发端口的
HTTP 探活（`--probe-port`），探活失败会推翻串口日志的乐观结论。

代码位置：`src/iris/monitor/ai_guardian.py`（值守）、`src/iris/monitor/ledger.py`（动作账本）。

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

配套能力：`load_log(start_line=0)` 加载日志（支持从指定行起读，增量窗口）、
`total_line_count()` 不缓存地数总行数（游标推进用）、`count_pattern_occurrences(pattern)`
计数、`get_boot_sequence_timeline()` 按 `Booting Linux on physical CPU` 切分启动周期
时间线（据此推算重启次数）、`get_latest_crash_context()` 返回崩溃点前后各 5 行的
上下文窗口。

**增量计数语义（2026-10-02 起）**：连续监控下每轮只统计上次游标之后新增的日志行——
watchdog/reboot 历史是"过去时"，一个触发过 watchdog、被修复后安静下来的 guest
不应被历史钉死在 critical。自监控启动以来的累计值保存在 `monitor.cumulative`。

### 4.3 健康状态机

`ContainerHealthStatus` 承载状态与指标，状态取值（`expired` 最先评估，压过一切计数器）：

| 状态 | 判定条件 | 含义 |
|---|---|---|
| `expired` | 超过 `timeout_minutes`（默认 60）仍未健康 | 超时（历史计数不再参与判定） |
| `critical` | 本轮新增：重启次数 ≥ 3 或 watchdog 触发 ≥ 2（含累计重启 ≥ 3） | 不可用 |
| `degraded` | soft lockup / diag 崩溃 / web 未启动 / **web 启动后意外退出** | 可用但有隐患 |
| `healthy` | 以上均不满足 | 正常 |
| `unknown` | 找不到串口日志 | 无法判定 |

`web_server_status` 取值：`active` / `started_but_stopped`（启动后退出——即"Web 意外
退出"，探活失败或串口只见到启动痕迹时判定）/ `not_started` / `unknown`。

指标字段：`state`、`uptime_seconds`、`reboot_count`、`watchdog_triggers`、
`diag_crashes`、`soft_lockup_events`、`web_server_status`、`anomalies`、
`actions_taken`、`diagnoses`。

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

### 4.4 五类恢复动作

`recommend_recovery_action()` 按优先级返回建议动作，`execute_recovery(action)` 执行：

| 动作 | 触发条件 | 实现层 |
|---|---|---|
| `WATCHDOG_RECOVERY` | 检测到 watchdog 触发 | guest 内脚本（构建期语义，见 4.8） |
| `RESOURCE_CLEANUP` | soft lockup 事件 | guest 内脚本 |
| `DIAGNOSTIC_DISABLEMENT` | diag 崩溃循环 | guest 内脚本 |
| `WEB_SERVER_DIAGNOSIS` | web 不可达 | 只排查不修复，结论记 `diagnoses` |
| `WEB_SERVER_RESTART` | web 启动后意外退出 | **容器层** `docker restart` + 重启 QEMU + 探活复验 |

`WEB_SERVER_RESTART` 的关键约束：
- **`docker restart` 单独做不了这件事**：容器 PID 1 是 `sleep 3600`，QEMU 由
  `docker exec -d` 另起，重启只把 `sleep` 拉回来，仿真进程一个都不剩。因此重启后
  必须再 `exec /work/scripts/run_qemu.sh <iid> <arch> <port>` 把 guest 重新拉起；
- **arch 来自 `make_image.sh` 写下的标记** `image.raw` 同目录的 `arch` 文件。
  容器里没有任何其他地方记录过 QEMU 是用什么架构起的（实测：重启前后该文件均在，
  `docker restart` 不丢容器可写层），读不到就**拒绝重启**而不是空转；
- **guest 地址也来自同目录的 `guest_ip` 标记**，由首启认出子网时写下，
  `run_qemu.sh` 重跑时优先读它（详见 6.5）。所以三参数重跑不会退回假定桥地址——
  这一点曾经让一次成功的重启把服务弄坏；
- **两个前置条件在重启之前检查**：重启会杀掉正在跑的 QEMU，事后才发现无法重新
  拉起，等于把"活着但不服务"变成"什么都没跑"，比调用前更难排查；
- 重启后必须在 `--restart-verify`（默认 120s）内探活成功才算修复，"重启了但 Web
  没回来"记失败；
- 两次重启之间有冷却期（默认 600s），无法救活的 guest 不会被无间隔地反复重启；
- 修复是否成功以 docker 命令退出码 + 探活双重确认，不做"跑过了就算"。

实测（TES7002 arm64 真实固件，容器 `iris-qemu-9001`）：

| 步骤 | 结果 |
|---|---|
| 起仿真 | 76s 后 HTTP 302 |
| 容器内 kill qemu + socat | HTTP 000 |
| `analyze_health()` | `degraded` / `started_but_stopped` → 建议 `WEB_SERVER_RESTART` |
| `execute_recovery()` | 77s 后返回 `True`，Web 恢复 302 |
| 负向对照：只 `docker restart` | qemu 进程数 0，87s 后仍 HTTP 000（`image.raw` 与 `arch` 均存活） |

每次成功执行会追加到 `actions_taken` 与 `recovery_history`，带时间戳以便审计；
所有动作与诊断同时落**动作账本**（SQLite，见 4.9）。

前三个动作的成功判定在 0.3.10 收紧：**脚本必须输出实际计数**（`n=<数>`）才算修过，
且 `n=0` 会把该动作记为「探针够不到」并在后续推荐中跳过——因为这三个脚本在容器里执行，
而 guest 根文件系统在 `image.raw` 里，找不到不等于没问题。详见 8.7。

### 4.5 持续监控循环

`start_continuous_monitoring(check_interval=30)` 每 30 秒：更新 uptime →
`analyze_health()` → 若状态非 `healthy` 则取建议动作并执行 → sleep。`Ctrl+C`
优雅退出；异常时退避 60 秒后重试，避免监控本身成为故障源。

### 4.6 CLI 集成

```bash
iris emulate guardian-start <iid> --interval 30 \
    --probe-port 8080 --restart-verify 120

# 查看值守动作账本
iris emulate guardian-log [--iid 10001] [--limit 20]
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
- 串口日志在某个点后完全停止时，增量窗口为空，正则计数会**低估**真实故障量
  （HTTP 探活可弥补"是否还在服务"这半个盲区）；
- `uptime_seconds` 仅在监控循环中更新，单次调用 `analyze_health()` 时恒为 0。

### 4.8 执行通道的两层语义（重要）

`docker exec` 到达的是**仿真容器**（ubuntu + QEMU 进程），不是 QEMU guest 内部；
guest 的 rootfs 在 `image.raw` 里，容器内没有它的挂载。因此：

| 层 | 通道 | 能触达什么 | 适用阶段 |
|---|---|---|---|
| guest 内 | `docker exec -i ... sh -s` | 容器自身文件系统；**看不到** guest 进程与文件 | 镜像构建期（chroot）语义 |
| 容器层 | `docker restart` | QEMU 与 guest 一并重来 | **运行期唯一真正触达 guest 的修复** |

`WEB_SERVER_DIAGNOSIS` 在容器层运行，因此它报告的是容器侧可见性（QEMU 进程是否
存活、容器内是否有 curl 可达的 :80），而不是 guest 内部的进程/配置——后者只能靠
串口日志与外部探活推断。

0.3.10 起这段语义在代码里也有名字：`docker exec` 那条通道的方法从 `_exec_in_guest`
改名为 `_exec_in_container`，docstring 明说「不是 guest，guest rootfs 在 `image.raw`」。
`_WATCHDOG_RECOVERY` / `RESOURCE_CLEANUP` / `DIAGNOSTIC_DISABLEMENT` 三个动作的表里
「实现层」标注为 guest 内脚本，因此**在运行期够不到 guest**——它们现在会输出实际计数并
在被证明够不到时停止被推荐，而不是记成成功的修复。**只有 `WEB_SERVER_RESTART`
（容器层重启）真正触达 guest。**

### 4.9 动作账本（guardian action ledger）

所有恢复动作与诊断落 `iris-home/scratch/guardian_ledger.sqlite3`（追加式 SQLite，
`src/iris/monitor/ledger.py`）。字段对齐 `db.models.RepairAction`
（source/rule_id/evidence/applied/promoted），将来接入 `emulation_run` 主链路时是
列迁移而非重新设计。`promoted` 标志为 P3"修复沉淀回 L3 规则"预留：
`mark_promoted(entry_id)` 在一次修复被固化为确定性规则后打上。账本写入失败只告警、
绝不阻断值守循环。

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

1. **自动恢复**：~~让 `execute_recovery()` 真正进入主循环自动触发~~ 已接线并配合
   执行结果校验（退出码 + echo 标记 + 探活复验）；
2. **Web 探活**：~~在串口日志分析之外增加 HTTP 可达性检查~~ 已落地（`--probe-port`，
   探活失败覆盖串口乐观结论）；
3. **Web 重启闭环**：~~Web 意外退出后自动恢复服务~~ 已落地容器级
   `WEB_SERVER_RESTART`（重启 + 重新拉起 QEMU + 探活复验 + 冷却期，2026-10-02 在真实
   固件上验证：故障注入后 77s 恢复 302；修复前只重启容器必然救不回，已做负向对照）；
4. **动作账本**：已落地（`guardian-log` 可查）；
5. **预测性告警**：在 `reboot_count > 0` 之前预测崩溃（待做）；
6. **健康度可视化**：容器健康状态随时间变化的仪表盘（待做）；
7. **规则自学习**：从账本中的有效修复自动生成 YAML 规则草稿（待做，P3）。

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
| L3 规则条数 | 6 条 | `rules/` 目录实测。其中 `dev-extended-nodes` / `mtd-name-lookup-guard` / `shadow-jffs2-opt` 为节点扩展与指纹标记型规则，另 3 条为 boot-fix 核心规则 |
| 涉及容器 | 8630 / 10000 / 10001 / 10003 / 10004 / 10006 | 各问题在不同容器上复现 |

> 早期文档曾给出"稳定性 +1200%"一类百分比指标。该指标依赖上述存疑的基线选择，
> 且百分比对"4 次 → 0 次"这类小基数无实际意义，故本归并文档不再引用。

### 6.1 仿真侧数字自 0.3.7 起不再手工维护

上表裁决的是**同一容器上反复观测**的口径冲突。跨固件的仿真成功率一度不属于此类——
`emulation_run` / `failure_profile` 有表定义但零写入路径，`docs/eval-log.md` 里
"4/5 启动成功"与"五行全部 ✅"只能算手工台账的内部矛盾。

自 0.3.7 起每次仿真（含从未启动容器的早退）自动落库，`iris db stats` 从表里读回
总数、Web 达标率、按 arch 分解与失败直方图，与本节的裁决原则一致：**空表就说空，
0/0 不渲染成百分比**。失败分类法（`src/iris/failures.py`，Stage 7 类 / Kind 23 个）
由测试双向守卫，新增成员未配 stage 会直接变红。

尚未收口：语料登记名 `image.arch`（`aarch64`）与运行时名 `emulation_run.arch`
（`arm64`）尚非同一套词表，`db stats` 的 arch 分组暂不能与语料表直接对齐。

### 6.2 架构命名已收口为单一权威（0.3.8）

上面那条遗留的前半段已解决：`src/iris/arch.py` 现在是架构命名的唯一权威，
`normalize_arch()` 把 `aarch64` / `arm64` / `arm64le` 归一到内核资产名。收口前这个
名字有八个家（两份内联 dict、两份私有副本、三份各自的 `e_machine` 解析、一份自创的
`arm64le`），漂移已经实际生效——API 层把 `aarch64` 判为不支持，而这正是 ELF 头写的名字。

对值守闭环的意义：失败归因此前有一个静默出口。guest 识别为 `aarch64` 而启动器只认
`arm64` 时，失败会被记成「未知架构」，进不了任何可聚合的分类。现在这类失败统一走
`iris.failures` 的既有 kind，直方图能看见它。

QEMU 设备模型同时补全为 `run_qemu.sh` 的镜像，并由 `tests/test_qemu_config_matches_script.py`
逐字段比对两侧。跨语言无法共用一份数据源，一致性守卫是替代方案：此前 Python 侧的
8 个字段在生产代码中零消费，脚本 arm64 分支的 `-cpu max` / `console=ttyAMA0` /
initramfs 三个设置 Python 侧连字段都没有，而 `-cpu max` 直接决定 guest 是否 SIGILL。

仍未收口的是另一半：`image.arch` 的历史登记值仍是 `aarch64`，与落库的 `arm64` 对不上。
这需要一个迁移决策（改历史行 or 改查询口径），本次未做。

### 6.3 失败知识从"只写不读"到读得回（0.3.13）

6.1 那节把仿真侧数字从手工台账收进了表，但只收了**写**的一侧。写进去而没有读者，
闭环仍然不存在：`failure_profile` 每次仿真都写，仓库内没有任何代码按历史失败指导
下一次仿真；`repair_action` 更彻底——表有六列，**零写入路径**，L3 规则每次仿真都在
触发，却没有任何一行记录"这次触发过"。连人都无法回答这张表唯一要回答的问题：
*施加这条规则之后结果变了吗？*

0.3.13 补上两端：

- **写**：`emulate_firmware(applied_rule_ids=...)` 把本次命中的 rule id 交给
  `db.knowledge.record_repairs`，落在**该 run 那一行**上（`source="rule"`）。
  只记真正命中的规则：被提出但没被采纳的修复不是动作，不进动作账本。
  `emulate run` 由 `prepared.matched_rule_ids` 透传，因此 CLI 这一条入口自动落账。
  API 的两个仿真端点**不跑 L3 规则**（它们接受已提取的 rootfs 直接开仿），没有 rule id
  可记，账本为空是实况而不是漏写；值守侧同理，它重启的是既有容器而不是重新配规则。
- **读**：`iris db cards` 把 `failure_profile` 按 kind 聚成根因卡片——多少次 run、
  哪些镜像、哪些架构、第一次到最后一次、以及这些 run 上触发过哪些规则。

两处口径是刻意选的，写在这里以免后人当成 bug 改回去：

1. **信息类被排除**。`network-fallback-ok` 的含义是"注入的网络兜底**按设计生效了**"
   （第 1.3 节的 Web 拉起就靠它）。若按根因排序，它会以 36 次排在第一位并把自己的意思
   反过来。复用 `db.runs` 直方图用的同一份封闭集合排除。
2. **门禁是"最近还在发生"，不是"是否已恢复"**。本语料里没有任何成功 run 带过失败行，
   所以 `recovered` 对每一个 kind 都答"否"，拿它当门禁等于把所有 kind 都排上，等于没排。
   改为统计最近 N 次**失败 run**里仍在出现的 kind（0.3.13 实测语料：N=10 时
   `no-guest-ip` / `web-wrong-port` / `no-network-driver` 三类仍在；N=3 时只剩
   `web-wrong-port`；不带参数时另有 `boot-hooks-missing`、`web-not-started`、
   `container-start-failed` 被标为"最近未见"，这是修复是否生效的第一个可见信号）。

**刻意不做**：不因为历史行自动施加修复。判定一条规则有效的唯一证据是活体运行上的
`Rule.post_action_verify`，被记住的成功不是它。输出是一份"该给哪些失败写确定性规则"
的排序清单，规则仍由人写，下一次运行仍然去证明或推翻它。

两处已知缺口，同样不掩饰：`RuleReport.touched_files` 不落库（`prepare_from_firmware`
只把命中的 rule id 带出来，报告本身不外传），因此回答不了"这次修补动了几处"；
卡片上的 `recovered` 目前恒为 0，那是数据的实况而非结论。

### 6.4 进 guest 的通道，与"运行期写入不再丢弃"（0.3.13）

§4.8 与 §9 反复记着一句话：探针在容器里，被探的东西在 `image.raw` 里，所以运行期
够不到 guest。这句话在 0.3.13 之后需要改一半。

**为什么以前连磁盘都是空的。** `run_qemu.sh` 每次启动都把 `image.raw` 复制一份到
`/tmp/qemu-<iid>.raw` 交给 QEMU，退出时 `rm -f`。guest 的一切写入只落在这份**临时副本**
上，随 QEMU 退出一起消失。于是即便有人能进 guest filesystem，也没有"上一次运行留下的
状态"可看——重启等于从出厂镜像重开。0.3.13 改为 `state.raw`：

- `state.raw` 与 `image.raw` 同目录，**不存在时才复制**，退出时**不删**，QEMU 挂的是它；
- `image.raw` 保持出厂状态。这样被强杀弄脏的状态盘可以直接删掉回到出厂镜像，
  不必重跑 `make_image.sh`，而重烤镜像也永远不会覆盖掉已经注入的修补；
- `make_image.sh` 在重烤后 `rm -f state.raw`——新镜像配旧状态盘是另一种错；
- QEMU 退出后 `e2fsck -p`。值守的 `docker restart -t 10` 是 SIGKILL，ext2 没有日志
  可回放，下一次挂载会直接失败；那看起来就像"固件坏了"，所以这一步必须存在，
  且用 `|| echo` 保证它不会带走后面的 TAP 清理。

**通道本身**：`iris guest ls/get/put`（`src/iris/emulate/guestfs.py`）。在特权仿真
容器内对镜像做 loop 挂载——和 `make_image.sh` 构建期做的同一个操作。三条性质是选择，
不是实现细节：读操作一律 `ro` 挂载；`state.raw` 在 QEMU 运行时**拒绝**访问而不是尝试，
因为把运行中 guest 打开读写着的文件系统再挂一次会损坏它，而损坏会很久以后才以
"无法解释的启动失败"出现；不做任意命令执行（`make_image.sh` 用 chroot + busybox 确实
能跑，但一个能执行任意文本的修补通道是另一件需要评审的事）。

**实测（2026-10-04，DIR-868L / iid 6630 真实容器）**：

1. QEMU 运行中读出厂镜像：`/firmadyne` 列出 13 个条目，`guest get /firmadyne/init`
   读出 `infer_init` 的结果 `/sbin/init`——这是此前**任何代码都看不到**的东西；
2. QEMU 运行中读 `state.raw` 被正确拒绝（`qemu is running as pid 176`）；
3. 停 QEMU（不删容器）→ `guest put` 改写 `/etc/init.d/iris_net_fix`，在脚本第二行插入
   一条 `echo` → `guest get` 读回一致；
4. 重启 QEMU（`Reusing existing state disk`）→ **串口日志出现 `IRIS-REPAIR-PROOF`**，
   即 guest 自己执行了注入的代码；随后补上首启那一版桥地址后 `curl` 得 **HTTP 200**
   （当时的「补地址」是手工的，这条已由 §6.5 收口：现在重启自己会复现那次观测）。

这构成双重证据：**注入的代码被 guest 执行**（串口），**服务仍然可用**（HTTP 200）。

**边界，如实写明**：通道看到的是**磁盘上的文件**，不是运行中 guest 的视图——内存里
缓冲的没变、被挂载覆盖掉的（JFFS2 卷、`/proc`、`/sys`）也不是那张文件。值守**仍然
用不上**这个通道：它要求 QEMU 已停止，而值守的动作都发生在 QEMU 正在跑的时候。

---

### 6.5 首启的宿主观测要能被重启复现（0.3.13）

**为什么它是个值守问题，不只是启动问题**。`run_qemu.sh` 只收三个参数
（`<iid> <arch> <port>`），而它要算两件事：宿主桥地址（假定 guest 地址减一、掩码 /16）
与端口转发目标。首启能拿到 HTTP 200，一半靠 orchestrator 在启动过程中**读串口认出
guest 自己的子网**再把桥地址补进去——那是一次只在启动时发生的观测。重启把容器拉回来
时没有人在旁边看，于是这次观测整个消失，`WEB_SERVER_RESTART` 把一个能服务的 guest
改成不能服务。实测（DIR-868L）：重启后 guest 已按注入的补丁启动，`curl` 却是
`HTTP 000`；手工补一个地址立刻 `HTTP 200`。**一次成功的修复动作，把服务弄坏了。**

**修法**：把首启认出的地址落盘，和 `arch` 标记放在一起。

| 端 | 位置 | 做什么 |
|---|---|---|
| 写 | `orchestrator._record_guest_ip` | 只在**检出值与假定值不同**时写 `/work/scratch/<iid>/guest_ip` |
| 读 | `run_qemu.sh` | 取址顺序：显式第 4 参 → 标记 → `192.168.1.1` 假定 |
| 失效 | `make_image.sh` | 重烤镜像时随 `state.raw` 一起删掉标记 |

三条约束都是踩出来的：

1. **不能把假定值写进去**。`guest_ip` 变量初值就是 `192.168.1.1`，无条件写等于把假定
   盖成"实测"，下一次启动读回来的还是它本来就有的那个值。
2. **标记里的东西会被 shell 算**。它直接喂给 `awk` 算宿主地址和 `ip addr add`，所以
   写入端校验（四段十进制、非 `127.`），读取端再校验一次；读取端校验失败时**退回假定
   而不是让启动失败**（`set -e` 下 `awk` 的非零退出会直接带走整次启动）。
3. **地址来自 guest 自己的 printk**。写文件走 `sh -c` 把地址作为**位置参数**传，不拼进
   脚本文本。

**实测（2026-10-04，DIR-868L / iid 6630，同一容器、同样三参数重跑）**：

| 标记 | run_qemu.sh 打印 | 桥上地址 | `curl` |
|---|---|---|---|
| 在 | `Guest address taken from /work/scratch/6630/guest_ip: 192.168.0.1` | `192.168.0.254/16` | **HTTP 200** |
| 挪走作对照 | `Network: ... host=192.168.1.254 guest=192.168.1.1` | `192.168.1.254/16` | **HTTP 000** |

同一块状态盘、同一次启动流程、同样的三个参数，唯一的差别是那个文件——所以这是因果，
不是巧合。

**留给值守的边界，如实写明**：标记只在**首启真的检出地址**时才有。没有检出的固件
（例如 guest 就住在 IRIS 设的网络里）重启后仍然走假定路径，这与今天一致、不算退化；
但也意味着「标记缺失」与「guest 确实在假定地址上」在容器里长得一样——判不出来，
不要把它当成后者的证据。

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
   默认怀疑。`vendor-watchdog-monitor.yaml` 就是把 TES7002 的教训推广到任意厂商：
   指纹既认 inittab 里的 `::once:-/bin/monitor` 形态，也认 rc 脚本里被直接 exec 的形态。

---

## 8 自愈动作的真实性

前面几条讲的是「怎么发现固件坏了」。这一条讲相反的方向：**IRIS 自己的修复动作，怎么确认
它真的生效了**。0.3.9 之前，这个方向上有一整类缺陷，而且它们有一个共同形状。

### 8.1 三种「声称成功」

在 Tenda TES7002 上追一个 7002 端口的排查过程里，同一个缺陷换了三张脸：

| 版本 | 日志说 | 实际 |
|---|---|---|
| 最初 | `starting telnetd on port 7002` | 二进制都没查，telnetd 从未绑定 |
| 改为函数后 | `fallback shell is listening on :7002 (after 0s)` | 绝对路径查找修对了，telnetd 真的绑定了，但绑的是 IPv6-only，IPv4 一律 refused |
| 改为双信号后 | `no command channel` + 五路证据 | 诚实报告，并指出这是固件限制 |

第二行是最危险的一行：它**比第一行更可信**——它经过了探活、有返回码、有轮询。正因如此，
它能骗过所有只看日志不看连接的人。

### 8.2 结论必须与证据同源

`ensure_command_channel` 的返回码和日志行由**同一个分支**产生，测试对四组 outcome 参数化
断言二者严格配对。理由不是洁癖：一个恒返回 0 的函数可以满足任何只看文本的测试。

反过来也一样——**报告里不能出现由失败探针推导出的结论**。本轮一度用 `process_running`
判定「daemon: gone」，而同时 netstat 显示该 daemon 正在 LISTEN；进程死了 socket 不会留在
LISTEN，所以两者不可能同真，有一个探针是坏的。最终改成把 `pidof` 的原话放进报告
（`pidof said: [466]`）——事实进日志，判断留给读日志的人。这条正是本轮唯一一次**自己
引入的谎报**，值得单列。

### 8.3 探针要报告它看见了什么

失败报告现在是一行五路证据：`pidof` 原话、`netstat -lan` 原文、客户端原话、`bindv6only`
当前值、`/proc/net` 原文。这些不是日志噪音——正是它们把「telnetd 坏了」一步步逼到
「BusyBox 1.22.1 绑 IPv6-only、QEMU 用户态网络不转发 IPv6、BusyBox nc 连 `::1` 都做不到」
这个只能靠实测得出的结论。任何布尔值都无法替代它们。

### 8.4 一个端口在表里，不等于它在监听

`/proc/net/tcp{,6}` 里有该端口，可能是 LISTEN（`0A`），也可能是 TIME_WAIT（`06`）。
按端口号匹配会把后者报成「daemon 活着但拒绝我们」——与谎报成功同型、方向相反的错误。
`port_listening_in_proc` 因此匹配状态位而不只是端口。

### 8.5 探测手段本身要先验证

本轮踩到的三个坑，都是「探针错了，被当成结论」：

- **`nc -w 2` / `nc -6`**：该固件的 BusyBox 1.22.1 只接受 `nc [IPADDR PORT]`，
  两个 flag 都报 `invalid option`，返回码与「连接被拒」完全一样。
  **修法落在测试上**：nc 替身现在复现真实 BusyBox 的拒绝行为。**一个会忽略自身参数的
  替身会让整类探测 bug 溜过去。**
- **`/proc/net/tcp6` 探针的两个盲区**：host 上存在 `timeout` 使脚本永远走被包裹的分支，
  无 `timeout` 的 guest 从未被覆盖；断言写成「数启动行个数」，把条件改成永假照样通过。
  两处都由变异验证暴露——**静态守卫必须做变异验证，否则它守的东西可能已经不在了**。

### 8.6 guest 侧 shell 脚本必须保持 LF

本轮两次栽在同一个坑：编辑工具在 Windows 上把整个 `.sh` 写成 CRLF。host 上完全隐形
（`bash -n` 通过，`.gitattributes` 的 `*.sh text eol=lf` 只管 checkout 路径），guest BusyBox
ash 直接崩，报 `/etc/init.d/iris_net_fix: line 7: : not found`。`tests/test_guest_shell_scripts_are_lf.py`
是为此存在，改完任何 shell 脚本必跑。

### 8.7 修好返回值还不够：探针够不到的动作要停止推荐

0.3.10 修掉 8.1 那类假成功之后，出现了第二层问题：`_apply_watchdog_fixes()` 老实地
返回 `False`，而 `recommend_recovery_action()` 每 30 秒照旧推荐它，`start_continuous_monitoring`
就把一条失败写进 append-only ledger。**一个永远修不好的东西持续刷审计记录，会把真正需要人看的
失败淹掉**——ledger 的价值来自「可信」，不是来自「完整」。

修法是把「探针够不到」变成一个**可记录的状态**而不是一个每次重试的返回值：

- 脚本末尾输出**实际计数**而非标记（`n=0` / 无 `n=` / `n>0` 是三种不同的事实）；
- `n=0` 时把动作记入 `_unreachable_actions` 并附原因，之后跳过它；
- 全部候选都不可达时打出原因并返回 `None`，**不做无效重试**。

`n=0` 的措辞是这里最要紧的部分：它不是「guest 是干净的」，而是「guest 的状态 UNKNOWN」。
探针在容器里，被探的东西在 `image.raw` 里——**找不到不等于没问题，只等于看不见**。

### 8.8 mock 掉被信任的对象，等于没测

`_WATCHDOG_SCRIPT` / `_CLEANUP_SCRIPT` 原有的一整套测试全部用 `FakeExec` 伪造 stdout，
返回 `"WATCHDOG-FIX-APPLIED\n"`。**一个恒 `echo X; exit 0` 的脚本能通过其中每一个断言**——
被测的正是「脚本说了什么」，而测试把它的输出替换成了自己写的字符串。

`tests/test_guardian_repairs_are_honest.py` 因此不 mock：把脚本写到文件、用真实 bash 执行、
通过环境变量喂 fixture rootfs（这也是三个脚本的路径可覆盖的原因，不是为了方便）。
`pidof` / `kill` / `mv` 用 shell 函数替身注入到同一个 shell 里，因为本机是 Windows：
`bash.exe` 的后台 job 的 `$!` 是 Windows pid，而脚本里的 `kill` 解析 MSYS pid，
`cygpath -p` 无法调和两者。**替身必须复现真实行为**——一个会忽略自身参数的 `nc` 替身
在 0.3.9 里就已经让整类探测 bug 溜过去了。

这套测试当场抓到一个连变异验证都没抓到的真 bug：`_CLEANUP_SCRIPT` 默认值赋给
`IRIS_GUARDIAN_PROCESSES`，循环读的却是 `IRIS_GUARDAN_PROCESSES`（少一个 `I`）。
未设置时它展开为空 → 循环一次都不跑 → `n=0`——**与「guest 里确实没有可杀进程」的输出
完全相同**，靠看输出无法区分，靠「把实际被问到的进程名写下来」才抓得住。

### 8.9 变异验证发现盲区的方式：先变绿才是信号

6 项变异中 5 项一次变红，1 项**先变绿**：把 `mv ... && count++` 改成 `mv ...; count++`
（rename 失败也算已禁用）后测试全绿。这说明「计数由 `&&` 守卫」这件事当时**根本没有被测**，
于是补了两项测试（watchdog 与 diag 各一项，用 `mv() { return 1; }` 替身）再复跑，才变红。

先变绿 = 盲区被打开的信号，不是失败的信号。

---

## 9 尚未修复（截至 0.3.10 记录在案）

- **通道路线需要另一条设计**：telnetd 在该固件上只服务一次连接即退出（`TIME_WAIT` 证据），
  且 BusyBox nc 无 `-e`、telnetd 无 `-b`，无法让它绑到 guest 的 eth0。可行方向是 guest 内
  第二串口 + `inittab` respawn，或 QMP。
- **三个修复脚本仍然只能作用于容器可见的对象**（0.3.10 修的是它们**如何汇报**，不是它们
  能修什么）。要真正修 guest 需要能进 guest 的通道。在那之前，探针报 `n=0` 的含义是
  「guest 状态 UNKNOWN」，不是「一切正常」。
  → **0.3.13 部分收口**：进 guest 文件系统的通道已经有了（`iris guest ls/get/put`，
  见 6.4），所以「够不到」不再是通道问题。但**值守仍用不上它**——通道要求 QEMU 已停止，
  而值守的全部动作都发生在 QEMU 正在跑的时候。因此这一条对值守仍然成立：
  `n=0` 依旧读作 UNKNOWN。
- ~~**`WEB_SERVER_RESTART` 不重复首启的宿主观测**~~（0.3.13 实测新发现，**已修**）。首启时
  orchestrator 会从串口日志认出 guest 自己的子网，把宿主桥地址补进那个子网
  （0.3.12 的 `Placed host at 192.168.0.254/24 on br6630`），这是 DIR-868L 能拿到
  HTTP 200 的原因之一；而重启只按 `run_qemu.sh <iid> <arch> <port>` 三个参数重跑，
  桥地址退回默认的 `192.168.1.254/16`。实测：这样重启后 guest 已按注入的补丁启动
  （串口出现 `IRIS-REPAIR-PROOF`），但 `curl` 仍是 `HTTP 000`；手动补上
  `ip addr add 192.168.0.254/24 dev br6630` 后立刻变 `HTTP 200`。
  **修法**：首启认出地址时把它写进 `/work/scratch/<iid>/guest_ip`（与 `arch` 标记同目录，
  见 6.5），`run_qemu.sh` 重跑时按「显式第 4 参 → 标记 → `192.168.1.1` 假定」的顺序取址。
  同一容器、同样三参数重跑的对照实测：有标记时 `Network: ... host=192.168.0.254
  guest=192.168.0.1`、桥为 `192.168.0.254/16`、`HTTP 200`；把标记挪走则退回
  `host=192.168.1.254 guest=192.168.1.1`、桥为 `192.168.1.254/16`、`HTTP 000`。
- **`kill -9` 计数是「信号送达数」而非「确认已死的进程数」**：脚本在容器侧无法复验目标是否
  真的消失。计数语义已在脚本注释里写明，读 ledger 时需要知道这一点。

---

## 10 相关文档

| 文档 | 内容 |
|---|---|
| `docs/05-崩溃归因.md` | 崩溃诊断方法论与 TES7002 案例的完整技术细节 |
| `docs/06-稳定性验证.md` | 稳定性验证方法与长时运行观察记录 |
| `docs/07-架构映射修复.md` | aarch64 → arm64 架构标签映射修复 |
| `rules/*.yaml` | L3 规则库（共 6 条） |
| `CHANGELOG.md` | 版本变更记录，含 Phase 1–4 里程碑与 git tag 对应关系 |