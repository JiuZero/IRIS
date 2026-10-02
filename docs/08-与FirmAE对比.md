# IRIS 与 FirmAE 固件仿真对比实测

> 实测日期：2026-10-03。两侧数据均为**本轮实跑**，不使用历史手工快照。
> 本文所有数字要么来自命令输出，要么来自固件产物文件，均标注了取数位置。

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
| 版本 | 本仓库 0.3.10 → 0.3.11（`inject_boot_hooks.sh` 有本轮修复，见 §6） | `pr0v3rbs/FirmAE` master，源码构建 |
| 内核 | 内置 `vmlinux.{mipsel,mipseb}.4` / `zImage.armel` / `Image.arm64` | **同一套** `vmlinux.*.4`（Linux 4.1.17+，构建串 `firmae@ubuntu`） |
| 数据库 | PostgreSQL 15 容器 | PostgreSQL（5 表 schema 就绪） |
| 单台上限 | `--timeout 120s`（3 台）/ `150s`（2 台 UBI） | `timeout 2400`，内部 `2×TIMEOUT` |
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
| Newifi D2 (mt7621) | .bin (uImage) | mipsel | ✅ 5,126,594 B | ✅ | ✅ HTTP 200 @75s | ❌ result=false | **IRIS 胜** |
| Archer C7 v2 (ath79) | .bin (uImage) | mipseb | ✅ | ✅ | ✅ HTTP 200（总耗时 61.8s） | ✅ result=true, web=true, IP 192.168.1.1 | **平** |
| DIR-868L revB (2016) | .zip → .bin | armel | ✅ 14,762,072 B | ✅ | ❌ HTTP 000（138.9s） | ✅ result=true, web=true, IP 192.168.0.1 | **FirmAE 胜** |
| WRT1200AC (mvebu) | .img (uImage+UBI) | armel | ✅ 4,713,565 B | ❌ 提取失败 | ❌ HTTP 000 | — 未能进入仿真 | **IRIS 胜（仅提取）** |
| R7800 (ipq806x) | .img (uImage+UBI) | armel | ✅ 5,644,270 B | ❌ 提取失败 | ❌ HTTP 000 | — 未能进入仿真 | **IRIS 胜（仅提取）** |

IRIS 侧两条成功行已写入 `emulation_run`；本轮后 `iris db stats` 为
`23 recorded run(s) / web reachable 16/23 (69.6%)`，其中新增 6 条来自本轮。

### 3.1 IRIS 侧失败的分类（不允许混为一谈）

**（a）DIR-868L：兜底被厂商收尾脚本饿死——已定位**

证据链（`iris-home/scratch/emulate-6630/qemu.serial.log` + `debugfs` 读镜像）：

- 该固件**没有 `/etc/inittab`**。因此 `inject_boot_hooks.sh` 的 sysinit 通道
  （第一道）被跳过，只剩 rcS 尾部通道（第三道）。
- 镜像内 `/etc/init.d/rcS` 尾部确实有注入行：`/bin/sh /etc/init.d/iris_net_fix_bg`。
- rcS 的实际结构是
  `for i in /etc/init.d/S??*; do echo "[$i]"; $i; done` → `echo "[$0] done!"`
  → `/etc/init0.d/rcS` → **注入行**。
- 串口里 `[/etc/init.d/S10init.sh]` … `[/etc/init.d/S45gpiod.sh]` → `[/etc/init.d/rcS]`
  全部出现，说明 `S??*` 循环跑完、`$0` 那一行也打出来了，而**注入行前面只剩
  `/etc/init0.d/rcS`**。该脚本开头是 `service status request` 的 while 轮询，
  正是「厂商脚本阻塞」的位置。
- 结果：串口 `IRIS-NETFIX` **0 行**，兜底一次都没执行。

厂商侧的网络配置本身是成功的——串口有
`8021q: adding VLAN 0 to HW filter on device eth0`、
`br_add_if[PID: 478 (brctl)]: br:br0 dev:eth0.1`、
`br0: port 1(eth0.1) entered forwarding state`，即 eth0.1 已经桥进 br0；
但 guest 全程没有非 loopback 地址（IRIS 判定 `no-guest-ip`），`httpd` 虽绑了
`:80` 却不可达。

**这一条不是「IRIS 能力不足」，而是「兜底的独立触发点在无 inittab 固件上退化成
单通道，而那唯一通道被放在厂商链路末尾」**——正是本项目自己早已写下的约束
（兜底不得以厂商 rcS 完成为门控）在这条路径上被违反。属**待修缺口**，不是已修复项。

**（b）WRT1200AC / R7800：根因未完全定位——如实标注**

已确认的事实：

- rootfs 提取成功（走 IRIS 专有的 UBI→squashfs 路径，见 §4）。
- 兜底**启动了**：`IRIS-NETFIX: start: brief pause before taking over the network`
  （各 1 行），此后不再有任何 NETFIX 输出，直到 154.78s 结束。
- guest 内 `eth0` 出现过：`8021q: adding VLAN 0 to HW filter on device eth0`（1.786s）、
  `__inet_insert_ifa[PID: 118 (ip)]: device:eth0 ifa:0x0101a8c0`（1.813s，
  即 192.168.1.1）。
- `uhttpd` 起来了，IRIS 自己的探测看到它绑了 `:80` 和 `:443`。
- 但宿主侧 `:8080` HTTP 000。

**未确认**：154.78s 结束时 `eth0`/`br0` 上到底还有没有 IP（早期 `__inet_insert_ifa`
不等于 netifd 接管后的最终状态），以及宿主 socat 转发为何打不通。这两条都需要在
guest 内取 `ip addr` 才能定论，本轮没有做到，因此**不写成结论**。

一个跨固件的旁证：TES7002（`emulate-9017`）与 Newifi D2（`emulate-1607`）的兜底日志
都在 `final: eth0 up with IP` 之后停止，而 9017 最终走到了 `done`。也就是说
「兜底在 `ensure_command_channel` 附近静默终止」在 OpenWrt 上是稳定现象，在 Alpine
上不是。这一处同样**未定位**，但它是上述三条失败候选解释里最先该查的。

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

## 5. 反向结论：FirmAE 在两处胜出

### 5.1 DIR-868L：FirmAE 成功，IRIS 失败

FirmAE 的 `makeNetwork.log` 显示它**正确推断并处理了 VLAN**：

```
Interfaces: [('br0','192.168.0.1'),('br1','192.168.7.1'),('eth0','10.0.2.15')]
networkInfo: [('192.168.0.1','eth0',1,None,'br0')]
```

第三个字段 `1` 就是 VLAN tag——FirmAE 把 eth0.1 接进了 br0，并给出
`filter network info: [('192.168.0.1','eth0',None,None,'br0')]` 作为对外接口。
IRIS 在这台上的厂商脚本做了同样的桥接（串口可见 `br_add_if ... br0 dev:eth0.1`），
但既没给 br0 配上地址，兜底也没执行（§3.1a）。

### 5.2 Newifi D2：FirmAE 失败，IRIS 成功

FirmAE `result=false`，但 `ping` 成功、`uhttpd` 起来了（串口
`inet_bind[PID:1300 uhttpd] ... port:80` @42.7s）。`makeNetwork.log` 同时显示：

```
Interfaces: []
ports: []
networkInfo: []
```

即接口/端口/网络信息三项全空 → 退到「bring up default network」，直接假设
`192.168.0.1` 并把它配到 `br0`。而 Newifi D2 的实际 LAN 是 **192.168.1.1**
（IRIS 侧的 `emulate-1607` 串口里 guest 通过 `192.168.1.1` 拿到 HTTP 200 即为此）。
**FirmAE 的网络地址推断在这台上是错的**，ping 通的是它自己假设的那个地址，Web 起在
真实地址上，转发自然打不通。

> 注：本轮开始时 IRIS 在这台上也失败（HTTP 000，`IRIS-NETFIX` 0 行），根因见 §6。
> 修复后 IRIS HTTP 200 @75s，FirmAE 仍为 false。

## 6. 本轮修复：procd 的 sysinit 通道会顶掉厂商 rcS

这是本轮唯一一个代码改动，也是 Newifi D2 能产出对比数据的前提。

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

## 7. 耗时不可直接比较

| 固件 | IRIS 总耗时 | FirmAE wall | 为什么不可比 |
|------|-------------|-------------|-------------|
| Newifi D2 | 76.5s | 1555s | IRIS `--timeout 120`，到点即退；FirmAE 内部 `2×TIMEOUT`，且要跑完自己的完整判定流程 |
| Archer C7 v2 | 61.8s | 1229s | 同上 |
| DIR-868L | 138.9s（失败） | 479s（成功） | IRIS 侧走满 timeout 才判失败 |

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
2. 本轮含 §6 的代码修复；M0 快照对应的是修复前的代码，两者不可混用。
3. 3 台成功/失败判定用 `--timeout 120s`，2 台 UBI 固件用 `150s`。

**共同局限**：

- 两台 UBI 固件在 IRIS 侧失败，根因未完全定位（§3.1b），因此**不能据此判断
  「IRIS 仿真能力弱于 FirmAE」**——FirmAE 在这两台上根本没进到仿真阶段。
- 语料只有 6 台，且 5 台是 OpenWrt 24.10 官方快照、1 台是 2016 年的 Tenda 厂商固件。
  这个分布对 procd 的覆盖比对 buildroot 好，对厂商 buildroot 变体的覆盖不够。
- 两侧都跑在同一套 4.1.17 内核上，**凡由内核代差造成的失败两侧共有**，
  本文的差异不包含这一类。

## 9. 结论

1. **提取能力**：IRIS 明显更强。UBI 包装的 squashfs（FirmAE 提取失败的两台）
   IRIS 全部提取成功；FirmAE 的失败机制已定位到 binwalk UBI 插件路径，且它环境里
   存在可行但未被采用的替代路径。
2. **架构覆盖**：IRIS 多支持 arm64/aarch64，FirmAE 白名单只有 3 个架构。
   两者都不支持 x86_64 仿真。
3. **仿真成功率**：本轮 5 台同语料下 IRIS 2 成 3 败，FirmAE 3 台可仿真的里 2 成 1 败
   （Newifi D2 / DIR-868L 失败，Archer C7 v2 成功），另 2 台卡在提取阶段。
   **两边各有对方做不到的成功**，方向不同：FirmAE 赢在 DIR-868L 的 VLAN 处理，
   IRIS 赢在 Newifi D2 的网络地址与 OpenWrt 启动链兼容。
4. **IRIS 侧已知缺口（按可修复性排序）**：
   - 无 inittab 固件上兜底退化为单通道且被厂商收尾脚本饿死（§3.1a，已定位）；
   - `iris_net_fix.sh` 的 `has_ip()` 硬编码 `grep "inet addr"`，而 busybox ≥1.20
     的 `ifconfig` 输出是 `inet 192.168.1.1`（无 `addr`）。证据：
     `emulate-9017`（Alpine）与 `emulate-1607`（OpenWrt）都误判成
     `no interface has an IP`。属**已确认的代码缺陷**，但本轮未修改；
   - 兜底在 `ensure_command_channel` 附近静默终止（OpenWrt 上稳定复现，根因未定位）。
5. 耗时数字不构成性能结论（§7）。