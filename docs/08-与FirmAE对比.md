# IRIS 与 FirmAE 固件仿真对比实测

> 实测日期：2026-10-03（FirmAE 侧）／2026-10-03 第二轮（IRIS 侧，含 0.3.12 修复后重跑）
> ／**2026-10-04 第三轮（0.3.13，语料扩充）**。
> 两侧 FirmAE 数据均为**实跑**，不使用历史手工快照。
> 本文所有数字要么来自命令输出，要么来自固件产物文件，均标注了取数位置。
>
> **本文在 0.3.12 这一轮被修订过两次结论**：DIR-868L 由失败转为成功，
> WRT1200AC/R7800 的根因由「未完全定位」改为「重宿主内核的 BUG」。
> 上一版的两处结论都被推翻，§3.1 写明了推翻过程与证据。
>
> **0.3.13 这一轮又修订了一次**（语料从 6 台扩到 10 台）：WRT1200AC/R7800 的
> **失败位置**由「web 服务不可达」精确为「**web 服务已就绪、二层不通**」，
> §3.1(b-1) 写明了新证据；同时**新发现一个未修的代码缺陷**（inspect 与 emulate 的
> 架构判定不一致），§9 完整记录。这一轮**没有推翻任何一台的成败判定**，
> 但把两台 UBI 固件的失败描述从「服务没起来」纠正了。

## 0. 为什么两侧都是新跑的

`docs/eval-log.md` 的 IRIS 侧表格是 M0 阶段的人工快照，而 `iris.db` 的 `emulation_run`
表在本轮开始时**只有 image_id=11（TES7002, arm64）的 17 条记录**，M0 语料（id 1-6）
没有任何库内记录。也就是说：那张表里的「Newifi D2 HTTP 200 @47s」既无法由 `iris db stats`
复现，也没有对应的 `emulation_run` 行。

拿这种快照去和 FirmAE 的实测数据并列，得到的差异分不清是「两个项目的差异」还是
「快照与当前代码的差异」。因此两侧都在当前代码/当前版本上重跑。

## 1. 两侧环境

| 项 | IRIS | FirmAE |
|----|------|---------|
| 宿主 | Windows + Docker Desktop 29.5.3 | WSL2 `FirmAudit2-Ubuntu`（Ubuntu 22.04.5，18 核 / 16 GB） |
| 版本 | 本仓库 0.3.11 → 0.3.13（`inject_boot_hooks.sh` 与 orchestrator 有多轮修复，见 §6） | `pr0v3rbs/FirmAE` master，源码构建 |
| 内核 | 内置 `vmlinux.{mipsel,mipseb}.4` / `zImage.armel` / `Image.arm64` | **同一套** `vmlinux.*.4`（Linux 4.1.17+，构建串 `firmae@ubuntu`） |
| 数据库 | PostgreSQL 15 容器 | PostgreSQL（5 表 schema 就绪） |
| 单台上限 | `--timeout 300s`（本轮 6 台统一） | `timeout 4500`（0.3.13 轮） |
| 采集口径 | `iris.py emulate run` 退出码 + `emulation_run` + `scratch/emulate-*/qemu.serial.log` | `scratch/<n>/` 下的 `result` / `ping` / `web` / `architecture` / `ip` / `time_*` |

**两侧共用同一套 FirmAE 内核**——这是解读差异时最重要的一条：任何由「内核太老、
模块加载不了」造成的失败，两侧会同时失败，不能算作某一方的优势。

**0.3.13 轮的两条采集纪律**：

1. **两侧都串行，且不与任何其他重负载并行**。仿真是 CPU 密集型的，第二个 QEMU 会
   抢走第一个被计时的 CPU。IRIS 侧 6 台跑完（06:59–07:23 UTC）之后才启动 FirmAE 侧
   批次。采集期间 WSL2 里有两条从 2026-10-03 就存在的 `qemu-mipsel-static ./bin/httpd`
   遗留进程（各占一个核），**IRIS 侧批次期间它们也在**——即两侧环境噪声一致，
   这比「把 FirmAE 环境单独清干净」更公平。`/proc/loadavg` 全程 ≈ 2.0 / 18 核，无争抢。
2. **FirmAE 侧外层上限从 2400 提到 4500**。`scripts/makeNetwork.py:750` 会遍历
   `scratch/<iid>/init` 里的**每一个** init 候选，各跑一轮完整的 infer + check
   （每轮 2 × `TIMEOUT` = 720s）。本语料普遍有 3 个候选 → 单台需要 ≈2160s，
   2400s 不够。第一轮因此把 G1V31si 切在第 3 个候选中间，该轮产物已弃用并重跑
   （重跑后 `time_network=2299.2s`，9 个 qemu 阶段全部完成）。

## 2. 架构支持矩阵

IRIS 的可仿真架构来自 `src/iris/emulate/qemu_config.py`（CLI 直接报出
`supported: armel, arm64, mipseb, mipsel`）；FirmAE 来自 `firmae.config` 的
`check_arch = ("armel" "mipseb" "mipsel")` 白名单。

| 架构 | FirmAE | IRIS | 说明 |
|------|---------|------|------|
| armel | ✅ | ✅ | |
| mipsel | ✅ | ✅ | |
| mipseb | ✅ | ✅ | |
| arm64 / aarch64 | ❌ 白名单里没有 | ✅ | IRIS 自带 `Image.arm64` + `initramfs.arm64` 通用内核通道。**0.3.13 实测补充**：FirmAE 并不是「被挡住进不去」，而是把 arm64 固件误判成 `armel` 后绕过白名单、用 32 位内核跑出 kernel panic（§2 详表） |
| x86_64 | ❌ 架构级不支持 | ❌ 仿真不支持 | IRIS 提取层能识别 `x64`（`m0-baseline.toml` 有记录），但无 x86_64 QEMU 配置 |

「白名单不支持」与「跑了但失败」在本文严格区分：**没有实跑的项目不出现它们的
成功/失败数字**，只在此处列出白名单事实。arm64 在 0.3.13 之前属于前者，0.3.13 之后
属于后者——因为 FirmAE 误判成 `armel` 后确实跑了，而跑的产物（kernel panic 串口）
是本文 §2 详表和 §3 表格的依据。x86_64 至今仍属前者：两侧都没有 x86_64 的仿真配置，
本文没有任何一台 x86_64 固件的实跑数字。

TES7002（arm64）在 IRIS 侧有 17 条历史 `emulation_run`（14 条 web 可达）。0.3.13 这轮
把它（Tenda **US 版** `US_TES7002V1.0re_..._TD.bin`，29 MB）纳入了两侧同批语料，
于是 arm64 这一行第一次有了**同语料的直接对照**，而对照结果**修正了本文上一版的预期**：

| | IRIS | FirmAE |
|---|------|---------|
| 架构判定 | `aarch64`（**正确**） | `armel`（**误判**，真实是 aarch64） |
| 白名单检查 | — | `armel` 在白名单里，**通过** |
| 实际跑的内核 | `Image.arm64` | `zImage.armel`（32 位 ARM） |
| 结果 | **HTTP 302 @96.8s**（`emulation_run` id 77，`web_reachable=1`、`time_web=96`、`arch=arm64`） | 3 个 init 候选全部 **kernel panic**：`Starting init: /bin/init exists but couldn't execute it (error -8)` → `No working init found`（`-8` = ENOEXEC） |

**本文上一版写的是「FirmAE 架构级不支持 arm64，所以进不了仿真」——实测不成立。**
FirmAE 并不是被白名单挡住的：它把这份 aarch64 固件**误判成 `armel`**，
于是 armel 通过了白名单，它真刀真枪地起了 3 轮 QEMU（每个 init 候选一轮），
每一轮都因为 32 位内核执行不了 aarch64 的 `/bin/init` 而 panic。
它的 `makeNetwork.py` 甚至正确地在 rootfs 里找到了 web 服务二进制
（`web service: /bin/boa`），但那个二进制永远没有机会启动。

**显式拒绝优于静默跑错架构**：IRIS 若判错成 mipsel 会同样失败（`inspect` 确实判错了，
见 §9.1），但 IRIS 的仿真路径不解压就没有架构、必须靠 census 才判，而 census 判对了。
这条差异的实质是**第二证据源**，不是谁的架构知识更全。§9.2 单独记录这条对照。

## 3. 逐台对比

前 5 台是 0.3.12 那一轮（时间数字未变，判定描述按 0.3.13 的新证据更新）；
后 4 台是 0.3.13 扩样批次（iid 7001-7006 / FirmAE scratch 6-9）。

| 固件 | 格式 | arch（IRIS / FirmAE） | IRIS 提取 | FirmAE 提取 | IRIS 仿真 | FirmAE 仿真 | 对比结论 |
|------|------|------|-----------|-------------|-----------|-------------|---------|
| Newifi D2 (mt7621) | .bin (uImage) | mipsel / mipsel | ✅ 5,126,594 B | ✅ | ✅ HTTP 200 @62.2s | ❌ result=false | **IRIS 胜** |
| Archer C7 v2 (ath79) | .bin (uImage) | mipseb / mipseb | ✅ | ✅ | ✅ HTTP 200 @49.9s | ✅ result=true, web=true, IP 192.168.1.1 | **平** |
| DIR-868L revB (2016) | .zip → .bin | armel / armel | ✅ 14,762,072 B | ✅ | ✅ HTTP 200 @53.0s | ✅ result=true, web=true, IP 192.168.0.1 | **平** |
| WRT1200AC (mvebu) | .img (uImage+UBI) | armel / — | ✅ 4,713,565 B | ❌ 提取失败 | ❌ `link-no-arp` 317.4s（**`uhttpd` 已 bind :80/:443**） | — 未进入仿真 | **IRIS 胜**（仅提取层，见 §3.1b-1） |
| R7800 (ipq806x) | .img (uImage+UBI) | armel / — | ✅ 5,644,270 B | ❌ 提取失败 | ❌ `link-no-arp` 310.8s（同上） | — 未进入仿真 | **IRIS 胜**（仅提取层） |
| **G1V31si** | .bin (tenda_wrapper) | mipsel / **mipsel ✅** | ✅ tarball 11,133,417 B | ✅ tarball 11,068,244 B | ❌ `link-no-service` 308.8s（ping 通） | ❌ ping=true、**web 文件从未生成**、result=false，IP 192.168.0.1 | **平**（同因失败：固件内无 web 服务） |
| **US TES7002** | .bin (raw squashfs) | **arm64 ✅** / **armel ❌** | ✅ tarball 31,047,651 B | ✅ tarball 30,886,212 B | ✅ **HTTP 302 @96.8s** | ❌ **3 个 init 候选全部 kernel panic**（ENOEXEC） | **IRIS 胜**（架构判定 + 仿真，§2） |
| **RP3V30** | .bin (tendaw, 多分区) | armel / — | ✅ tarball 10,898,954 B | ❌ **900s 内未完成**（见下） | ❌ `link-no-service` 309.1s（ping 通） | — 未进入仿真 | **无法判定**（FirmAE 侧未定位） |
| **i27V11br** | .bin (tenda_wrapper + 加密 FIT) | unknown / — | ❌ rc=2，1s 早退 | ❌ `Extracting root filesystem failed!`，9s | — 未进入仿真 | — 未进入仿真 | **平**（两侧都不行：需厂商密钥，§4.2） |

**两侧 tarball 字节数并不相等**（G1V31si 11,133,417 vs 11,068,244，相差 65,173；
US TES7002 31,047,651 vs 30,886,212，相差 161,439），差值在 0.3%–0.6%。**tar 是归档
格式，字节数受 mtime、权限、条目顺序、符号链接目标写法影响，两侧的归档器不同，
所以字节数不同既不说明内容不同也不说明内容相同。**本文**没有**对两侧 rootfs 做
逐文件比对，因此「两侧提取出的是同一份 rootfs」是合理推断，**不是已验证事实**——
这一条限制写在这里，避免后续引用本文时把它当成验证过的结论。

**RP3V30 的 FirmAE 侧为什么写「无法判定」**：不是提取失败，是**提取没结束**。
FirmAE 的 extractor 在这台多分区固件上停不下来，我们把它自己内建的
`timeout 300` 放宽到 900s（并补装了 binwalk 需要的 `jar`——Ubuntu 的
`default-jre-headless` 是 JRE，**不含 `jar`**），仍然在 900s 被 SIGINT（rc=130）。
三次运行的 Python 栈**位置完全相同**，且每次都停在同一处：

```
extractor.py:730 in _check_recursive   ->  if new_item.extract():
extractor.py:474 in extract            ->  self._check_recursive(module, entry)
extractor.py:730 in _check_recursive   ->  if new_item.extract():      # 自我递归
extractor.py:725 in _check_recursive   ->  new_item = ExtractionItem(...)  # 每层新建连接
extractor.py:241 in __init__           ->  psycopg2.connect(...)
KeyboardInterrupt
```

`_check_recursive` 递归调用自身，且**每一层都新建一条数据库连接**，15 分钟内栈帧
没有前进。**归类是「未定位」**：栈形状像递归不收敛，但本文没有证据断定它是
无限递归还是「有大量候选 item 要逐个建连接」，也没有排除「binwalk 对该分区表
产生了异常多的候选项」。**可以确定的只有两件事**：与 IRIS 无关（IRIS 侧同一固件
14.6 MB 提取成功），以及这不是「FirmAE 不支持这个格式」——它连提取都没结束。
测完后已把 `run.sh` 的 `300` 恢复原状（无残留标记）。

**IRIS 侧六行的取数位置**：权威来源是 `iris-home/iris.db` 的 `emulation_run` id 76-80
（`iid` 7001/7002/7003/7005/7006，字段 `web_reachable` / `ping_reachable` /
`time_web` / `result_kind` / `arch` 可直接复查）；串口证据在
`iris-home/scratch/emulate-700{1,2,3,5,6}/qemu.serial.log`。
i27V11br 提取失败**不落库**（§4.2），它的证据是当轮的批次日志 `/tmp/iris-batch/*.status`
——**这是临时目录，可能已被清理**；引用该台的结论时请以 §4.2 引用的错误原文为准。
**FirmAE 侧取数位置**：`/root/firmae/scratch/6`（G1V31si）、`scratch/7`（US TES7002）、
`scratch/9`（i27V11br，其 `result` 文件内容就是 `extraction fail`），以及各自的
`makeNetwork.log` / `qemu.final.serial.log`（持久目录，本文写作时仍在）。
RP3V30 **没有 scratch 目录**——它的 `get_iid` 没拿到 IID，这本身就是「提取没结束」
的旁证（`run.sh` 里 `mkdir` 在 `get_iid` 之后）。

DIR-868L 与 WRT1200AC/R7800 的成功/失败都是实测数字，根因与证据见 §3.1 与 §6.2；
DIR-868L 在此之前于同一份语料上是失败的。

### 3.1 IRIS 侧失败的分类（不允许混为一谈）

0.3.12 这一轮把上一版这里的**两条结论都推翻了**，并且其中一条推翻的方式是
「上一版引用的证据本身没错，但结论建立在一个从未被检验的前提上」。因此下面先写
现在的结论，再写它推翻了什么。

**（a）DIR-868L：已修复，HTTP 200 @53.0s——根因是宿主不在 guest 的子网内**

上一版把这台判为「兜底被厂商收尾脚本饿死」，并称「guest 全程没有非 loopback
地址」。**两句都不成立。**现在的实测：

- guest 有网卡、有地址、有 Web。串口里 `eth0` / `eth0.1` 进入 promiscuous、
  `br_add_if ... br:br0 dev:eth0.1`、`8021q: adding VLAN 0 to HW filter on device eth0`
  都在；`br0` 在 t=16.9s 拿到 **192.168.0.1**（`__inet_insert_ifa ... device:br0
  ifa:0x0100a8c0`，小端解码）；`httpd` 在 t=40.8s `inet_bind ... port:80`。
- 上一版的判据之所以没看见这些，是因为 `no-guest-ip` 与 `no-network-driver` 两个
  探针的正则都只认一种拼写：前者只认 `inet_insert_ifa: dev X`，不认 FirmAE 改写过的
  `__inet_insert_ifa[PID: 10045 (ip)]: device:br0`；后者只认行首 `eth0:` 与
  `dev eth0`，不认 `device eth0 entered promiscuous mode`、`dev:eth0.1`。
  **两个探针同时误判，且互相印证**，于是「没有网卡」和「没有地址」一起被写进了结论。
- 真正的阻塞点在宿主侧。`run_qemu.sh` 把宿主桥放在**假定**的 guest 地址减一、
  掩码 /16，即 `192.168.1.254/16`；而这台路由器的 LAN 是 `192.168.0.0/24`。
  分层探活（容器内实测）：

  | 探测 | 结果 |
  |------|------|
  | `ip route get 192.168.0.1` | `dev br6630 src 192.168.1.254`（路由正确） |
  | `ip neigh` | `192.168.0.1 lladdr 00:de:fa:1a:01:00 REACHABLE`（**L2 通**，guest 回了 ARP） |
  | `ping 192.168.0.1` | 2 发 0 收 |
  | `curl http://192.168.0.1/` | `000` |
  | 桥上 `ip addr add 192.168.0.254/24` 之后 `ping` | 2 发 2 收 |
  | 同一条命令之后 `curl http://192.168.0.1/` | **`200`** |

  即：帧到了、guest 应答了，但源地址不在 guest 自己的 /24 内，被它静默丢弃——
  而路由器正是这样被设计成工作的。同一条已经跑着的 socat 转发（`TCP:192.168.0.1:80`）
  在加地址后立刻返回 200，无需改动。因此这不是转发配错，是**宿主的地址选错了位置**。

修复见 §6.2。上一版「兜底退化为单通道且被厂商收尾脚本饿死」的判断，实测不成立：
该固件的 `/etc/init.d/rcS` 尾部确实以 `/etc/init0.d/rcS` 收尾，但注入行已被改为
**插在 rcS 最后一条非空非注释行之前**（`inject_boot_hooks.sh` 的 rcS 插入式通道），
厂商收尾脚本不再挡在前面。

**（a-2）DIR-868L 上兜底脚本本身跑不起来——真实存在，但当时不是阻塞点**

这台固件的 BusyBox 是厂商极简定制版，从镜像里提取 `/bin/busybox` 做字符串扫描，
applet 清单实测如下：

- **有**：`sh ash cat awk sed grep cut tr wc printf echo test mkdir rm cp mv ln sleep
  kill expr basename ifconfig route netstat dd ls seq mount umount insmod lsmod
  modprobe mknod chmod ps wget vi tar gzip timeout free yes`
- **缺**：`head tail sort uniq dirname iptables nc netcat stat od readlink find
  xargs env chown stty setsid tty dmesg hexdump cmp tee`（`type` 亦为 not found）
- 参数展开残缺：`x=/a/b/c; echo "[${x%/*}]"` 输出 `[]`；`$(echo hi)` 行为异常。
- **该 ash 不支持 shell 函数定义**：把 `log()` 换成单行 `log() { echo hi; }`、
  换成多行、或整体删除，探针输出位置随之变化——只要存在任何函数定义，其后的代码
  就不执行。`iris_net_fix.sh` 通体基于函数，因此在这台固件上原理上跑不起来。

这条**已定位且如实记录**，但它不是 DIR-868L 失败的原因：宿主的 socat 转发直接指向
guest 自己的 `httpd`，压根不需要兜底脚本。上一版把「兜底跑不起来」直接写成
「能力不足」并当成整体失败的解释，是把一个次要限制当成了主因。

**（b）WRT1200AC / R7800：根因已定位——重宿主内核自身的 BUG，与 IRIS 代码无关**

两台表现完全一致，且是同一个根因。证据（`emulate-6714` / `emulate-6715` 串口日志）：

- t≈1.4s：`__inet_insert_ifa[PID: 118 (ip)]: device:eth0 ifa:0x0101a8c0`
  → eth0 一度拿到 **192.168.1.1**（正是 IRIS 假定的地址）。
- t≈122s（WRT1200AC）/ t≈102s（R7800）：
  `netifd (1056): undefined instruction: pc=c01abe18`，紧接
  `kernel BUG at /usr/src/firmadyne_kernel-v4.1/lib/nlattr.c:41!`，
  `PC is at validate_nla+0x3c/0x1a0`、`LR is at nla_parse+0xdc/0xf4`，
  `Code: ... (e7f001f2)`（`e7f001f2` 即 `udf`，是 `BUG()` 陷阱，不是二进制真的跑到
  非法指令上）。
- 两台的 `pc` 完全相同（`c01abe18`）、文件行号完全相同（`nlattr.c:41`）。
  即：**重宿主内核（FirmAE 4.1.17）在 netlink 属性校验器里 BUG 掉了 netifd。**
- 后果链：netifd 在持有 rtnl 锁的情况下被内核 BUG 打死 → 之后任何地址操作
  （`ifconfig eth0 192.168.1.1`）永久阻塞。实测支持这一点：兜底日志里
  `ifconfig` 出现在 t=118.77s，`address probe finished` 出现在 t=123.7s，
  差值 ≈5s，正好是 `run_bounded` 的上限，即该调用是被超时 kill 的，从未返回。
- 时序上兜底**晚于**崩溃（`kernel BUG` 在日志第 629 行，兜底第一行在第 731 行），
  所以崩溃不是兜底触发的。
- 也没有「早抓一个窗口」的机会：`uhttpd` 绑 `:80` 发生在 t≈121.8s，
  而那时 eth0 的地址已经没了。

**分类：环境适配失败，且失败点在重宿主内核而不在 IRIS 或固件。**这条无法在 IRIS 内
修复——要绕过它得换掉 `zImage.armel` 或给内核打个补丁，那是重宿主内核的维护范围。
上一版写的「netifd 持有 eth0 device lock 导致 ifconfig 永不返回」是**症状**，
描述对了现象但归因错了对象：锁是被这次内核 BUG 泄漏的，不是 netifd 正常持有。

#### (b-1) 0.3.13 重测修正：这两台的 Web 服务其实**起来了**，HTTP 000 不代表服务未启动

上一版把这两台记作「HTTP 000」，读者容易读成「web 服务没起来」。0.3.13 用带分层
判定的重跑（iid 7005 / 7006）证明**服务是就绪的，失败发生在宿主↔guest 的二层**。
串口证据（`scratch/emulate-7005/qemu.serial.log`、`emulate-7006/qemu.serial.log`）：

| iid | 固件 | `dropbear :22` | `uhttpd :80` | `uhttpd :443` | `validate_nla` BUG | 兜底结局 |
|----|------|---------------|-------------|---------------|-------------------|---------|
| 7005 | WRT1200AC | t=127.06s | t=131.29s | t=131.33s | t=132.36s（`pc=c01abe18`，`nlattr.c:41`） | `vendor web server is already running, leaving :80 to it` → `done` |
| 7006 | R7800 | t=119.14s | t=123.38s | t=123.39s | t=124.02s（同一 `pc`、同一行号） | 同上 |

即三次 `inet_bind ... port:80/443` 都真实发生，且兜底自己都判定「厂商 web 服务已在运行」
并主动让出 `:80`。地址侧的顺序也一致：t≈1.4s eth0 拿到 `192.168.1.1`
（`__inet_insert_ifa ... ifa:0x0101a8c0`）→ BUG 打死了 netifd → 兜底
`no non-loopback address, assigning fallback 192.168.1.1 to eth0` →
`address probe finished (has_ip=0)` → `final: guest still has no non-loopback address`。

**因此上一版「没有早抓一个窗口的机会」那句只对 6714/6715 那两次成立，在 7005/7006 上
被推翻**：这两次 `uhttpd` 绑 `:80` 发生在 BUG **之前** 0.07s / 0.64s，窗口是存在的，
只是那时 eth0 的地址已经被 BUG 打掉了，宿主仍然探不到。**根因（重宿主内核的
`validate_nla` BUG）不变，变化的是失败位置的精确描述**：不是「服务没起来」，
而是「服务就绪 + 地址丢失 → 二层不通」。

这也正是 0.3.13 分层判定（`result_kind`）带来的表达力：两台在 `emulation_run` 里
落的是 `link-no-arp`（id 79 / 80），而不是笼统的失败。`link-no-arp` 的含义是
**ARP 层就没有应答**——与服务是否启动无关。旧的单一「HTTP 000」口径把这两类
完全不同的情况压成了同一个符号。

**（c）G1V31si / RP3V30：网络层完全正常，失败在「固件里根本没有 web 服务」**

这两台是 0.3.13 新增的语料（vendor Tenda/Anyka buildroot，不是 OpenWrt 官方快照），
失败方式与 (b) 完全不同，`result_kind` 落的是 `link-no-arp` 之外的第三种：
**`link-no-service`**（`emulation_run` id 76 / 78）。分层含义是
**二层通、地址已配好，但 guest 内没有任何进程在监听 Web 端口**。
串口证据（`scratch/emulate-7001/qemu.serial.log`、`emulate-7003/qemu.serial.log`）：

| iid | 固件 | 地址 | 命令通道 | `:80` 探测（串口原文） | 判定 |
|----|------|------|---------|---------------------|------|
| 7001 | G1V31si (mipsel) | t=16.27s `ifconfig` 配出 `192.168.1.1`，`final: guest has a non-loopback address` | ✗ `/sbin/telnetd` 起来了但 `:7002` 5s 内无应答（`pidof: not found`，该 BusyBox 无此 applet） | `probing for a web server on :80` → `no web server fallback available` | `link-no-service` + `WEB_NOT_STARTED`（detail 是 `orchestrator.py:210` 的常量「no known web server process ever started in the guest」） |
| 7003 | RP3V30 (armel) | t=26.86s `ifconfig` 配出 `192.168.1.1`，`final: guest has a non-loopback address` | ✓ `telnetd` bind `:7002` 成功，兜底自证「fallback shell is listening on :7002」 | 同上 | 同上 |

**关键区别：这两台的网络层是健康的**——IRIS 自己配的地址被内核接受
（`__inet_insert_ifa`），RP3V30 上兜底的命令通道（telnetd:7002）甚至完全跑通了。
失败的是**最后一环：guest 里没有可监听的 HTTP 服务，而 IRIS 的兜底也无法凭空造一个**。

**能力边界的准确范围**（`scripts/emulate/iris_net_fix.sh:659-688`，实测读码）：兜底的
web 兜底**不是通用探测，而是一条 `if/elif` 链，只认两种硬编码的厂商组合**——
`/opt/goahead/goahead` + `/opt/goahead/route.txt`（Tenda goahead）与
`/usr/bin/boa` + `/etc/boa/boa.conf`（boa）。两个都不满足才打印
`no web server fallback available`。**它不扫 `/bin`、`/usr/bin`，也不找 busybox
的 httpd applet**——所以「IRIS 兜底能在任意固件上造出一个 web 服务」是不成立的，
这一条必须按上面的实际范围理解。US 版 TES7002 之所以能成，正是因为它是 Tenda
固件、带 `/opt/goahead`（串口里兜底自己走的就是 `launching goahead` 分支）。

**分类：语料属性 + 兜底能力边界，不是网络层缺陷。**严格地说这不是「IRIS 失败」——
没有任何 HTTP 服务可以失败。要判定 IRIS 在这两台上的能力，正确的问题是
「兜底能否在没有厂商 web 服务时自建一个」，答案是不能（只认 goahead / boa 两种
硬编码组合，见上），且**这是有意的能力边界**：IRIS 不自带 httpd 注入
（那会改变被测设备的行为），只复用固件自带的、且只认已知的两种厂商实现。
RP3V30 上的 `telnetd` 兜底成功说明命令通道这一半是通的。

一个跨固件的旧旁证仍**未定位**，保留记录：TES7002（`emulate-9017`）与 Newifi D2
（`emulate-1607`）的兜底日志都在 `final: eth0 up with IP` 之后停止，而 9017 最终走到了
`done`。这两台**整体是成功的**，所以这处静默终止目前只影响日志可读性，不影响判定。

## 4. 提取能力边界

### 4.1 IRIS 能、FirmAE 不能：UBI 包装的 Squashfs

WRT1200AC / R7800 的 rootfs 不是裸 squashfs，而是 UBI 卷里装着 squashfs：

- `binwalk` 在偏移 `0x600000`（6,291,456）看到 UBI EC header，与 `docs/eval-log.md`
  记录的 UBI 偏移一致。
- `file` 对 `ubireader_extract_images` 取出的 volume 判定为
  **Squashfs 4.0 xz, 3,608,158 bytes, 1357 inodes**——里面确实是 squashfs，
  但它被 UBI 包了一层。

**FirmAE 侧的失败机制（精确表述）**：

- `sources/extractor/extractor.py` 的 `main` 只在 `arg.debug` 或 `psql_check(arg.sql)`
  时真正提取，本轮数据库正常，所以不是 DB 问题；加 `-d` 复跑仍反复命中同一 UBI
  后 `>> Cleaning up`，无产出。
- `grep -rn "ubi\|UBI" sources/extractor/*.py` → **FirmAE extractor 里没有任何 UBI
  处理代码**，完全依赖 binwalk 的 `extract.conf` 插件
  （`^ubi erase count header:ubi:ubireader_extract_files ...`）。
- 对整个 UBI 镜像调 `ubireader_extract_files` → `UBIFS Fatal: Super block error:
  Wrong node type.`
- **可行的路径其实存在**：`ubireader_extract_images` 能取出 volume，两步走
  （images → files）对取出的 `.ubifs` 也失败（`guess_start_offset Fatal`），但
  `file`/`binwalk` 证明其中就是 squashfs。

因此准确说法是：**FirmAE 所依赖的 binwalk UBI 插件路径对该固件形态失败；能处理这个
形态的路径（`ubireader_extract_images`）在环境里可用但 FirmAE 没有走，也没有
fallback 到按 magic 切 squashfs。** 不是「FirmAE 不支持 UBI」。

0.3.13 这一轮的产物文件把这件事又钉了一遍：`scratch/4`（WRT1200AC）与
`scratch/5`（R7800）的 `result` 文件内容就是字面的 `extraction fail`——**这一支只在
提取失败的机器上出现**。作为对照，提取成功的六台
（`scratch/1` Newifi D2、`2` DIR-868L、`3` Archer C7 v2、`6` G1V31si、
`7` US TES7002，以及 `9` i27V11br 之外的正常路径）的 `result` 要么 `true`
（DIR-868L、Archer C7 v2）要么 `false`（Newifi D2、G1V31si、US TES7002），
**没有第三种形态混进来**。所以本文表格里「未能进入仿真」那一栏不是推测，
是产物文件里的一个明确取值。

**IRIS 侧**：`src/iris/extract/ubi.py` 解析 PEB/VID header，按 vol_id+lnum 拼接 LEB，
再切出内嵌 squashfs 交给 alpine+unsquashfs，本轮两台均提取成功（tarball
4,713,565 / 5,644,270 字节）。这是 IRIS 相对 FirmAE 的**明确、可复现的能力差异**。

### 4.2 两侧都不能：厂商加密的 FIT（i27v11br）

`i27V11br.bin`（Tenda i27 v11，13.4 MB）在 **IRIS 侧提取失败，1 秒早退**
（当轮批次脚本记录：`rc=2 elapsed=1s`，日志在临时目录 `/tmp/iris-batch/`），
错误信息本身是可诊断的、也是权威证据：

```
[error] extraction failed: encrypted-fit: image wraps a FIT containing
        38 YZTenda-encrypted segments; rootfs is not extractable without
        the vendor decryption key
```

`iris extract inspect` 对它的判定是 `tenda_wrapper` 容器、`arch unknown`、内层为
FIT image 且 38 个数据段全部标记为 `YZTenda` 加密。**没有厂商密钥就没有 rootfs，
两侧都做不到**——这不是谁的短板，而是语料本身的加密。

一个**工程缺陷**（与能力无关，但影响对比数据的完整性）：提取失败**不落
`emulation_run`**。`emulation_run` 只有成功进入仿真的行，iid 7004 在库里没有对应记录
（本轮 id 76-80 对应 iid 7001/7002/7003/7005/7006，独缺 7004）。因此「IRIS 侧跑了几台」
不能只查 `emulation_run`，提取阶段的早退需要另找记录（批次日志 / `firmware` 表状态）。
**这条如实记为待改进项**：失败也该落库，否则批次级对比会静默漏掉提取失败的样本。

## 5. 逐项复盘：FirmAE 曾在 DIR-868L 上胜出，现已追平

### 5.1 DIR-868L：FirmAE 当时胜出，机制不同

FirmAE 的 `makeNetwork.log` 显示它**正确推断并处理了 VLAN**：

```
Interfaces: [('br0','192.168.0.1'),('br1','192.168.7.1'),('eth0','10.0.2.15')]
networkInfo: [('192.168.0.1','eth0',1,None,'br0')]
```

第三个字段 `1` 就是 VLAN tag——FirmAE 把 eth0.1 接进了 br0，并给出
`filter network info: [('192.168.0.1','eth0',None,None,'br0')]` 作为对外接口。

**需要说清楚的是：FirmAE 赢的不是 VLAN。**IRIS 在这台上的厂商脚本做了同样的桥接
（串口可见 `br_add_if ... br0 dev:eth0.1`、`8021q: ... device eth0`），桥接从来不是
问题。FirmAE 赢的是**把宿主放进了 guest 自己的子网**——它的 `10.0.2.15` 与
`192.168.0.1` 同在 QEMU 的 user-mode 网络里，而 IRIS 把宿主放在 `192.168.1.254/16`，
与 guest 的 `192.168.0.0/24` 不重叠。IRIS 修的正是这一条（§6.2），修法与 FirmAE
不同：FirmAE 靠 QEMU slirp 的虚拟网段天然同网段，IRIS 用 TAP+bridge，只能自己补地址。

### 5.2 Newifi D2：FirmAE 失败，IRIS 成功（结论不变）

FirmAE `result=false`，但 `ping` 成功、`uhttpd` 起来了（串口
`inet_bind[PID:1300 uhttpd] ... port:80` @42.7s）。`makeNetwork.log` 同时显示：

```
Interfaces: []
ports: []
networkInfo: []
```

即接口/端口/网络信息三项全空 → 退到「bring up default network」，直接假设
`192.168.0.1` 并把它配到 `br0`。而 Newifi D2 的实际 LAN 是 **192.168.1.1**
（IRIS 侧 `emulate-6712` 实测 HTTP 200 即为此）。**FirmAE 的网络地址推断在这台上是错的**，
ping 通的是它自己假设的那个地址，Web 起在真实地址上，转发自然打不通。

> 注：本轮开始时 IRIS 在这台上也失败（HTTP 000，`IRIS-NETFIX` 0 行），根因见 §6.1。
> 修复后 IRIS HTTP 200 @62.2s，FirmAE 仍为 false。

## 6. 两轮修复

### 6.1 0.3.11 的修复：procd 的 sysinit 通道会顶掉厂商 rcS

**症状**：Newifi D2 仿真失败，串口唯一线索是 `procd: valid format is rcS <S|K>
<param>`，随后 guest 只剩每秒一次的 `sys_socket family:1 type:2` 空转；
`IRIS-NETFIX` 0 行。

**根因（procd 源码级）**：`procd_inittab_run()` 遍历 action 列表，命中即
`break`，除非 handler 带 `multi` 标记——`sysinit`/`shutdown` 都没有。`sysinit` 的
handler 是 `runrc()`，它要求 `<process> <S|K> <param>` 三段齐全，否则报错并 return。
而 `inject_boot_hooks.sh` 注入的是两段的
`::sysinit:/etc/init.d/iris_net_fix_bg`。两条合起来：

1. 注入行排在 `::sysinit:/etc/init.d/rcS S boot` 之前 → 成为唯一的 sysinit action；
2. `runrc` 参数不足 → 报错 return，**IRIS 自己的兜底没跑**；
3. `break` 已经发生 → **厂商的 rcS 永远不会被执行**。

对 OpenWrt 而言后果是 guest 既没有厂商的 netifd/uhttpd，也没有 IRIS 的兜底，
而日志里只有 procd 的那一行抱怨。

**对照实验（同一份镜像，只删掉注入的两行）**：该行报错消失，`netifd` 于 30.7s 起来，
`uhttpd` 于 37.2s `inet_bind port:80`。这排除了「固件自身起不来」的可能。

**修复**：`inject_boot_hooks.sh` 先按 procd 自己的要求读回 inittab——`sysinit` /
`shutdown` 行是否带 `<S|K> <param>` 尾参（3 个以上字段）。是则**不注入 sysinit 条目**，
改为确保 `/etc/rc.d/S99iris_net_fix` 存在，因为 procd 自己遍历 `rc.d/S*` 且没有 rcS
文件可追加。BusyBox init 的 inittab 从不在 sysinit 行带额外参数，所以两者不会混淆；
判定只看 `sysinit`/`shutdown` 行，避免把本来就多字段的 `respawn` 行误判成 procd。

**验证**：
- `tests/test_boot_hooks.py::TestProcdInittab` 新增 10 项，全部跑真实脚本。
- 5 项变异验证如期变红：`seen>=4`（8 红）、只认 sysinit 不认 shutdown（1 红）、
  去掉 action 过滤（8 红）、链接目标改成 bg 版（2 红）、去掉陈旧条目清理（1 红）。
- 真机回归：Newifi D2 `emulation success`，`HTTP 200 after 75s`，串口出现
  `IRIS-NETFIX: final: lo up with IP` / `final: eth0 up with IP`——兜底经 rc.d 通道生效。
- baked image 指纹从 `9358dc207ef7` 变为 `b2112090961b`，旧 tag 由构建流程自动清理。

**同时修掉的次生缺陷**：`ln -s` 失败曾让整个注入脚本在 `set -e` 下非 0 退出，
后面的 rcS tracing 与 tail hook 全部不执行。现在链接失败只打印明确警告（宿主不能建
符号链接 → guest 将拿不到兜底），不伪装成功也不中断后续步骤。

### 6.2 0.3.12 的修复：宿主必须在 guest 自己的子网内

**症状**：DIR-868L 上 guest 的网络、Web、地址全部正常，宿主转发也已经指向
`192.168.0.1:80`，但 HTTP 恒为 000（§3.1a 的分层探活表）。

**根因**：`run_qemu.sh` 按「假定的 guest 地址减一、掩码 /16」给宿主桥配地址
（`ip addr add 192.168.1.254/16`）。这个假设对「guest 的网络就是 IRIS 搭的那个网络」
的固件成立，对**路由器**不成立——路由器会按自己的配置起一个 LAN 子网。宿主地址落在
guest 子网之外时，guest 会按路由器的本职丢弃这些包；而 IRIS 的两个探针又恰好看不见
guest 其实有网有地址（§3.1a），于是失败被归到了错误的方向上。

**修复**：`_place_host_on_guest_subnet()`——检测到 guest 地址后，在同一座桥上再加一个
该子网内的地址（`192.168.0.254/24`）。内核会自行按「目的地址所在子网」挑选源地址，
因此已经跑着的 socat 转发无需任何改动即可生效（实测确认）。两处细节：

- 加地址**在**起转发**之前**，否则转发存在但打不通；
- 用 `placed_subnets` 按地址去重，避免每 5s 轮询重复 `ip addr add`；
- `ip addr add` 遇到 `File exists` 退出码 2 视为成功（重试 boot 时属正常）；
- 真实失败（如桥不存在）只记 debug 日志、不升级为 boot 失败——这个地址只是让 guest
  的包过滤器放我们进来，转发本身并不依赖它。

**为什么是 /24**：IRIS 能读到的唯一地址声明是内核的 `inet_insert_ifa` printk，
它不带前缀长度，因此 /24 是假设而非推导。路由器 LAN 绝大多数是 /24，DIR-868L 实测
也是。若某固件的 LAN 用别的前缀，本办法不覆盖——这一点写在这里，不假装覆盖。

**这次观测只发生在首启**：容器重启（值守的 `WEB_SERVER_RESTART` 就是它）之后没有人在
旁边看串口，`run_qemu.sh` 又只收三个参数，于是桥地址退回假定值——实测重启后
`HTTP 000`，手工补 `ip addr add 192.168.0.254/24 dev br6630` 立刻 `HTTP 200`。
0.3.13 起首启认出的地址会落进 `image.raw` 同目录的 `guest_ip` 文件，`run_qemu.sh`
重跑时优先读它，所以重启会自己复现这次观测（同一容器三参数重跑的对照实测：有标记
`192.168.0.254/16` → HTTP 200，把标记挪走 → `192.168.1.254/16` → HTTP 000）。

**验证**：
- `tests/test_guest_subnet_placement.py` 16 项，含地址算术、幂等、失败降级、
  以及「非 /24 前缀不覆盖」这一局限的说明。
- `tests/test_boot_diagnosis.py::TestGuestNicAndAddressProbes` 7 项，用真实串口日志行
  作样本，锁住两个曾同时误判的探针。
- 真机回归：DIR-868L **HTTP 200 @53.0s**（修复前同语料失败）；Newifi D2 62.2s、
  Archer C7 v2 49.9s 均保持成功，确认对既有成功路径无副作用。

### 6.3 0.3.12 的修复：两个误判的诊断探针

`no-guest-ip` 只认 `inet_insert_ifa: dev X`，`no-network-driver` 只认行首 `eth0:` 与
`dev eth0`。DIR-868L 的真实日志用的是 FirmAE 改写过的
`__inet_insert_ifa[PID: 10045 (ip)]: device:br0` 和
`device eth0 entered promiscuous mode` / `dev:eth0.1` / `8021q: ... device eth0`，
两个探针同时看不见，而**它们互相印证**，于是「没有网卡」和「没有地址」一起被写进了
上一版的结论。补齐这些拼写后，另加一条排除规则：同一行若写着 `not found` /
`no such device`，则不计入网卡证据。

### 6.4 0.3.12 的修复：guest 侧脚本对残缺 BusyBox 的兼容

这些不是 DIR-868L 成功的原因（§3.1a-2），但都是实测存在的真实缺陷，且会让兜底在
部分固件上静默失效：

- **`iris_net_fix_bg.sh` 末尾缺换行**。BusyBox ash 会丢掉脚本最后一行，而那正是启动
  兜底的那一行；整个 launcher 在此之前完全静默，于是固件表现为「boot hooks 没跑」，
  而 hook 安装其实是对的。已修，并加末尾换行守卫。
- **注释里的 shell 元字符会被当代码解析**。实测：`iris_net_fix.sh` 头部注释结尾的
  `&` 让脚本停在第一条语句之前（逐行探针定位）。已清掉两个 guest 脚本注释中的
  反引号、`${`、`$(`、`&`、`<`、`>` 与非 ASCII 字符，并加守卫；守卫已做变异验证。
- **默认值展开在该 BusyBox 上返回空**。`${var:-x}`、`${var:=x}` 全部改写为
  `$VAR` + `[ -n "$VAR" ] || VAR=x`；`${0%/*}`（bg launcher 求自身目录）改为宿主侧
  `sed` 烘焙路径占位符；`acquire_lock` 里取父目录改用 `sed`。加守卫禁止这两个
  家族的展开重新出现（正则含数字变量名，否则 `[0-9]` 这类名字漏拦）。
- **`head` applet 不存在**。新增纯内建的 `first_line()`，替换 4 处 `head`。
- **`/dev/console` 显式打开在该固件上失败**。`log()` 改为两通道回退：
  先显式写 console，失败则继承 stdout（stdout 是 init 已经交出去的描述符）。
- **baked image 加 `--pull=false`**：拉取失败时降级为本地重建，而不是整轮失败。
  这一条是防御性加固，不是某个实测故障的修复。

## 7. 耗时不可直接比较

| 固件 | IRIS 总耗时 | FirmAE wall | 为什么不可比 |
|------|-------------|-------------|-------------|
| Newifi D2 | 62.2s | 1555s | IRIS `--timeout 300`，到点即退；FirmAE 内部 `2×TIMEOUT`，且要跑完自己的完整判定流程 |
| Archer C7 v2 | 49.9s | 1229s | 同上 |
| DIR-868L | 53.0s | 479s | 同上 |
| WRT1200AC | 312.8s（失败） | — 未能进入仿真 | IRIS 走满 timeout 才判失败 |
| R7800 | 246.0s（失败） | — 未能进入仿真 | 同上 |
| G1V31si | 308.8s（失败） | 2325s（`time_network` = 2299.2s） | 见下方「FirmAE 耗时结构」 |
| US TES7002 | **96.8s（成功）** | 2327s | 同上；FirmAE 每一轮都 kernel panic，但仍把 3 个候选全跑完才退出 |
| RP3V30 | 309.1s（失败） | **≥900s，且没进入仿真** | 不可比：FirmAE 侧卡在提取阶段 |
| i27V11br | 1s（提取失败） | 9s（提取失败） | **这一行可比**：两侧都在提取阶段早退，量级相同 |

**FirmAE 耗时结构（0.3.13 实测，用来解释上表）**：`scripts/makeNetwork.py:750` 会
遍历 `scratch/<iid>/init` 里的**每一个** init 候选，每个候选跑两段各
`TIMEOUT`（`firmae.config` 的 360s）：一段 `Infer test`、一段 `Check inferred
emulation`（`Inferred network` 与 `Waiting web service` 共享这一段）。
本语料普遍有 3 个候选 → 3 × 720s = **2160s**，加上提取与镜像制作正好落在 2300s 上下。
换句话说 **FirmAE 的 2300s 里，大部分是「把 3 个 init 各试一遍」，与固件本身多难
仿真无关**。G1V31si 的 9 个 qemu 阶段实测全部完成、`time_network=2299.2s`，
而 US TES7002 三个候选**全部 kernel panic**——两边花的时间一样多，得到的结果
却相反。所以本文不把 FirmAE 的 wall time 当作「它更努力」的证据。

两侧的**判定口径不同**（IRIS 看宿主转发端口的 HTTP 状态码，FirmAE 看 guest 内
ping/web 探针），且 IRIS 有 rootfs tarball 级缓存、FirmAE 没有。
本文只把耗时当作「是否在合理时间内出结果」的粗略参考，不作为性能结论。

## 8. 偏离与局限声明

**FirmAE 侧（本轮为跑通 check 模式做的改动，全部与仿真语义无关）**：

1. `BINARIES` 裁剪为 `busybox / console / libnvram.so / libnvram_ioctl.so`，
   移除 `gdb` / `gdbserver` / `strace`——GitHub release 在本网络下不可达。
   已核对这三者只被 `sources/debug.py`（`-d` 模式）引用，check 模式不涉及，
   不影响本表的 result/ping/web 判定。
2. `add_partition` 适配为宿主侧实现：WSL2 的 `losetup -Pf` 不发布分区节点，
   `/dev/loopNpM` 是目录而非块设备。改为优先走原生路径（5s 轮询、`[ -b ]` 判据），
   不成立则回退 `kpartx -av` 并解析 kpartx 自身输出取映射名。**未改动 QEMU 参数、
   内核、网络配置、仲裁与判定口径**；`kpartx` 本就在 FirmAE 官方 `install.sh` 依赖里，
   且 `del_partition` 已调用 `dmsetup remove`。隔离脚本验证返回
   `/dev/mapper/loop3p1`（块设备）。
3. 运行在 WSL2 而非原生 Linux；`--brand` 传 `auto`（脚本 `set -u` 下空参会
   `unbound variable`，FirmAE 官方 docker-helper 同样用 `auto`）。
4. 串行执行、每台跑前清理残留 dm/loop/TAP/mount，避免设备号串台。
5. **0.3.13 新增**：装了 `openjdk-11-jdk-headless`——binwalk 的 Java extractor 需要
   `jar`，而 `default-jre-headless`（环境里本来就有）**是 JRE，不含 `jar`**
   （实测 binwalk 报 `failed to run external extractor 'jar xvf ...': [Errno 2]`）。
   这一项只是补齐环境，未改动 FirmAE 代码。**它没有解决 RP3V30 的问题**
   （见 §3），留着是因为它是环境事实的一部分。
6. **0.3.13 新增且已撤销**：为查 RP3V30 的提取问题，把 `run.sh` 里两处
   `timeout --preserve-status --signal SIGINT 300` 改成 `900`。**测完已改回 300**
   （`grep -n 'signal SIGINT' run.sh` 确认两处均为 300，且文件里没有任何本轮标记）。
   中途还踩了一个坑值得记下来：第一次改法是在 `300` 后面追加
   `# BACKUP-BY-IRIS-ROUND4`，而该行以 `\` 续行——bash 里注释吞掉后面的续行，
   下一行 `./sources/extractor/extractor.py ...` 变成独立命令，`timeout` 因缺少
   被执行文件而立刻返回 **125**。`bash -n` 对这种写法**报语法通过**，所以必须
   打印改后原文来确认，不能只看语法检查。

**IRIS 侧**：

1. DIR-868L 的输入是本地从 `.zip` 解出的 `DIR868L_B1_FW205WWb02.bin`——
   IRIS 明确拒绝 zip 容器（`refusing zip container ... unpack the upgrade package
   locally`）。
2. 本文含 §6 的两轮代码修复；M0 快照对应的是修复前的代码，两者不可混用。
3. 0.3.12 轮用 `--timeout 300s`（WRT1200AC 300s / R7800 240s）；
   **0.3.13 轮 6 台统一 `--timeout 300s`**（含 WRT1200AC / R7800 重跑，
   分别 317.4s / 310.8s）。两轮的 WRT1200AC/R7800 数字不同是**超时上界不同**
   所致，不是同一条件下复测出了不同结果——引用时必须带上批次。
4. `/24` 是对 guest LAN 前缀的**假设**，不是推导（§6.2）；非 /24 的 guest LAN
   本办法不覆盖。
5. **两侧 rootfs 未做逐文件比对**（§3 表格下）。tarball 字节数相差 0.3%–0.6%，
   归档元数据差异即可解释，但「内容相同」是推断不是实测。

**共同局限**：

- 两台 UBI 固件在 IRIS 侧失败，根因是重宿主内核自身的 BUG（§3.1b），不在 IRIS 代码内
  可修；且 FirmAE 在这两台上根本没进到仿真阶段，因此**这一条既不能算 IRIS 的劣势，
  也不能算 IRIS 的优势**。
- 语料从 6 台扩到 **10 台**，但分布仍偏：5 台是 OpenWrt 24.10 官方快照，
  4 台是 Tenda 厂商固件（G1V31si / i27V11br / RP3V30 / TES7002 US），
  1 台是 2016 年的 D-Link 厂商固件。厂商 buildroot 的覆盖比 0.3.12 那轮好，
  但仍集中在 Tenda 一家——**Anyka 那类闭源 SDK 固件（cfms/configd）只被间接碰到过**。
- **10 台里只有 8 台真的进了仿真**：i27V11br 在提取阶段就失败（§4.2），
  GPON_TES7002 是一份 717 KB 的残包（ELF census 只有 `unk`，不是完整固件），
  直接跳过。写成功率时分母只能是 8，写「语料 10 台」会凭空多算两台。
- 本轮 IRIS 侧的耗时全部是**单次**运行，且 6 台统一 `--timeout 300s`；
  4 台走满 timeout（300s+），因此这些耗时是**上界而非典型值**。
- 两侧都跑在同一套 4.1.17 内核上，**凡由内核代差造成的失败两侧共有**。
  事实上 §3.1b 那两台的失败正是这一类：两侧若都跑到这一步，会一起失败。
- 本文所有 IRIS 数字都是**单次**运行，没有重复取中位数；QEMU TCG 的耗时波动未量化。

## 9. 本轮新发现：**未修**的代码缺陷

本节记录 0.3.13 这轮扩语料时**新发现、已定位、但本轮没有修**的缺陷。
写在这里而不是修掉，是因为它要动 5 个消费方 + 重写一条把错误行为固化成期望值的测试，
属于独立一轮的工作量；混在数据采集轮里改会让本文的实测数字失去可追溯性。

### 9.1 `inspect` 与 `emulate` 的架构判定不一致（IRIS 侧真实缺陷）

**现象**：同一份 US 版 TES7002 固件（`raw` squashfs，offset `0x43100`，无 uImage），
`iris extract inspect` 报 `arch: mipsel`，`iris emulate run --arch auto` 却用
**arm64** 跑出了 HTTP 200。

**根因（精确到行）**：`src/iris/extract/firmware.py:272-293` 的 `_infer_arch()` 有四级
兜底，第四级是：

```python
    if info.squashfs:
        return "mipsel" if info.squashfs[0].endian == "le" else "mipseb"   # :290-291
```

**这一级丢了 277-285 行的前提**。277-285 行的用法是「已确认是 MIPS 时，用 squashfs
端序区分 mipsel/mipseb」，而 290-291 行把同一个表达式当成通用兜底。squashfs 的端序
只描述**容器元数据**的字节先/后，与 guest 架构无关——aarch64 小端 ABI + LE 宿主打出的
rootfs 同样是 `hsqs`（小端），于是被误判成 mipsel。

同一文件 157-171 行的字节流 `find_elf_archs()` 在这里救不了场：压缩流里扫 ELF 是噪声
扫描，实测零命中。

**为什么 `emulate` 是对的**：`emulate --arch auto` 走的是另一条路径。
`src/iris/extract/rootfs_extract.py:384` 只消费 `fw_info.rootfs_offset` 一类字段，
**从不读 `fw_info.arch`**；真实架构来自解压后的 `_census_elfs()`
（`rootfs_extract.py:178-201`、`422-426`）。本机实测（`iris.extract.rootfs_extract.
_census_elfs` 直接跑一遍该 tarball 解压后的目录树）得到 **640 个 ELF 条目：
aarch64 × 639、mipseb × 1**。两个口径的差别也查清了：`_census_elfs` 用
`os.walk` + `open()`，**会把符号链接计进去**（本 tarball 有 256 个符号链接，其中
196 个指向 ELF），而只数 tar 里的普通文件是 444（aarch64 × 443、mipseb × 1），
差额恰好是那 196 个符号链接。唯一的异类是 **`bin/dbg_tool`（mipseb，厂商 SDK 里
的交叉编译产物）**，1 比 639 的少数，`census_to_runnable` 取多数因此不受影响。
经 `src/iris/arch.py:166-197` 的 `census_to_runnable` 得到 `arm64`。
落库的 `emulation_run.arch` 也是 `arm64`（id 77），可交叉验证。

**影响面（真实消费方，不是理论风险）**：

| 消费方 | 后果 |
|--------|------|
| `src/iris/api/server.py:405,453,464` | `/pipeline` 路径用 `info.arch`，会对这份固件真实报 `arch-mismatch` 失败 |
| `src/iris/cli.py:317` | `extract add --no-verify` 会把错误 arch 写进 `firmware` 表 |
| `src/iris/cli.py:400-402` | 已打印 `arch check: mipsel vs aarch64 -> MISMATCH`，但**没有人把 MISMATCH 当失败** |
| `tests/test_firmware.py:215`（`test_lzma_payload_squashfs`） | 把错误行为写成了期望值：断言 `info.arch == "mipsel"` |

`docs/eval-log.md:48` 记的「arch 识别 6/6 正确」**不覆盖这个形态**——那 6 个语料都带
uImage header 或有真实 ELF 证据，从来没测过「裸 squashfs 猜架构」这条路径。
也没有任何测试覆盖 inspect / emulate 两端的一致性。

**修正方向（推荐，未实施）**：引入**带证据分级的单一判定函数**，inspect 展示分级证据
（解压后 census 最高优先，其次 uImage header，容器端序仅供参考），emulate 采用最高
置信度级。要点是 inspect 是**同步不解压**的只读探针，不能为了准确就升级成重操作——
所以正确形态是「inspect 明确标注该结论证据不足」而不是「inspect 也去解压」。
配套需要重写 `test_firmware.py:215` 并新增 inspect↔emulate 一致性测试。

### 9.2 同一形态下 FirmAE 的架构判定也会错（对照数据，且是更差的那种错）

同一份 US 版 TES7002，FirmAE 判定结果是 **`armel`**（`scratch/7/architecture`），
并据此起了 `qemu-system-arm -M virt -kernel .../zImage.armel`。后果不是「拒绝」，
而是**跑错**：3 个 init 候选全部

```
request_module: runaway loop modprobe binfmt-464c
Starting init: /bin/init exists but couldn't execute it (error -8)
Starting init: /bin/sh exists but couldn't execute it (error -8)
Kernel panic - not syncing: No working init found.
```

`-8` 是 `ENOEXEC`。32 位 ARM 内核没有 aarch64 的 `binfmt` handler，所以连
「执行一个二进制」都做不到。同一份固件在 IRIS 侧是 **HTTP 302 @96.8s**。

**必须把话说准，不能拿「IRIS 会显式拒绝」当优势**——IRIS 并不拒绝，它也是靠判。
差别只有一条：**IRIS 的判定链上有一个能判对的第二证据源**（解压后的 ELF census，
639/640 命中 aarch64），而 FirmAE 的 `getArch.py` 只有 tarball 扫描这一条路，
在这份固件上给出了 `armel`。IRIS 若 census 判错，同样会跑错架构
（§9.1 里 `inspect` 就判错了）。所以这一条的正确表述是
**「IRIS 在这份固件上判对了，FirmAE 判错了」**，不是「IRIS 的架构处理更严谨」。

顺带一条机制差异：FirmAE 的 `makeNetwork.py` 明明在 rootfs 里正确找到了
`web service: /bin/boa`（真有一台 Tenda 路由器的 web 服务），但因为架构错、
内核起不来，这个二进制永远没机会监听。**「固件自带 web 服务」与「仿真里能拿到
web 服务」之间隔着架构判定这一关**，这也是为什么 §3 的 G1V31si（两侧都判对
mipsel、都失败于固件无 web 服务）和 US TES7002（IRIS 判对 → 拿到 web 服务）
放在一起看才有意义。

### 9.3 提取失败不落库（IRIS 侧工程缺陷）

见 §4.2 末段：i27v11br 提取失败**不产生 `emulation_run` 行**，批次级统计会静默漏样本。

## 10. 结论

1. **提取能力：IRIS 更强，但差距只体现在两类形态上。**
   - UBI 包装的 squashfs（FirmAE 提取失败的 2 台）IRIS 全部提取成功，
     FirmAE 的失败机制已定位到 binwalk UBI 插件路径，且它环境里存在可行但未被
     采用的替代路径。
   - 厂商加密的 FIT（i27v11br）**两侧都不行**——这是语料属性，不是任何一方的短板，
     不计入能力比较。
   - RP3V30 上 FirmAE 的提取**未完成**（900s 内不收敛，栈固定在同一处，**未定位**），
     因此该台**无法判定**。本文不把它算成 FirmAE 的失败。

2. **架构覆盖：IRIS 白名单多一个 arm64，但 0.3.13 的实测把「FirmAE 不支持 arm64」
   这句话说窄了。** 实测是 FirmAE 把 arm64 固件**误判成 `armel`**、用 32 位内核跑出
   kernel panic（ENOEXEC），而 IRIS 在同一份固件上判对并拿到 HTTP 302。
   **差别在第二证据源**（IRIS 解压后 ELF census 639/640 命中 aarch64），
   不在谁更严谨——IRIS 的 `inspect` 在同一份固件上也判错了（mipsel，§9.1）。
   两者都不支持 x86_64 仿真。

3. **仿真成功率：分母不同，必须分开报。**

   | 口径 | IRIS | FirmAE |
   |------|------|--------|
   | 进入仿真的台数 | **8**（10 台语料减去 i27V11br 提取失败、GPON 残包跳过） | **5**（WRT1200AC / R7800 / RP3V30 / i27v11br 未进入） |
   | 其中 Web 可达 | **4**（Newifi D2、Archer C7 v2、DIR-868L、US TES7002） | **2**（Archer C7 v2、DIR-868L） |

   **逐台分类比总数有用**：同因失败 2 台（G1V31si 固件无 web 服务、i27V11br 加密，
   两侧都不行）；IRIS 胜 1 台（US TES7002）；FirmAE 胜 **0 台**；无法判定 1 台
   （RP3V30）。WRT1200AC / R7800 上 IRIS 的优势**只在提取层**——仿真侧两侧都失败
   （FirmAE 连提取都没过），所以**既不能算 IRIS 的优势，也不能算 IRIS 的劣势**。

   0.3.12 那轮的结论仍然成立：DIR-868L 上的落后已追平，
   「两边各有对方做不到的成功」不再成立。

4. **0.3.13 最重要的产出不是胜负，而是把两类失败彻底分开了。** 旧口径只有
   「HTTP 000」，现在能说清：
   - `link-no-service`（G1V31si / RP3V30）：二层通、地址已配好、RP3V30 上兜底
     telnetd:7002 甚至跑通了，**固件里就是没有 web 服务**。这是语料属性 +
     有意的能力边界（兜底只认 goahead / boa 两种厂商组合，IRIS 不自带 httpd 注入）。
   - `link-no-arp`（WRT1200AC / R7800）：**`uhttpd` 真的 bind 了 `:80`/`:443`**，
     失败在地址丢失后的二层。**根因不变**（重宿主内核 `validate_nla` BUG，
     `pc=c01abe18`、`nlattr.c:41`，两台完全相同），变的是失败位置的精确描述。
   这个区分不是措辞偏好：前者要换固件或接受能力边界，后者要修宿主内核。

5. **IRIS 侧已知缺口（按可修复性排序）**：
   - **`inspect` 与 `emulate` 的架构判定不一致**（§9.1）：`_infer_arch()`
     把 squashfs 容器端序当成通用兜底，裸 squashfs 的 arm64 固件被误报 mipsel。
     影响 `/pipeline` API、`extract add --no-verify`，且 `tests/test_firmware.py:215`
     把错误行为固化成期望值。**本轮只记录未修**——它要动 5 个消费方。
   - **提取失败不落 `emulation_run`**（§9.3）：批次统计会静默漏掉提取失败的样本。
   - `guest_has_ipv4()` 硬编码 `grep "inet addr"`，而 busybox ≥1.20 的 `ifconfig`
     输出是 `inet 192.168.1.1`（无 `addr`）。证据：`emulate-9017`（Alpine）与
     `emulate-1607`（OpenWrt）都误判成 `no interface has an IP`。
     属**已确认的代码缺陷**，本轮未修改；
   - 兜底在 `ensure_command_channel` 附近静默终止（OpenWrt 上稳定复现，根因未定位）；
     目前只影响日志可读性，两台受影响的固件整体是成功的；
   - `iris_net_fix.sh` 通体基于 shell 函数，在 DIR-868L 这类 ash 不支持函数定义的
     厂商 BusyBox 上原理上跑不起来（§3.1a-2）。**不打算为此重写无函数版本**
     （成本极高，且该固件不需要兜底即可成功），如实记录为能力边界。

6. **耗时数字不构成性能结论**（§7）。特别地：FirmAE 那 2300s 里绝大部分是
   「把 3 个 init 候选各试一遍」，与固件难度无关——G1V31si（3 轮都跑完、无 web）
   与 US TES7002（3 轮全 kernel panic）耗时几乎相同，结果却相反。