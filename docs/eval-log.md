# IRIS M0 评测日志

> 本文件记录 M0 基线语料每款固件的提取/识别/仿真结果。
> FirmAE baseline 已于 2026-10-03 在 WSL2 内源码构建后实测回填（见下文该节）。
> **两侧的逐台对比、失败机制、耗时口径与偏离声明见 `08-与FirmAE对比.md`**；
> 本文除该节外仍为 M0 阶段的人工快照，不随后续运行自动更新。
> 失败阶段固定 7 类：`extraction / arch / infra / boot / nvram / network / service`。
>
> 这 7 类不是本文档的约定，而是 `src/iris/failures.py` 的 `Stage` 枚举——判定发生在代码里，
> 不是写在这里。`tests/test_failures.py` 反向守卫两个方向：每个 `Stage` 至少被一个
> `FailureKind` 映射，每个 `FailureKind` 都有唯一 stage 和一句可执行建议；新增成员忘了配
> stage 会让测试直接变红，而不是等到某天直方图里出现一个空阶段。
>
> （原文此处列 8 类，含 `wizard` / `verify`。这两个阶段没有任何代码路径能够产出：
> 全仓 `grep -rn "Stage\." src/` 只匹配到上列 7 个。它们写在这里只会让人以为存在一类
> 从未被观测到的失败。现已删除。）

## 数字口径：从哪来

| 来源 | 覆盖范围 | 可复现性 |
|------|----------|----------|
| `iris db stats` | 走完编排器的每一次仿真尝试，含失败与从未启动容器的早退 | 从 `emulation_run` / `failure_profile` 读回 |
| 本文各表 | M0 阶段的逐固件人工快照，不含此后新增的实物固件 | 手工维护的历史快照 |

**0.3.7 之前这两个来源没有关系。** `emulation_run` 与 `failure_profile` 虽有表定义，
但全仓 `grep -r EmulationRun src/` 只匹配到模型本身——没有任何写入路径。上面每张表里的
百分比都是人工填写、无法由代码验证的，这正是同一份文档能一边写「4/5 启动成功」、
一边列出五行全部 ✅ 的原因。

自 0.3.7 起每次仿真自动落库，`iris db stats` 输出即当前真实数字；本文表格保留为
M0 当时的记录，不再随后续运行自动更新。

**arch 命名存在已知口径差异（待 P1-a 收口）**：语料登记名（`image.arch`，如 `aarch64`）
与运行时名（`emulation_run.arch`，如 `arm64`）目前不是同一套词表，`iris db stats` 的
「by arch」按运行时名分组。因此该表的分组键暂时不能直接与语料表的 `arch` 列对齐比较。

## 语料入库总览

| # | 固件 | 品牌 | arch 识别 | 格式 | image id | MD5 | 大小 | rootfs 提取 | rootfs 偏移 |
|---|------|------|-----------|------|----------|-----|------|-------------|-------------|
| 1 | DIR-868L fw revB 2.05b02 | D-Link | armel | zip | 1 | 37c8589b90d04c551170e6aaf175f231 | 13,581,763 (13.0M) | 是 (squashfs in zip) | 1,835,160 |
| 2 | OpenWrt 24.10.0 Archer C7 v2 | TP-Link | mipseb | tplink | 2 | 8cc4338551646c861eccf4cd824b220e | 16,252,928 (15.5M) | 是 (squashfs) | 2,614,256 |
| 3 | OpenWrt 24.10.0 WRT1200AC | Linksys | armel | uImage | 3 | ee094210b9b37c076cf5f576fa4bf17b | 11,010,048 (10.5M) | 是 (UBI→squashfs) | 6,291,456 (UBI) |
| 4 | OpenWrt 24.10.0 R7800 | Netgear | armel | netgear | 4 | 08cb2a3c35f341bde94c50cb9fff9087 | 9,699,457 (9.3M) | 是 (UBI→squashfs) | 4,194,432 (UBI) |
| 5 | OpenWrt 24.10.0 x86/64 generic | OpenWrt | x64 | gzip→ext4 | 5 | 4915b25aae5a9857639592d447fe9f10 | 13,809,666 (13.2M) | 否 (ext4 非 squashfs) | — |
| 6 | OpenWrt 24.10.0 Newifi D2 | D-Team | mipsel | uImage | 6 | a101c31cfe710bcbc4d2bb9eb489cb85 | 7,340,623 (7.0M) | 是 (squashfs) | 3,181,000 |

**说明**：M1 L1 固件识别模块（`src/iris/extract/firmware.py`）通过 uImage header 解析、squashfs magic 检测（bytes_used 校验）、UBI magic 检测、gzip/zip 递归解压、ELF census 多信号综合推断架构。arch 识别 6/6 正确。

> **限定的适用范围（2026-10-04 补）**：「6/6 正确」只覆盖 M0 这六款——它们要么带
> uImage header（1/2/3/4/6），要么有真实 ELF 证据（5），**从未测过「裸 squashfs
> 没有任何架构线索时靠容器端序猜架构」这条路径**。0.3.13 用 US 版 TES7002
> （raw squashfs、无 uImage）测到了：那条路径会把 arm64 误判成 mipsel。
> 详见 [`08-与FirmAE对比.md`](08-与FirmAE对比.md) §9.1（已定位，本轮未修）。

## rootfs 提取与 arch 验证

| # | 固件 | rootfs 格式 | 提取状态 | ELF 数 | ELF arch | arch 验证 |
|---|------|-------------|----------|--------|----------|-----------|
| 1 | DIR-868L revB | squashfs (in zip) | 成功 | 265 | armel:265 | armel ✓ |
| 2 | Archer C7 v2 | squashfs | 成功 | 287 | mipseb:287 | mipseb ✓ |
| 3 | WRT1200AC | UBI → squashfs | 成功 | 278 | armel:278 | armel ✓ |
| 4 | R7800 | UBI → squashfs | 成功 | 300 | armel:300 | armel ✓ |
| 5 | x86/64 generic | ext4 | 跳过 (非 squashfs) | — | — | x64 (arch_hint) |
| 6 | Newifi D2 | squashfs | 成功 | 297 | mipsel:297 | mipsel ✓ |

**说明**：5/6 固件 rootfs 提取 + ELF arch 验证成功。3 款直接 squashfs 提取（DIR-868L/Archer C7/Newifi D2），2 次 UBI volume 解析 + 内嵌 squashfs 提取（WRT1200AC/R7800）。1/6 为 ext4 格式（x86-64），非 squashfs 提取范围。UBI 提取流程：`firmware.py` 检测 UBI EC header（"UBI#"）→ `ubi.py` 解析 PEB/VID header → 按 vol_id+lnum 拼接 LEB → 切片 squashfs → Docker alpine + unsquashfs 解压 → 遍历 ELF 验证架构。

## IRIS M1 L2 仿真结果

> IRIS 自主仿真（QEMU + libnvram + TAP/bridge + socat），非 FirmAE baseline。
>
> **本表已按 2026-10-03 的 0.3.12 重跑更新**（iid 6711–6715，`--timeout 300/240`）。
> 与上一版相比：DIR-868L 由失败转为成功；两台 armel OpenWrt 的失败原因由
> 「kernel panic」更正为**内核 BUG 打死 netifd 并泄漏 rtnl 锁**（非 panic，
> 且两者 `pc` 与 `nlattr.c:41` 完全相同，是同一根因）。详见
> [`08-与FirmAE对比.md`](08-与FirmAE对比.md) §3.1。

| # | 固件 | arch | QEMU 启动 | 服务启动 | Web 可达 | guest IP | 耗时 | 失败原因 |
|---|------|------|-----------|----------|----------|----------|------|----------|
| 1 | DIR-868L revB | armel | ✅ | ✅ httpd:80 | ✅ HTTP 200 | 192.168.0.1 | 53.0s | —（0.3.12 修复宿主不在 guest 子网） |
| 2 | Archer C7 v2 | mipseb | ✅ | ✅ uhttpd:80 | ✅ HTTP 200 | 192.168.1.1 | 49.9s | — |
| 3 | WRT1200AC | armel | ✅ | ✅ uhttpd:80 | ❌ | 无（eth0 曾短暂为 192.168.1.1） | 312.8s | 重宿主内核 `nlattr.c:41` BUG 打死 netifd |
| 4 | R7800 | armel | ✅ | ✅ uhttpd:80 | ❌ | 无（eth0 曾短暂为 192.168.1.1） | 246.0s | 同上（同一 `pc=c01abe18`） |
| 5 | x86/64 | x64 | — | — | — | — | — | x86 不在仿真范围 |
| 6 | Newifi D2 | mipsel | ✅ | ✅ uhttpd:80 | ✅ HTTP 200 | 192.168.1.1 | 62.2s | — |

**说明**：
- 仿真成功率 **3/5**（x86/64 架构级不在范围内）；上一版为 2/5。
- mipsel/mipseb/armel 三个架构**各有至少一台 Web 可达**，armel 的可达那台是
  2016 年的 Tenda 厂商固件（非 OpenWrt）。
- WRT1200AC/R7800 的根因链（实测）：t≈1.4s eth0 拿到 192.168.1.1 →
  t≈102–122s `netifd (1056): undefined instruction: pc=c01abe18` +
  `kernel BUG at firmadyne_kernel-v4.1/lib/nlattr.c:41`（`PC is at validate_nla`，
  `LR is at nla_parse`）→ netifd 在持有 rtnl 锁时被内核 BUG 打死 →
  之后 `ifconfig eth0 192.168.1.1` 永久阻塞（实测卡满 5s `run_bounded` 上限）
  → guest 无地址，`uhttpd` 虽绑 `:80` 但不可达。
  **这是重宿主内核自身的缺陷，不在 IRIS 代码内可修。**
- 网络推断：orchestrator 从串口日志解析 `__inet_insert_ifa` 自动发现 guest IP，
  并在桥上补一个该子网内的宿主地址（0.3.12 新增），随后动态调整 socat 转发目标。
- 网络修复注入：`iris_net_fix` OpenWrt init 脚本（START=99）在固件未配置 IP 时分配 192.168.1.1。
  该脚本在 DIR-868L 上跑不起来（厂商 BusyBox 不支持 shell 函数定义），
  但该固件不需要它即可成功——如实的边界，不是缺陷。

**本表覆盖范围**：仅上表 6 款 M0 语料（`image` id 1-6）。此后入库的实物固件
（G1 V3.1si / i27 V1.1br / RP3 V3.0ac / TES7002，id 7/8/10/11）不在本表内，
其运行结果以 `iris db stats` 为准。已知的一例：TES7002（aarch64）2026-10-02 实测
72s 起来、Web 可达（HTTP 302），已落库为 `emulation_run` 第 1 行。

## 2026-10-04 扩样实测（0.3.13 批次，iid 7001-7006）

> 这一批把 4 台厂商固件（Tenda 系）纳入与 FirmAE 的同批对照，语料从 6 台扩到 10 台。
> 全部 `--timeout 300s` 串行跑（06:59-07:23 UTC），数据来自 `emulation_run`
> 真实落库（id 76-80），不是手工快照。
> 分层判定 `result_kind` 是 0.3.13 新增的列，**旧的单一「HTTP 000」口径无法区分
> 下面第 7/10 行（二层通但无服务）与第 3/4 行（服务就绪但二层不通）**。

| image id | 固件 | arch | 提取 | 服务启动（串口证据） | Web | guest IP | 耗时 | `result_kind` | 失败原因 |
|-----------|------|------|------|----------------------|-----|----------|------|---------------|---------|
| 7 | G1V31si | mipsel | ✅ | ❌ 无（telnetd 起了但 `:7002` 无应答，`pidof: not found`） | ❌ | 192.168.1.1（兜底 `ifconfig` 配出） | 308.8s | `link-no-service` | **固件内无 web 服务**，兜底报 `no web server fallback available` |
| 8 | i27V11br | unknown | ❌ | — | — | — | 1s 早退（rc=2） | *（不落库）* | FIT 内 38 个 `YZTenda` 加密段，无厂商密钥不可解 |
| 10 | RP3V30 | armel | ✅ tarball 10,898,954 B | ❌ 无（但兜底 `telnetd` bind `:7002` **成功**） | ❌ | 192.168.1.1（兜底 `ifconfig` 配出） | 309.1s | `link-no-service` | 同上；网络层与命令通道都正常 |
| 11 | TES7002 (US 版) | **arm64** | ✅ 29 MB | ✅ 兜底拉起 goahead（`web not listening on :80, launching goahead`） | ✅ **HTTP 302** | 192.168.1.1 | **96.8s** | *（成功）* | — |
| 3 | WRT1200AC | armel | ✅ | ✅ `uhttpd` bind `:80`/`:443`（t=131.3s） | ❌ | 无（BUG 后 eth0 地址丢失） | 317.4s | `link-no-arp` | 重宿主内核 `validate_nla` BUG（`pc=c01abe18`） |
| 4 | R7800 | armel | ✅ | ✅ `uhttpd` bind `:80`/`:443`（t=123.4s） | ❌ | 无（同上） | 310.8s | `link-no-arp` | 同一根因（`pc` 与行号完全相同） |

**说明**：

- **arm64 通道再次跑通**：US 版 TES7002 `HTTP 302 @96.8s`，`emulation_run.arch=arm64`
  （由解压后 ELF census 得到，`iris.extract.rootfs_extract._census_elfs` 实测
  **640 个 ELF 条目：aarch64 × 639、mipseb × 1**；640 里含 196 个「符号链接指向
  ELF」的条目，只数 tar 普通文件则是 444。唯一异类是 `bin/dbg_tool`（mipseb，
  厂商 SDK 的交叉编译产物），1:639 的少数，不影响取多数的判定）。
  同时 `recorded 4 repair(s) on run 77`：`dev-extended-nodes`、`generic-diag-crash-fix`、
  `tenda-web-server-forced-start`、`vendor-watchdog-monitor`——L3 规则记账在真实批次上生效。
- **WRT1200AC / R7800 的失败位置被更正**：**web 服务是起来了的**（`uhttpd` 三次
  `inet_bind`，兜底自己也判定 `vendor web server is already running, leaving :80 to it`），
  失败在地址丢失后的二层。上一版「`uhttpd` 虽绑 `:80` 但不可达」的措辞把重点放在
  不可达上，容易读成服务没起来；根因（内核 BUG）不变。
- **两条 `link-no-service` 是语料属性，不是网络层缺陷**：G1V31si / RP3V30 的地址都由
  兜底 `ifconfig` 配出并被内核接受，RP3V30 上兜底命令通道（telnetd:7002）甚至完全
  跑通。IRIS 不自带 httpd 注入（那会改变被测设备行为），因此在「固件无 web 服务」时
  无路可走——这是**有意的能力边界**。
- **i27v11br 提取失败不落 `emulation_run`**，因此本表 6 行对应库里 5 行
  （id 76-80，iid 7001/7002/7003/7005/7006）。批次级统计不能只查该表。

## FirmAE baseline 仿真结果

> 本表于 **2026-10-03** 首次回填：用户在 WSL2 `FirmAudit2-Ubuntu` 内源码构建
> FirmAE（master），串行执行 `run.sh -c <firmware>`，逐台读回 `scratch/<n>/` 下的
> `result` / `ping` / `web` / `architecture` / `ip` 与 `time_*` 产物。
> 逐台结论、失败机制与全部偏离声明见 **`08-与FirmAE对比.md`**。

**与 IRIS 侧同口径重跑的对照、耗时口径、以及两侧各自的偏离声明，都在
`08-与FirmAE对比.md`。本表只保留 FirmAE 单侧的事实，不做跨项目结论。**

**表头口径**：前两列（`arch 识别` / `rootfs 提取`）是 **IRIS 侧**取值，与本文上半部分
的语料表同源；`FirmAE result` 起是 **FirmAE 侧**本次实测取值。`network_type` 列在本表
填的是 FirmAE 自己推断出的网络形态，仅供对照，不代表 IRIS 的判定。

| # | 固件 | arch 识别<br>(IRIS) | rootfs 提取<br>(IRIS) | FirmAE result | network_type<br>(FirmAE) | 失败阶段 | 关键日志指纹 |
|---|------|-----------|-------------|---------------|--------------|----------|--------------|
| 1 | DIR-868L revB | armel ✓ | ✅ | true | br0 / eth0.1 | —（成功） | `makeNetwork.log`: `networkInfo: [('192.168.0.1','eth0',1,None,'br0')]`（正确带 VLAN tag=1）；`ip`=192.168.0.1，wall 479s |
| 2 | Archer C7 v2 | mipseb ✓ | ✅ | true | eth0 | —（成功） | `ping`=true，`web`=true，`ip`=192.168.1.1，wall 1229s |
| 3 | WRT1200AC | armel ✓ | ✅ | —（未能进入仿真） | — | extraction | `UBIFS Fatal: Super block error: Wrong node type.`（binwalk 插件 `ubireader_extract_files` 路径失败；UBI EC header @ `0x600000`），wall 12s |
| 4 | R7800 | armel ✓ | ✅ | —（未能进入仿真） | — | extraction | 同 #3，wall 11s |
| 5 | x86/64 | x64 | —（ext4 非 squashfs） | —（**架构级不支持，未实跑**） | — | — | `firmae.config` 的 `check_arch = ("armel" "mipseb" "mipsel")` 无 x64 |
| 6 | Newifi D2 | mipsel ✓ | ✅ | false | br0 / eth0 | network | `ping`=true 但无 web；串口 `inet_bind[PID:1300 uhttpd] port:80` @42.7s；`makeNetwork.log`: `Interfaces: []` / `ports: []` / `networkInfo: []` → 退到 default network 并假设 `192.168.0.1`（设备实际 LAN 为 192.168.1.1），wall 1555s |

**#3/#4 的失败性质**：`extractor.py` 内无任何 UBI 处理代码，完全依赖 binwalk 的
`^ubi erase count header:ubi:ubireader_extract_files` 插件。`ubireader_extract_images`
能取出 volume（`file` 判定为 Squashfs 4.0 xz, 3,608,158 bytes），但 FirmAE 没有走这条路，
也没有 fallback 到按 magic 切 squashfs。准确说法是「所依赖的插件路径对该固件形态失败」，
不是「不支持 UBI」。

## 未入库条目（pending，M0 不要求）

| 固件 | 品牌 | 状态 | 原因 |
|------|------|------|------|
| R6220 | Netgear | pending | vendor URL 需验证 |
| TL-WDR4300 | TP-Link | pending | vendor URL 需验证 |
| DIR-615 E4 | D-Link | pending | FTP 可能不可达 |
| camera-slot-1 | TBD | pending | 摄像头固件需手动获取 |

## 新固件分析（用户提供）

| 固件 | 品牌 | 格式 | 架构 | rootfs | 仿真 | 备注 |
|------|------|------|------|--------|------|------|
| TC3T14CV1.0.bin | Tenda | 自定义(ZIP) | ARM EABI5 | JFFS2 ×4 | 待研究 | Lua init, 多 JFFS2 合并 |
| US_i29V2.0mt.bin | Tenda | uImage+FIT | ARM64 | FIT 内嵌 | 不支持 | 需 ARM64 内核 |

**TC3T14C 分析**：
- 头 "TD0201AX520CE" + ZIP 归档（PK magic @ offset 3007）
- ZIP 内容：u-boot.bin.img, zImage-dtb.img (ARM), romfs/user/web/custom-x.squash.img (实为 JFFS2)
- JFFS2 rootfs 用 jefferson 提取成功（296 nodes）
- romfs: busybox only; user: Lua init + audio; web: web UI; custom: hostapd
- 仿真挑战：Lua init 系统 + 多 JFFS2 overlay + ARM kernel panic 风险

**US_i29 分析**：
- uImage header (LZMA comp) → FIT image (FDT magic 0xd00dfeed)
- ARM64 OpenWrt Linux-5.4.231, 3.3MB FIT
- 不支持：需 qemu-system-aarch64 + ARM64 预编译内核

## M0 验收清单进度

- [x] 6 款 confirmed 固件下载落盘，MD5/大小已记录
- [x] 每款在 IRIS db 中有 image 记录，arch 识别 6/6 正确（armel/mipseb/mipsel/x64）
- [x] rootfs 提取 5/6 成功（3 squashfs + 2 UBI），ELF arch 验证全部一致
- [x] UBI volume 解析 + 内嵌 squashfs 提取 2/2 成功（WRT1200AC/R7800）
- [x] ≥ 5 次完成 IRIS 仿真并记录 result（5/5 启动成功，2/5 Web 可达）
- [x] eval-log.md 失败模式 ≥ 3 类有真实日志指纹（VLAN/kernel panic/无 IP）
- [x] （加分）2 款固件仿真成功且 curl Web 可达（Newifi D2 + Archer C7）
**关于「5/5 启动成功」的更正**：原文写「4/5 启动成功」，与上表矛盾——上表 5 台可仿真设备
（DIR-868L / Archer C7 / WRT1200AC / R7800 / Newifi D2）的「QEMU 启动」列全部为 ✅，
x86/64 一行是「—」即不在仿真范围，计入分母会凭空多出一个从未尝试过的失败。
按上表口径为 5/5 启动成功、其中 2/5 Web 可达。

这两项在 0.3.7 之前无法由任何代码验证（见文首「数字口径」）；现每次仿真自动写入
`emulation_run`，`iris db stats` 的计数即为该口径。