# IRIS 与 FirmAE 固件仿真对比实测

> 实测日期：2026-10-03（FirmAE 侧）／2026-10-03 第二轮（IRIS 侧，含 0.3.12 修复后重跑）。
> 两侧 FirmAE 数据均为**实跑**，不使用历史手工快照。
> 本文所有数字要么来自命令输出，要么来自固件产物文件，均标注了取数位置。
>
> **本文在 0.3.12 这一轮被修订过两次结论**：DIR-868L 由失败转为成功，
> WRT1200AC/R7800 的根因由「未完全定位」改为「重宿主内核的 BUG」。
> 上一版的两处结论都被推翻，§3.1 写明了推翻过程与证据。

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
| 版本 | 本仓库 0.3.11 → 0.3.12（`inject_boot_hooks.sh` 与 orchestrator 有两轮修复，见 §6） | `pr0v3rbs/FirmAE` master，源码构建 |
| 内核 | 内置 `vmlinux.{mipsel,mipseb}.4` / `zImage.armel` / `Image.arm64` | **同一套** `vmlinux.*.4`（Linux 4.1.17+，构建串 `firmae@ubuntu`） |
| 数据库 | PostgreSQL 15 容器 | PostgreSQL（5 表 schema 就绪） |
| 单台上限 | `--timeout 300s`（WRT1200AC）/ `240s`（R7800） | `timeout 2400`，内部 `2×TIMEOUT` |
| 采集口径 | `iris.py emulate run` 退出码 + `emulation_run` + `scratch/emulate-*/qemu.serial.log` | `scratch/<n>/` 下的 `result` / `ping` / `web` / `architecture` / `ip` / `time_*` |

**两侧共用同一套 FirmAE 内核**——这是解读差异时最重要的一条：任何由「内核太老、
模块加载不了」造成的失败，两侧会同时失败，不能算作某一方的优势。

## 2. 架构支持矩阵

IRIS 的可仿真架构来自 `src/iris/emulate/qemu_config.py`（CLI 直接报出
`supported: armel, arm64, mipseb, mipsel`）；FirmAE 来自 `firmae.config` 的
`check_arch = ("armel" "mipseb" "mipsel")` 白名单。

| 架构 | FirmAE | IRIS | 说明 |
|------|---------|------|------|
| armel | ✅ | ✅ | |
| mipsel | ✅ | ✅ | |
| mipseb | ✅ | ✅ | |
| arm64 / aarch64 | ❌ 架构级不支持 | ✅ | IRIS 自带 `Image.arm64` + `initramfs.arm64` 通用内核通道 |
| x86_64 | ❌ 架构级不支持 | ❌ 仿真不支持 | IRIS 提取层能识别 `x64`（`m0-baseline.toml` 有记录），但无 x86_64 QEMU 配置 |

「架构级不支持」与「跑了但失败」在本文严格区分：不支持的项目**没有实跑**，表中不出现
它们的成功/失败数字，只在此处列出白名单事实。

TES7002（arm64）在 IRIS 侧有 17 条历史 `emulation_run`（14 条 web 可达），可用于说明
IRIS 独有的 arm64 通道确实跑通过；但它不在本轮 FirmAE 语料内（FirmAE 无法仿真），
因此不进入 §3 的逐台对比表。

## 3. 逐台对比

| 固件 | 格式 | arch | IRIS 提取 | FirmAE 提取 | IRIS 仿真 | FirmAE 仿真 | 对比结论 |
|------|------|------|-----------|-------------|-----------|-------------|---------|
| Newifi D2 (mt7621) | .bin (uImage) | mipsel | ✅ 5,126,594 B | ✅ | ✅ HTTP 200 @62.2s | ❌ result=false | **IRIS 胜** |
| Archer C7 v2 (ath79) | .bin (uImage) | mipseb | ✅ | ✅ | ✅ HTTP 200 @49.9s | ✅ result=true, web=true, IP 192.168.1.1 | **平** |
| DIR-868L revB (2016) | .zip → .bin | armel | ✅ 14,762,072 B | ✅ | ✅ HTTP 200 @53.0s | ✅ result=true, web=true, IP 192.168.0.1 | **平** |
| WRT1200AC (mvebu) | .img (uImage+UBI) | armel | ✅ 4,713,565 B | ❌ 提取失败 | ❌ HTTP 000（312.8s） | — 未能进入仿真 | **IRIS 胜** |
| R7800 (ipq806x) | .img (uImage+UBI) | armel | ✅ 5,644,270 B | ❌ 提取失败 | ❌ HTTP 000（246.0s） | — 未能进入仿真 | **IRIS 胜** |

IRIS 侧三条成功行已写入 `emulation_run`。DIR-868L 与 WRT1200AC/R7800 的成功/失败
都是 0.3.12 这一轮重测的数字，根因与证据见 §3.1 与 §6.2；DIR-868L 在此之前于同一份
语料上是失败的。

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

一个跨固件的旧旁证仍**未定位**，保留记录：TES7002（`emulate-9017`）与 Newifi D2
（`emulate-1607`）的兜底日志都在 `final: eth0 up with IP` 之后停止，而 9017 最终走到了
`done`。这两台**整体是成功的**，所以这处静默终止目前只影响日志可读性，不影响判定。

## 4. 提取能力：UBI 包装的 Squashfs

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

**IRIS 侧**：`src/iris/extract/ubi.py` 解析 PEB/VID header，按 vol_id+lnum 拼接 LEB，
再切出内嵌 squashfs 交给 alpine+unsquashfs，本轮两台均提取成功（tarball
4,713,565 / 5,644,270 字节）。这是 IRIS 相对 FirmAE 的**明确、可复现的能力差异**。

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

**IRIS 侧**：

1. DIR-868L 的输入是本地从 `.zip` 解出的 `DIR868L_B1_FW205WWb02.bin`——
   IRIS 明确拒绝 zip 容器（`refusing zip container ... unpack the upgrade package
   locally`）。
2. 本文含 §6 的两轮代码修复；M0 快照对应的是修复前的代码，两者不可混用。
3. 本轮回归用 `--timeout 300s`（WRT1200AC / R7800 用 300s / 240s）。
4. `/24` 是对 guest LAN 前缀的**假设**，不是推导（§6.2）；非 /24 的 guest LAN
   本办法不覆盖。

**共同局限**：

- 两台 UBI 固件在 IRIS 侧失败，根因是重宿主内核自身的 BUG（§3.1b），不在 IRIS 代码内
  可修；且 FirmAE 在这两台上根本没进到仿真阶段，因此**这一条既不能算 IRIS 的劣势，
  也不能算 IRIS 的优势**。
- 语料只有 6 台，且 5 台是 OpenWrt 24.10 官方快照、1 台是 2016 年的 Tenda 厂商固件。
  这个分布对 procd 的覆盖比对 buildroot 好，对厂商 buildroot 变体的覆盖不够。
- 两侧都跑在同一套 4.1.17 内核上，**凡由内核代差造成的失败两侧共有**。
  事实上 §3.1b 那两台的失败正是这一类：两侧若都跑到这一步，会一起失败。
- 本文所有 IRIS 数字都是**单次**运行，没有重复取中位数；QEMU TCG 的耗时波动未量化。

## 9. 结论

1. **提取能力**：IRIS 明显更强。UBI 包装的 squashfs（FirmAE 提取失败的两台）
   IRIS 全部提取成功；FirmAE 的失败机制已定位到 binwalk UBI 插件路径，且它环境里
   存在可行但未被采用的替代路径。
2. **架构覆盖**：IRIS 多支持 arm64/aarch64，FirmAE 白名单只有 3 个架构。
   两者都不支持 x86_64 仿真。
3. **仿真成功率**：0.3.12 修复后，5 台同语料下 **IRIS 3 成 2 败**（Newifi D2、
   Archer C7 v2、DIR-868L 均 HTTP 200），FirmAE 在能进仿真的 3 台里 **2 成 1 败**
   （Newifi D2 失败）。上一版「两边各有对方做不到的成功」的说法**已不成立**——
   DIR-868L 上的落后已追平。剩下的 2 台 IRIS 失败源于重宿主内核（§3.1b），
   不构成对 FirmAE 的比较优势。
4. **IRIS 侧已知缺口（按可修复性排序）**：
   - `guest_has_ipv4()` 硬编码 `grep "inet addr"`，而 busybox ≥1.20 的 `ifconfig`
     输出是 `inet 192.168.1.1`（无 `addr`）。证据：`emulate-9017`（Alpine）与
     `emulate-1607`（OpenWrt）都误判成 `no interface has an IP`。
     属**已确认的代码缺陷**，本轮未修改；
   - 兜底在 `ensure_command_channel` 附近静默终止（OpenWrt 上稳定复现，根因未定位）；
     目前只影响日志可读性，两台受影响的固件整体是成功的；
   - `iris_net_fix.sh` 通体基于 shell 函数，在 DIR-868L 这类 ash 不支持函数定义的
     厂商 BusyBox 上原理上跑不起来（§3.1a-2）。**不打算为此重写无函数版本**
     （成本极高，且该固件不需要兜底即可成功），如实记录为能力边界。
5. 耗时数字不构成性能结论（§7）。