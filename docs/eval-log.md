# IRIS M0 评测日志

> 本文件记录 M0 基线语料每款固件的提取/识别/仿真结果。
> FirmAE baseline 仿真由用户在 WSL2 内手动执行（见 `03-M0执行手册.md`），结果回填本表。
> 失败阶段固定 8 类：`extraction / arch / boot / nvram / network / service / wizard / verify`。

## 语料入库总览

| # | 固件 | 品牌 | 预期 arch | image id | MD5 | 大小 | rootfs 提取 | 入库时间 |
|---|------|------|-----------|----------|-----|------|-------------|----------|
| 1 | DIR-868L fw revB 2.05b02 | D-Link | armel | 1 | 37c8589b90d04c551170e6aaf175f231 | 13,581,763 (13.0M) | 否 (M1 binwalk) | 2026-09-26 |
| 2 | OpenWrt 24.10.0 Archer C7 v2 | TP-Link | mipseb | 2 | 8cc4338551646c861eccf4cd824b220e | 16,252,928 (15.5M) | 否 (M1 binwalk) | 2026-09-26 |
| 3 | OpenWrt 24.10.0 WRT1200AC | Linksys | armel | 3 | ee094210b9b37c076cf5f576fa4bf17b | 11,010,048 (10.5M) | 否 (M1 binwalk) | 2026-09-26 |
| 4 | OpenWrt 24.10.0 R7800 | Netgear | armel | 4 | 08cb2a3c35f341bde94c50cb9fff9087 | 9,699,457 (9.3M) | 否 (M1 binwalk) | 2026-09-26 |
| 5 | OpenWrt 24.10.0 x86/64 generic | OpenWrt | x64 | 5 | 4915b25aae5a9857639592d447fe9f10 | 13,809,666 (13.2M) | 否 (M1 binwalk) | 2026-09-26 |
| 6 | OpenWrt 24.10.0 Newifi D2 | D-Team | mipsel | 6 | a101c31cfe710bcbc4d2bb9eb489cb85 | 7,340,623 (7.0M) | 否 (M1 binwalk) | 2026-09-26 |

**说明**：6 款固件均为原始厂商/OpenWrt 镜像（非 tar rootfs），arch 识别与 rootfs 提取推迟到 M1（binwalk/unblob 集成）。M0 阶段验证的是元数据入库闭环（brand → image → md5 → target_type）。

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
- [x] 每款在 IRIS db 中有 image 记录（arch 识别推迟到 M1 binwalk）
- [ ] ≥ 5 款完成 FirmAE `-c` 仿真并记录 result
- [ ] eval-log.md 失败模式 ≥ 3 类有真实日志指纹
- [ ] （加分）1 款固件仿真成功且 curl Web 可达