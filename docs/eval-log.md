# IRIS M0 评测日志

> 本文件记录 M0 基线语料每款固件的提取/识别/仿真结果。
> FirmAE baseline 仿真由用户在 WSL2 内手动执行（见 `03-M0执行手册.md`），结果回填本表。
> 失败阶段固定 8 类：`extraction / arch / boot / nvram / network / service / wizard / verify`。

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

| # | 固件 | arch | QEMU 启动 | 服务启动 | Web 可达 | guest IP | 耗时 | 失败原因 |
|---|------|------|-----------|----------|----------|----------|------|----------|
| 1 | DIR-868L revB | armel | ✅ | ✅ httpd:80 | ❌ | 192.168.0.1 | 148s | VLAN (eth0.1→br0) 路由不通 |
| 2 | Archer C7 v2 | mipseb | ✅ | ✅ uhttpd:80 | ✅ HTTP 200 | 192.168.1.1 | 73s | — |
| 3 | WRT1200AC | armel | ✅ | ✅ uhttpd:80 | ❌ | 192.168.1.1 | 144s | kernel panic (nlattr.c:41) |
| 4 | R7800 | armel | ✅ | ✅ uhttpd:80 | ❌ | 192.168.1.1 | 161s | kernel panic (nlattr.c:41) |
| 5 | x86/64 | x64 | — | — | — | — | — | x86 不在仿真范围 |
| 6 | Newifi D2 | mipsel | ✅ | ✅ uhttpd:80 | ✅ HTTP 200 | 192.168.1.1 | 47s | — |

**说明**：
- MIPS 固件（mipsel/mipseb）2/2 仿真成功，Web 管理面从主机可达（LuCI 界面）。
- ARM 固件 3/3 启动成功，服务启动成功，但 Web 不可达：
  - DIR-868L：VLAN 架构（eth0.1→br0=192.168.0.1），TAP+VLAN1 桥接仍不通，需进一步调试 VLAN tag 转发。
  - WRT1200AC/R7800：FirmAE 预编译 ARM kernel v4.1 有 bug（`lib/nlattr.c:41` Oops），启动后 ~127s panic 导致网络栈崩溃。
- 网络推断：orchestrator 从串口日志解析 `__inet_insert_ifa` 自动发现 guest IP，动态调整 socat 转发目标。
- 网络修复注入：`iris_net_fix` OpenWrt init 脚本（START=99）在固件未配置 IP 时分配 192.168.1.1。

## FirmAE baseline 仿真结果

> 以下由用户在 WSL2 内执行 FirmAE `run.sh -c <firmware>` 后回填。
> 每款固件最长约 12 分钟（2×TIMEOUT）。

| # | 固件 | arch 识别 | rootfs 提取 | FirmAE result | network_type | 失败阶段 | 关键日志指纹 |
|---|------|-----------|-------------|---------------|--------------|----------|--------------|
| 1 | DIR-868L revB | armel ✓ | ✅ | _待填_ | _待填_ | _待填_ | _待填_ |
| 2 | Archer C7 v2 | mipseb ✓ | ✅ | _待填_ | _待填_ | _待填_ | _待填_ |
| 3 | WRT1200AC | armel ✓ | ✅ | _待填_ | _待填_ | _待填_ | _待填_ |
| 4 | R7800 | armel ✓ | ✅ | _待填_ | _待填_ | _待填_ | _待填_ |
| 5 | x86/64 | x64 | — | _待填_ | _待填_ | _待填_ | _待填_ |
| 6 | Newifi D2 | mipsel ✓ | ✅ | _待填_ | _待填_ | _待填_ | _待填_ |

## 未入库条目（pending，M0 不要求）

| 固件 | 品牌 | 状态 | 原因 |
|------|------|------|------|
| R6220 | Netgear | pending | vendor URL 需验证 |
| TL-WDR4300 | TP-Link | pending | vendor URL 需验证 |
| DIR-615 E4 | D-Link | pending | FTP 可能不可达 |
| camera-slot-1 | TBD | pending | 摄像头固件需手动获取 |

## M0 验收清单进度

- [x] 6 款 confirmed 固件下载落盘，MD5/大小已记录
- [x] 每款在 IRIS db 中有 image 记录，arch 识别 6/6 正确（armel/mipseb/mipsel/x64）
- [x] rootfs 提取 5/6 成功（3 squashfs + 2 UBI），ELF arch 验证全部一致
- [x] UBI volume 解析 + 内嵌 squashfs 提取 2/2 成功（WRT1200AC/R7800）
- [x] ≥ 5 次完成 IRIS 仿真并记录 result（4/5 启动成功，2/5 Web 可达）
- [x] eval-log.md 失败模式 ≥ 3 类有真实日志指纹（VLAN/kernel panic/无 IP）
- [x] （加分）2 款固件仿真成功且 curl Web 可达（Newifi D2 + Archer C7）