# IRIS M0 评测日志

> 本文件记录 M0 基线语料每款固件的提取/识别/仿真结果。
> FirmAE baseline 仿真由用户在 WSL2 内手动执行（见 `03-M0执行手册.md`），结果回填本表。
> 失败阶段固定 8 类：`extraction / arch / boot / nvram / network / service / wizard / verify`。

## 语料入库总览

| # | 固件 | 品牌 | arch 识别 | 格式 | image id | MD5 | 大小 | rootfs 提取 | rootfs 偏移 |
|---|------|------|-----------|------|----------|-----|------|-------------|-------------|
| 1 | DIR-868L fw revB 2.05b02 | D-Link | armel | zip | 1 | 37c8589b90d04c551170e6aaf175f231 | 13,581,763 (13.0M) | 是 (squashfs in zip) | 1,835,160 |
| 2 | OpenWrt 24.10.0 Archer C7 v2 | TP-Link | mipseb | tplink | 2 | 8cc4338551646c861eccf4cd824b220e | 16,252,928 (15.5M) | 是 (squashfs) | 2,614,256 |
| 3 | OpenWrt 24.10.0 WRT1200AC | Linksys | armel | uImage | 3 | ee094210b9b37c076cf5f576fa4bf17b | 11,010,048 (10.5M) | 是 (squashfs) | 6,557,696 |
| 4 | OpenWrt 24.10.0 R7800 | Netgear | armel | netgear | 4 | 08cb2a3c35f341bde94c50cb9fff9087 | 9,699,457 (9.3M) | 是 (squashfs) | 4,460,672 |
| 5 | OpenWrt 24.10.0 x86/64 generic | OpenWrt | x64 | gzip→ext4 | 5 | 4915b25aae5a9857639592d447fe9f10 | 13,809,666 (13.2M) | 否 (ext4 非 squashfs) | — |
| 6 | OpenWrt 24.10.0 Newifi D2 | D-Team | mipsel | uImage | 6 | a101c31cfe710bcbc4d2bb9eb489cb85 | 7,340,623 (7.0M) | 是 (squashfs) | 3,181,000 |

**说明**：M1 L1 固件识别模块（`src/iris/extract/firmware.py`）通过 uImage header 解析、squashfs magic 检测、gzip/zip 递归解压、ELF census 多信号综合推断架构。5/6 固件检测到 squashfs rootfs（x86-64 为 ext4 格式）。arch 识别 6/6 正确（与 manifest arch_hint 一致）。

## FirmAE baseline 仿真结果

> 以下由用户在 WSL2 内执行 FirmAE `run.sh -c <firmware>` 后回填。
> 每款固件最长约 12 分钟（2×TIMEOUT）。

| # | 固件 | arch 识别 | rootfs 提取 | FirmAE result | network_type | 失败阶段 | 关键日志指纹 |
|---|------|-----------|-------------|---------------|--------------|----------|--------------|
| 1 | DIR-868L revB | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ |
| 2 | Archer C7 v2 | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ |
| 3 | WRT1200AC | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ |
| 4 | R7800 | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ |
| 5 | x86/64 | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ |
| 6 | Newifi D2 | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ |

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
- [x] rootfs 偏移检测 5/6 成功（x86-64 为 ext4 格式）
- [ ] ≥ 5 次完成 FirmAE `-c` 仿真并记录 result
- [ ] eval-log.md 失败模式 ≥ 3 类有真实日志指纹
- [ ] （加分）1 款固件仿真成功且 curl Web 可达