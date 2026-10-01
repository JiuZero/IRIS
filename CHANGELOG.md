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

- **L3 规则库扩容至 6 条**：新增 `generic-diag-crash-fix`（`diag` 工具 SIGSEGV 死循环）、
  `tenda-web-server-forced-start`（为 `iris_net_fix.sh` 准备 Web 启动前置条件），
  `vendor-watchdog-monitor` 由 TES7002 专用扩展为通用厂商 watchdog 防护（覆盖
  `monitor`/`watchdog`/`monitord`/`arp_monitor`/`ppp-monitor`/`keep_alive`/`keepalive`/
  `wdt`/`reboot_guard` 共 9 类命名变体与符号链接）。
- **规则引擎 detect 条件补齐**：新增 `path_exists`（含 `executable` 修饰符）与
  `all`/`any` 分组，使一条规则可以表达"多个事实同时成立"；原有"顶层条件 OR、无法表达
  AND"的语义缺口导致 `tenda-web-server-forced-start` 只能用过宽的 `rcS` 存在性做指纹，
  会对语料内几乎所有固件误命中。
- **规则引擎支持 `post_action_verify` 证据校验**：`check_files` / `forbidden_patterns` /
  `health_check` 三类断言在修复后回读 rootfs 校验，结果写入报告，取代原先"写了就算"的
  不可审计行为；`health_check` 属 guest 侧检查，会渲染为 shell 段落写入
  `/firmadyne/iris_rules.sh`，在 chroot 内执行并把裁决落到 `/etc/scripts/iris_verify.log`。
- **`load_rules` 不再静默丢弃未知键**：未知顶层键、未知 detect/action 键记入
  `Rule.warnings` 并出现在报告中。这正是 `generic-watchdog-guard.yaml` 里
  `file_pattern`/`condition` 与整块 `post_action_verify` 长期"配置了但从不执行"的原因。
- **`iris emulate guardian-start`**（L2/L3 联动）：按 `--interval` 周期拉取仿真容器串口
  日志，交给 AI 值守模块分析。
- **AI 值守模块 `iris.monitor.ai_guardian`**：7 类串口日志正则模式（watchdog 重启 /
  sysrq / reboot 尝试 / diag 崩溃 / soft lockup / Web 启动与存活）+ 四态健康状态机
  （healthy / degraded / critical / expired，另有找不到日志时的 unknown）+ 四类自愈动作
  （WATCHDOG_RECOVERY / RESOURCE_CLEANUP / DIAGNOSTIC_DISABLEMENT /
  WEB_SERVER_DIAGNOSIS）。
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
- **`tenda-web-server-forced-start` 的运行时启动逻辑无效**：L3 `guest_shell` 动作在
  镜像构建期的 chroot 内执行（`fix_image.sh`），此处没有 `/proc`，且 chroot 退出后
  所有后台进程随之消失——原规则用 `nohup goahead &` 拉起 Web 从来不会生效。同时该
  规则的 boa 分支条件写作 `[ ! -d /proc/meminfo ]`（意图借 chroot 无 `/proc` 绕过，
  但语义仍是错的），且 `&>/dev/null` 是 bash 语法、busybox `sh` 不支持。规则改为只创建
  `iris_net_fix.sh` 启动 Web 所必需的前置文件（`/opt/goahead/route.txt`、
  `/etc/boa/boa.conf`），由脚本层在 guest 启动时完成拉起——各归其位。
- **`generic-diag-crash-fix` 的 init 脚本处置方式**：`find /etc/init.d -name "*diag*" -delete`
  改为 `sed` 注释，避免连带删除同一脚本内的其他启动逻辑。
- **aarch64 架构映射**（`docs/07-架构映射修复.md`）：ELF 普查得到的标准架构名
  `aarch64` 映射到 QEMU 内核标签 `arm64`，修复 arm64 固件被误判为"未知架构"而无法
  自动选参的问题。
- **Windows 换行保真**：`_read_text` / `_write_text` 使用 `newline=""`，
  避免 Windows 上 universal-newline 把所有被修 shell 脚本重写成 CRLF。
- **guest 脚本纯 ASCII 化**：规则 guest_shell 里的 `→`/`✓`/`⚠` 会让生成脚本含非 ASCII
  字节，任何按 locale（Windows 上是 GBK）读取该脚本的工具都会抛 `UnicodeDecodeError`
  ——此缺陷已实际导致 `tests/test_rules.py` 两个用例崩溃。规则内容改为纯 ASCII，
  引擎侧新增 `_ascii_safe()` 兜底。同时为全部测试的 `read_text`/`write_text`
  显式指定 `encoding="utf-8"`，消除同类隐患。
- **`path_exists` 的 `executable` 修饰符跨平台诚实**：`os.access(X_OK)` 在 Windows 上对
  普通文件恒返回 True，会让该修饰符"匹配一切"。改为仅在 POSIX 平台生效，
  其他平台忽略并在 `Rule.warnings` 中声明。
- **AI 值守的 WATCHDOG_RECOVERY 从未真正生效**：脚本被写到**宿主**的 `/tmp`（Windows 上
  即 `C:\tmp`），随后让容器去执行 `/tmp/watchdog-fix-*.sh`——文件从不在容器内，动作恒失败。
  改为 `docker exec -i ... /bin/sh -s` 从 stdin 送入脚本，不落任何中间文件。
- **AI 值守的执行结果失真**：`_cleanup_resources` / `_disable_diagnostic_tools` 丢弃
  `docker exec` 的返回码并无条件 `return True`，导致"未生效"被记为"已修复"。改为以容器内
  命令退出码为准，并用显式标记行二次确认；无 diag 可禁用时返回 False（"无事可做"）
  而非虚报成功。
- **AI 值守把 Web 排查记成修复**：`WEB_SERVER_DIAGNOSIS` 不做任何修复，却进入
  `actions_taken`，报告因此声称"修好了"。新增 `diagnoses` 字段，只排查不修复的结论
  单独存放。
- **AI 值守的 `expired` 状态实际不可达**：超时判定排在 degraded 各项之后，一个运行
  数小时、Web 从未启动的容器会被永远标为 degraded。超时判定前移——监控窗口已过仍未
  达成健康，是结论而不是持续告警。
- **`web_server_active` 识别面过窄**：原正则只匹配 IRIS 自己的一句让位日志，
  厂商自行启动 Web 的 guest 会被误判为"未启动"。补充端口监听与进程绑定痕迹。
- **串口日志按 locale 解码**：`open(log, 'r', errors='ignore')` 未指定编码，Windows 上
  按 GBK 读取原始 UART 字节流，遇到非 ASCII 即抛异常。改为 UTF-8 + `replace`，
  保留可读内容而非中断读取。
- **`monitor/` 缺少 `__init__.py`**：`[tool.setuptools.packages.find]` 不含
  `find_namespace`，无 `__init__.py` 的目录不会被打包——构建 wheel 时整个值守子包丢失。
- **`cli.py` 的 `if __name__ == "__main__"` 位于文件中段**：`guardian-start` 命令注册在
  其之后，直接 `python src/iris/cli.py` 会静默丢失该命令。已移至文件末尾。
- **`EmulationResult.firmware_path` → `rootfs_dir`**：该字段唯一赋值处传入的是 rootfs 目录，
  原名与实际语义不符。
- **AI 值守的时间戳无时区**：`datetime.now()` 产出 naive datetime，一旦宿主时区变化或与
  tz-aware 时间比较即抛 `TypeError`。全部改为 `datetime.now(UTC)`（`last_check` /
  `start_time` / `uptime_seconds` / 动作时间戳），保证跨时区与夏令时下行为一致。
- **CLI 退出码未抑制异常链**：`raise typer.Exit(...)` 跟在已打印过用户可见错误的
  `except` 之后，Python 仍会把原异常挂到 `__context__` 上，调试输出里出现重复且误导的
  双重报错。补 `from None`。
- **guest 验证脚本里的目录名被宿主平台改写**：`mkdir -p {Path(VERIFY_LOG_PATH).parent}`
  在 Windows 宿主上渲染成 `mkdir -p \etc\scripts`，而该行是要在 Linux chroot 里由
  POSIX `sh` 执行的——反斜杠是转义符，这条命令实际创建的是 `./etcscripts`，
  `/etc/scripts` 根本没建，裁决日志落不到预定位置。改为 POSIX 字面量常量
  `VERIFY_LOG_DIR`；新增 `TestGuestScriptIsPosix` 断言生成的脚本不含任何反斜杠。
- **`iris emulate status` 的容器不存在判定是死代码**：`subprocess.run(check=True)`
  已经在非零退出时抛 `CalledProcessError`，其后的 `result.returncode != 0` 分支永不可达。
  同时 `json.loads(...)[0]` 在容器于两次调用之间消失时会抛 `IndexError` 裸栈。改为先取
  列表、判空后再取首元素。
- **`iris emulate list/status` 未处理 docker 不可用**：`docker` 不在 PATH 时抛
  `FileNotFoundError` 裸栈。补 `except OSError` 并给出明确提示。

### 依赖

- **`pyproject.toml` 漏声明 Web 层依赖**：`api/server.py` import `fastapi`、`cli.py` 的
  `serve start` import `uvicorn`，但两者都不在 `dependencies` 里——全新环境
  `pip install -e .` 后 `iris serve start` 直接 `ImportError`。已补
  `fastapi>=0.110`、`uvicorn>=0.27`、`python-multipart>=0.0.9`（`UploadFile`/`File`
  表单解析所需）；`httpx>=0.27` 归入 `dev`（`fastapi.testclient` 的传输层）。

### 变更

- **文档归档**：根目录 10 份散落的英文说明文档归并去重后迁入 `docx/`，并统一改为
  中文命名（详见下节"文档"）。
- **架构映射常量生效**：`_ARCH_MAPPINGS` 取代重复硬编码的映射字典。
- **规则库去重**：`vendor-watchdog-monitor` 与 `generic-watchdog-guard` 功能重叠
  （同为"注释 inittab + 重命名 monitor 二进制"），合并为前者，规则库由 7 条收敛为 6 条。
- **AI 值守日志改用项目统一的 structlog**（`iris.log.get_logger`），并移除模块内
  自带的独立 Typer 入口——`iris emulate guardian-start` 是唯一入口。
- **`get_latest_crash_context()` 接入健康报告**：此前定义了却无调用方；现在异常时随
  计数一并输出最近故障上下文，报告从"有多少次"变成"长什么样"。
- **AI 值守模块 docstring 校正**：原声明的四项能力中"预测性告警"与"兼容性矩阵追踪"
  从未实现，已删除该声明，只保留实际具备的三项。
- **`orchestrator.py` 的 14 处 `print()` 改用 structlog**：进度输出此前绕过项目统一的
  日志通道，既无法按级别过滤也无法落盘；同时把循环内的 `import re` 提到模块级、
  `Image build output` 降为 `debug`（原本是直接吞掉最后 200 字节 stdout）。
- **`determine_peb_size(data, ec_offsets)` 的 `data` 是死参数**：PEB 大小完全由 EC 头
  偏移间距推导，函数体从未读 `data`；调用方与测试却必须为此传入镜像字节。已删除该
  参数。
- **`auto.py` 中重复定义的 `_pick_arch_from_counter`**：同名函数写了两遍，后者覆盖前者，
  实际生效的是引用 `_ARCH_MAPPINGS` 的版本。删除重复定义。
- **`SerialLogAnalyzer.PATTERNS` 标注 `ClassVar`**：字典类属性不带标注会被静态检查视为
  实例可变状态。
- **`ruff check` 告警清零**：本轮结束时 `ruff check .` 为 `All checks passed!`
  （起始基线 50 项）。三处有意保留的宽边界捕获改为 `noqa` 并写明理由
  （`auto.py` 的"提取永不抛异常只上报"契约、`cli.py` 的 arch 探测、值守 watch 循环的
  存活性），而非机械收窄成具体异常类型——收窄会让未预料的异常穿透到调用方。

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
- **规则条数表述校正**：README、`docx/免安装使用指南.md`、`docx/AI值守与稳定性治理.md`
  中"7 条规则"按合并后的实测值改为 6 条，并同步修正被删除的
  `generic-watchdog-guard.yaml` 在归并文档中的残留引用。

### 移除

- 死代码与未引用符号：`qemu_config.build_qemu_args()`（与真实 `run_qemu.sh` 的
  TAP+bridge 网络模型已漂移，构成误导）、`arch.identify_file()`、
  `config.reset_settings()`、`EmulationResult.ping_ok` / `EmulationResult.qemu_pid`
  （定义后从不赋值）。
- 临时调试文件：根目录 `test_guardian.py`、`test_manual_usage.py`（硬编码本机绝对路径、
  且 `SerialLogAnalyzer(iid=...)` 参数写错因而从未成功运行；其覆盖内容已重写为
  `tests/test_guardian.py` 共 38 个用例）、`iris-home/scratch;C`（Windows 路径误转义产物）。
- `auto.py` 中永不生效的分支（检查引擎从不产生的 `"SKIPPED(binary)"` 标记），改为把
  `Rule.warnings` 汇入 `PreparedRootfs.notes`——规则里的未知键不再被丢弃后无人知晓。

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