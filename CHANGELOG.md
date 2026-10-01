# 更新日志

本文件记录 IRIS（IoT Rehosting & Interconnection Simulator，鸢尾）的版本变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

暂无。下一批改动将在此累积。

## [0.2.0] - 2026-10-01

本版本的主题是**从"能跑起来"走向"能自愈、可归因、可交付"**：把 TES7002 实战中暴露的
厂商 watchdog / diag 崩溃 / Web 不启动三类问题沉淀为规则库与 AI 值守能力，同时完成
文档归档与代码整洁性治理。

### 新增

- **L3 规则库扩容至 7 条**：新增 `generic-watchdog-guard`（通用厂商 watchdog 防护，
  覆盖 `monitor`/`watchdog`/`monitord`/`arp_monitor`/`ppp-monitor`/`keep_alive`/`wdt`
  共 7 类命名变体与符号链接）、`generic-diag-crash-fix`（`diag` 工具 SIGSEGV 死循环）、
  `tenda-web-server-forced-start`（Tenda rcS 阻塞导致 Web 拉不起来）、
  `vendor-watchdog-monitor`（Tenda TES7002 厂商 watchdog 周期性 sysrq 重启整机）。
- **规则引擎支持 `post_action_verify` 证据校验**：`check_files` / `forbidden_patterns` /
  `health_check` 三类断言在修复后回读 rootfs 校验，结果写入报告，取代原先"写了就算"的
  不可审计行为。同时 `load_rules` 对未知顶层键与未知 detect/action 键不再静默丢弃，
  而是记录为报告警告。
- **`iris emulate guardian-start`**（L2/L3 联动）：按 `--interval` 周期拉取仿真容器串口
  日志，交给 AI 值守模块分析。
- **AI 值守模块 `iris.monitor.ai_guardian`**：7 类串口日志正则模式（watchdog 重启 /
  sysrq / reboot 尝试 / diag 崩溃 / soft lockup / Web 启动与存活）+ 四态健康状态机
  （healthy / degraded / critical / expired）+ 四类自愈动作（WATCHDOG_RECOVERY /
  RESOURCE_CLEANUP / DIAGNOSTIC_DISABLEMENT / WEB_SERVER_DIAGNOSIS）。
- **`iris.emulate.auto` 仿真前置准备层**：`prepare_from_firmware()` /
  `prepare_from_rootfs()` 把「L1 提取 + L3 规则 + 架构推断」收敛为单次调用，
  `pick_host_port()` 提供 8080–8199 空闲端口自动分配。
- **`scripts/emulate/iris_net_fix.sh` Web 存活判定降级链**：优先 `pidof` 进程判定
  （goahead/boa/lighttpd/uhttpd/thttpd 等 9 种），其次查 80 端口，最后才尝试拉起
  实例。修复 busybox `netstat` 打印服务名而非端口导致误判"Web 已死"、
  进而重复拉起引发 `Cannot bind to address *:80, errno 98` 的问题。

### 修复

- **规则引擎不再改写二进制文件**：`edit` / `comment_lines` 动作新增 `_is_binary()`
  守卫（前 512 字节含 `\x00` 即跳过）。此前 `_read_text()` 以 `errors="ignore"`
  有损解码后回写，会静默损坏厂商 ELF；自动管道无人值守执行规则后该风险已实际发生。
- **aarch64 架构映射**（`docs/07-arch-mapping-fix.md`）：ELF 普查得到的标准架构名
  `aarch64` 映射到 QEMU 内核标签 `arm64`，修复 arm64 固件被误判为"未知架构"而无法
  自动选参的问题。
- **Windows 换行保真**：`_read_text` / `_write_text` 使用 `newline=""`，
  避免 Windows 上 universal-newline 把所有被修 shell 脚本重写成 CRLF。

### 变更

- **文档归档**：根目录 10 份散落的英文说明文档归并去重后迁入 `docx/`，并统一改为
  中文命名（详见下节"文档"）。
- **架构映射常量生效**：`_ARCH_MAPPINGS` 取代重复硬编码的映射字典。

### 文档

- 新增 `CHANGELOG.md`，建立版本变更记录机制。
- 归并去重（消除约 70% 重复内容与互相矛盾的实测数据）：
  - `AI_GUARDIAN_SUMMARY.md` + `AI_GUARDIAN_DEPLOYMENT.md` + `TES7002_WEB_NOT_STARTING_ANALYSIS.md`
    + `FINAL_SOLUTION_SUMMARY.md` + `FINAL_STATUS.md` → `docx/AI值守与稳定性治理.md`
  - `HOW_TO_USE.md` + `README_USAGE.md` + `QUICK_START.md` + `INSTALLATION_INSTRUCTIONS.md`
    + `INSTALLATION_COMPLETE.md` → `docx/免安装使用指南.md`
- `docs/05-crash-diagnosis.md` → `docs/05-崩溃归因.md`
- `docs/06-stability-test.md` → `docs/06-稳定性验证.md`
- `docs/07-arch-mapping-fix.md` → `docs/07-架构映射修复.md`

### 移除

- 死代码与未引用符号：`qemu_config.build_qemu_args()`（与真实 `run_qemu.sh` 的
  TAP+bridge 网络模型已漂移，构成误导）、`arch.identify_file()`、
  `config.reset_settings()`、`EmulationResult.ping_ok` / `EmulationResult.qemu_pid`
  （定义后从不赋值）、`SerialLogAnalyzer.get_latest_crash_context()`（无调用方）。
- 临时调试文件：根目录 `test_guardian.py`、`test_manual_usage.py`（其覆盖内容已迁入
  `tests/`）、`iris-home/scratch;C`（Windows 路径误转义产物）。
- `auto.py` 中永不生效的分支（检查引擎从不产生的 `"SKIPPED(binary)"` 标记）。

### 修复（字段命名）

- `EmulationResult.firmware_path` → `rootfs_dir`：该字段唯一赋值处传入的是 rootfs 目录，
  原名与实际语义不符。

## [0.1.0] - 2026-09-28

M0 骨架到 M1 五层贯通的累积版本，对应 24 个提交与 5 个里程碑标签
（`m0-skeleton` / `m1-l1-extract` / `m1-l2-emulate` / `m1-l5-api` / `m1-pipeline`）。

### 新增

- **L1 提取识别**：10 种固件格式识别（squashfs / UBI / uImage / TendaW / FIT / trx /
  TPLink / Netgear / gzip / raw），rootfs 前缀启发式判定，ELF 头架构普查，
  结构化失败画像（`encrypted-fit` / `fit-unsupported` / `tendaw-nojffs2` /
  `ubi-no-squashfs` / `no-rootfs`）。
- **L2 执行**：QEMU 四架构通道（armel / arm64 / mipseb / mipsel）、Docker 容器编排、
  TAP + bridge 网络、串口日志驱动的 guest IP 推断与 Web 可达性判定；
  启动前 ELF 架构预检（不匹配时秒级失败并给出 `--arch` 修正建议）。
- **L3 环境恢复**：最小可用的 YAML 规则引擎（4 类 detect × 4 类 action）与首批实证规则。
- **L5 编排**：Typer CLI（6 个命令组 / 15 个命令）+ FastAPI 服务
  （health / firmware / emulate / pipeline 四组端点）。
- **数据层**：SQLAlchemy 2.x，firmadyne 兼容五表 + IRIS 自有
  `emulation_run` / `failure_profile` / `repair_action` 三表。
- **语料管理**：TOML 语料清单、GitHub 镜像改写、分块临时文件原子落盘 + sha256 校验。

### 修复

- 上传文件名防路径穿越，CLI 与 API 的 `iid` 稳定化。
- 容器内组装 rootfs 方案，规避 Windows NTFS 宿主 `cp` 静默丢弃符号链接；
  提取失败接入结构化画像（加密 FIT 不再误报 "no squashfs"）。
- 规则引擎修复 Windows 换行破坏、目录作用域越界与 `write` 路径穿越。