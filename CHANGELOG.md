# 更新日志

本文件记录 IRIS（IoT Rehosting & Interconnection Simulator，鸢尾）的版本变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.3.9] - 2026-10-02

`iris_net_fix.sh` 每次启动都往串口日志里写「正在 7002 端口启动 telnetd」，而该端口从未被
绑定过。上一轮把这个日志行改成了带返回码的函数，发现按绝对路径查找后 telnetd **确实绑定了**
——然后容器侧 `socat` 仍然三次全部 `Connection refused`。于是有了本轮：一串「声称成功、
实测失败」，以及最终把谎报换成证据的过程。

### 修复

- **自愈动作谎报成功**。`_WATCHDOG_SCRIPT` / `_CLEANUP_SCRIPT` 无条件
  `echo ...-APPLIED; exit 0`（同批发现的 `ai_guardian` 问题，见下）。本轮修的是同一类缺陷在
  guest shell 侧的那一份：`ensure_command_channel` 现在返回码与日志行由**同一分支**产生，
  rc=0 当且仅当出现「listening on :7002」，rc=1 当且仅当出现「no command channel」。
- **`command -v telnetd` → 绝对路径候选表**。guest 的 PATH 是厂商 init 留下的样子：telnetd
  在 `/sbin` 而 PATH 里没有 `/sbin` 的固件，有 shell，却被 PATH 判定为「没有命令通道」。
  `IRIS_TELNETD_CANDIDATES` 可覆盖，报告里列出搜索过的路径，使这类固件在串口日志里可见。
- **nc 探测**（新增）。原来只有 netstat 文本匹配，现在追加真实连接尝试
  （`IRIS_NC_CANDIDATES`，同样绝对路径查找）。无客户端的固件降级为只信 netstat 并**明说降级**。
- **`bindv6only` 放开**。启动 telnetd 前 `echo 0 > /proc/sys/net/ipv6/bindv6only`，
  因为 OpenWrt 衍生固件默认 `bindv6only=1`，而 busybox telnetd 绑 IPv6 通配地址。
- **`setsid` 脱离**。telnetd 是几秒后就要退出的 boot hook 的子进程。
- **`/proc/net/tcp{,6}` 作为内核权威证据**。区分「端口在监听（0A）」与「端口曾经在表里
  （如 TIME_WAIT 06）」，这是文本匹配做不到的。

### 诊断（本轮的主要产出）

失败报告不再是一句结论，而是五路证据同在一行：`pidof` 原话、`netstat -lan` 原文、
客户端原话、`bindv6only` 当前值、`/proc/net` 原文。正是这些证据把问题定位成
「telnetd 只绑了 IPv6 且 QEMU 用户态网络不转发 IPv6」，而不是任何一个布尔值能说明的。

### 实测结论（TES7002，arm64，BusyBox 1.22.1）

**通道路线在该固件上不可用，这是固件限制而非脚本缺陷**，已如实报告而非掩盖：

- telnetd 绑定成功但**只服务一次连接**：实测 `/proc/net/tcp6` 留下
  `::ffff:127.0.0.1:1B5A ... TIME_WAIT`、netstat 同步显示
  `::ffff:127.0.0.1:7002 ::ffff:127.0.0.1:32978 TIME_WAIT`——连接**建起来过**，
  但 socket 之后不再 LISTEN。
- 由此得到两条**已知局限**（下一轮处理）：nc 的退出码不表示连接成功（成功也可返回非 0），
  且 nc 探测本身**会消耗掉一次 inetd 风格会话**，使后续轮询失去对象。
- `bindv6only` sysctl 对本固件无效：BusyBox 1.22.1 的 telnetd 在 socket 层显式设置
  `IPV6_V6ONLY`，socket 级标志优先于 sysctl。该行保留——不设 V6ONLY 的固件仍需要它。
- BusyBox 1.22.1 的 `nc` 只接受 `nc [IPADDR PORT]`，`-w` / `-6` 均报 `invalid option`，
  且在返回码上与「端口拒绝连接」无法区分。测试替身现在复现这一行为。

### 测试

- `tests/test_guest_shell_channel.py`（51 项）：用 `add_func` source 技巧驱动真实脚本，
  nc / telnetd 替身是真实可执行文件。替身**复现真实 BusyBox 的拒绝行为**——一个会忽略
  自身参数的替身会让整类探测 bug 溜过去。
- `tests/test_guest_shell_scripts_are_lf.py`（12 项）：本轮两次栽在 CRLF 上——Edit 工具在
  Windows 上把整个 `.sh` 写成 CRLF，host 上 `bash -n` 通过、Git 的 `*.sh text eol=lf` 管不到，
  guest BusyBox ash 直接崩（`line 7: : not found`）。该守卫在改完任何 shell 脚本后必跑。
- **11 项变异验证全部如期变红**，其中两项先变绿、暴露了真实盲区后补齐测试：
  - 变体「给 nc 加回 `-w`」最初测不出来——host 上存在 `/usr/bin/timeout`，使脚本永远走
    带包裹的分支，无 `timeout` 的 guest 从未被覆盖。
  - 变体「把 `setsid` 条件改成永假」测不出来——原断言是数启动行个数，改成正则匹配条件本身。

### 遗留（未修复，已记录）

- `src/iris/monitor/ai_guardian.py` 的两处无条件成功标记（`_WATCHDOG_SCRIPT` /
  `_CLEANUP_SCRIPT`）与 `_exec_in_guest` 实为容器 exec 而非 guest exec，**尚未修复**。
- 通道路线需要另一条设计（guest 内第二串口 `inittab` respawn、或 QMP），见下轮。

## [0.3.8] - 2026-10-02

新增架构此前要改八处，而且已经改乱了：`cli.py` 里两份逐字相同的内联 dict、
`emulate/auto.py` 与 `emulate/orchestrator.py` 各一份私有副本、提取层三份各自实现的
`e_machine` 解析（其中两份对大端 ARM 一律报 `armel`），再加 `extract/arch.py` 自己发明的
第四种写法 `arm64le`。这些副本的漂移是实际生效的：API 层把 `aarch64` 判为不支持，而这正是
ELF 头里写的名字。

### 新增（`src/iris/arch.py`：架构命名的唯一权威）

- **`normalize_arch()`**：任意拼法归一到内核资产名。`aarch64` / `arm64` / `arm64le` /
  `ARM64` 都得到 `arm64`；未知名字原样返回，让报错能引用调用方真正给的东西。
- **`census_to_runnable()`**：ELF census 标签 → 可启动架构，不可启动返回 `""`（调用方据此
  继续看下一个信号）。`x64` / `mips64le` / `armeb` 不映射到任何现有内核——大端 ARM 跑在
  小端 `zImage.armel` 上会执行完 init 脚本再 misexec，mips64 跑 `vmlinux.mipsel.4` 同理。
- **`census_label()`**：`e_machine` + 端序 + 位宽 → 标签的唯一实现，含 64 位 MIPS 与 32 位
  MIPS 的区分。
- **`LITTLE_ENDIAN_ARCHS` / `is_little_endian()`**：从串口日志解码 guest 地址时用。判错会
  得到一个「看起来合理」的错地址而不是显性失败，`mipseb` 是唯一大端成员。

### 修复（收口过程中暴露的真实缺陷）

- **API 层读 ELF 时无视 `EI_DATA`**：`int.from_bytes(data[18:20], "little")` 硬编码小端，
  大端 MIPS 的 `e_machine` 8 被解成 2048，落到 `unk` 分支。改用 `identify_elf`。
- **API 层拒绝 `aarch64`**：现在先归一化再判定，拒绝时列出支持列表。
- **`FIRMAE_SUPPORTED_ARCHS` 答的是别的问题**：它答「FirmAE 能不能」，而调用方要的是
  「IRIS 能不能」，两者矛盾——arm64 在这里能跑（自有 `Image.arm64` + initramfs）而那个集合
  说不能。该集合删除，`ArchInfo.firmae_supported` 更名 `runnable`，判定来源改为
  `census_to_runnable`。
- **`preflight_arch` 对真架构但无内核的情况放行**：大端 ARM 固件会被放去启动，报出来的是
  「仿真器坏了」而不是「这个架构没内核」，用户无从分辨。现在返回 `UNSUPPORTED_ARCH`。
- **`census_label` 缺位宽参数会把 64 位 MIPS 标成 `mipsel`**：共享 `e_machine` 8，
  误标等于给 64 位用户空间配 32 位内核。端序不可知时返回 `mips` / `arm`（机器已知、
  决定标签的那一个事实未知）而不是猜一个 `mipsel`——固件镜像里截断的厂商 blob 会走到这条
  路径，`census_to_runnable` 对这两个标签一律拒绝。
- **`EM_*` 常量与标签映射分居两处**：收口时新引入的重复——`iris/arch.py` 的 `_EM_LABELS`
  用裸数字，而 `extract/arch.py` 另有一份 `EM_MIPS = 8` 命名常量。常量搬到权威模块，
  `extract/arch.py` 改为 re-export，`iris.arch` 不反向依赖提取层。

### 变更

- **`qemu_config.py` 补全为 `run_qemu.sh` 的镜像**：新增 `cpu` / `console` / `initramfs`
  字段，删除名不副实且无消费的 `net_device` / `net_backend`（后者被填成了同一个设备模型名）。
  此前 dataclass 的 8 个字段在生产代码中**零消费**——唯一调用是 `get_config(arch) is None`
  判定位——而脚本 arm64 分支里的 `-cpu max`、`console=ttyAMA0`、initramfs 三个设置 Python
  侧连字段都没有。`-cpu max` 不是细节：`cortex-a72` 无法执行厂商 aarch64 二进制的 ARMv8.3
  pointer-auth，guest 会 SIGILL。
- **`tests/test_qemu_config_matches_script.py`**：解析脚本的 `case` 块（不是 grep 字符串），
  逐字段比对两侧。跨语言无法复用同一份数据源，所以用一致性守卫替代。Python 侧字段此前
  无人读取，也就无人会注意到它错了。
- **守卫 `tests/test_arch.py`**：除行为测试外，反向断言旧副本不能回来——禁止模块自定义
  架构映射（扫源码找 `"aarch64": "arm64"` 这种形状）、要求每个映射 arch 的模块都经过
  权威入口、禁止 `FIRMAE_SUPPORTED_ARCHS` 复活（只匹配代码行，不匹配解释移除原因的注释）、
  要求 `RunnableArch` 与 QEMU 配置集合相等。白名单只保留 `arch.py` 与 `qemu_config.py`：
  `api/server.py` 与 `cli.py` 是在内联 dict 删除之后才退出的，一条没人需要的豁免就是一个
  守卫再也看不穿的洞。

### 测试修正

- **`tests/test_preflight.py` 的 ELF 夹具硬编码 `EI_CLASS=2`**：即造出 64 位二进制。census
  从不读该字段，所以 mips64 被报成 `mipsel`，整份文件是在一个谎上通过的。读位宽之后转红，
  夹具改为显式参数化。

### 实测（TES7002 arm64 真实固件）

| 步骤 | 结果 |
|---|---|
| `iris emulate run <rootfs>`（**完全省略 `--arch`**） | census 产 `aarch64` → 归一化 `arm64`，80s Web 可达 |
| 落库 | `emulation_run` 2 行，均 `arch=arm64` / `image_id=11` |
| `iris db stats` | `2 recorded run(s)` / `web reachable 2/2 (100.0%)` |

### 变异验证（7 项，全部如期变红并恢复）

Python 表丢掉 `-cpu max` / 脚本把 `-cpu max` 改回 `cortex-a72`（精确模拟 SIGILL bug）/
脚本单侧把 `mipsel)` 改名 `mipsel64)` / 去掉 `census_to_runnable` 的字节序防护 /
删除 `aarch64` 别名（7 个测试红，含 `test_preflight` 的 aarch64 用例）/
把内联 `arch_map` 加回 `cli.py`（守卫收紧后立即变红）/
把 `EM_MIPS = 8` 改成 `80`（5 个测试红）。

### 遗留

- `image.arch` 登记名与 `emulation_run.arch` 运行时名的口径差异仍在：归一化后
  `aarch64` 在进入编排器时就变成 `arm64`，而语料表里是 `aarch64`。两份数据的对齐需要一个
  迁移决策（改历史行 or 改查询），本次未做。

## [0.3.7] - 2026-10-02

### 新增（仿真结果落库，此前每张统计表都是手工填的）

- **`src/iris/db/runs.py`**：`emulation_run` 与 `failure_profile` 此前只有表定义、
  零写入路径（`grep -r EmulationRun src/` 只匹配到模型本身）。`docs/eval-log.md`
  里每一个百分比都是人工填写、无法由代码验证的——这正是同一份文档能一边写
  「4/5 启动成功」、一边列出五行全部 ✅ 的原因。现在每次仿真自动落库。
- **`record_run()` 记 primary 失败 kind + 每个 finding 各一行**。一次起不来的固件
  通常叠着多个原因（无网卡 / 无地址 / 无 web 服务），压成一个桶会让人看不出该先修
  哪个。无法归属语料的运行 `image_id` 存 NULL 而不是硬塞最近的候选：错归属会静默
  污染全部按固件统计的数字，缺归属是看得见的。
- **`iris db stats`**：从表里读回总运行数、Web 达标率、按 arch 分解、失败直方图。
  空表时明确说「尚未记录任何运行」而不是打印 `0.0%`——把 0/0 渲染成百分比，
  和「全部固件都没起来」在读起来时完全一样。
- **`src/iris/failures.py`**：闭合的失败分类法。`Stage` 7 类 / `FailureKind` 23 个，
  每个 kind 都有唯一 stage 和一句可执行建议。

### 修复（分类法与真实故障对齐）

- **`Stage.NVRAM` 此前是死阶段**：DB 注释与 `docs/eval-log.md` 都宣称有 `nvram`
  阶段，但没有任何 `FailureKind` 映射到它。真实故障 `nvram partition is destory`
  → 28 次 reboot 恰恰是 nvram 问题，此前只被笼统归为 `REBOOT_LOOP`（stage=boot），
  看直方图的人会被导向「等更久」或「删 reboot 二进制」。新增
  `FailureKind.NVRAM_UNREADABLE`（stage=nvram），reboot trigger 命中 nvram 时
  额外产出该 finding。
- **`NETWORK_FALLBACK_OK` 曾被错标为 `WEB_UNREACHABLE`**：「IRIS network fallback ran」
  是**正常信号**，当成失败会污染失败直方图。新增 `INFORMATIONAL_KINDS` 与
  `Failure.is_failure`，`BootDiagnosis.primary` 与聚合统计均跳过它。
- **`session.execute(select(Model))` 返回以实体为键的 Row 而非实体**，
  `run.web_reachable` 会抛 `KeyError`。改用 `session.scalars(...)`。
- **`init_db` 只 `create_all` 不 ALTER 已存在的表**：旧库的 `emulation_run` 缺
  `image_id`/`arch` 时每次插入都失败，症状读起来像「落库坏了」。新增幂等
  `ensure_columns()`。
- **落库点移进薄包装层**：初版把 `_record_outcome` 放在 `emulate_firmware` 函数末尾，
  所有 early return（arch 不支持 / tarball 失败 / 镜像构建失败）全部绕过落库——
  而这些恰恰是手工表格最容易漏掉的运行。重构为 `emulate_firmware`（薄包装，负责
  落库）+ `_emulate_firmware`（原实现）。
- **删除残留的 `if record:` 调用**（`_emulate_firmware` 末尾）：薄包装重构时漏删，
  主路径（成功/最终失败）会抛 `NameError`。由 `tests/test_run_recording.py` 抓到。
- **`Failure` 的证据此前仍可事后改写**：`frozen=True` 只阻止属性重新绑定，
  `evidence` 仍是可变 dict，且 `MappingProxyType` 直接包原 dict 也拦不住持有原引用的
  构造方。现在 `__post_init__` 内拷贝后再包只读代理。
- **直方图与按阶段统计曾用两套 stage 判定**：`failure_histogram()` 二次解析 kind，
  对未知 kind 的历史行回退到 `infra`；而 `run_stats()` 回退到存储的 stage。同一行数据
  会在两个视图里落在不同阶段。现在直方图直接读 `run_stats` 已解析的结果。

### 测试修正

- **`"make_image.sh" in cmd` 对 argv list 是成员判断而非子串匹配**：mock 从未生效，
  落库用例实际走完了整个 120s 启动超时才到达断言点，即被测分支一次也没执行
  （该文件单次运行从 124s 降到 3.5s）。

### 变更（iid 外键语义修正）

- **`emulation_run.iid` 去掉 FK，改普通 int；新增可空 `image_id`**：模型声明
  `iid → image.id`（CASCADE），但所有调用方传的 `iid` 是 scratch 运行号
  （`md5(rootfs) % 10000`），与语料 id（1~11）几乎无交集。把运行号写进一个声明为
  语料 id 的列，是 schema 无法表达的谎。
- **测试不再污染真实语料库**：`tests/conftest.py` 用 session 级 fixture 把
  `IRIS_DATABASE_URL` 重定向到临时文件。`iris-home/iris.db` 是 11 个语料固件与
  全部评估数字的唯一来源，测试往里写假运行等于把假固件混进被评测的集合。
- **删除 `docs/eval-log.md` 中无代码路径的阶段** `wizard` / `verify`（见该文件文首）。
- **更正「4/5 启动成功」**：按同文档上表应为 5/5（x86/64 一行是「—」，不在仿真范围）。

### 已知口径差异（留待 P1-a 收口）

- 语料登记名 `image.arch`（如 `aarch64`）与运行时名 `emulation_run.arch`（如 `arm64`）
  目前不是同一套词表，`iris db stats` 的「by arch」按运行时名分组，暂不能与语料表
  `arch` 列直接对齐比较。

### 实测（TES7002 arm64 真实固件）

| 步骤 | 结果 |
|---|---|
| `iris emulate run`（tarball 命中缓存） | 72s，Web 可达 |
| 落库 | `emulation_run` 1 行：iid=9001, arch=arm64, web=1, image_id=11（自动归属） |
| 失败路径落库 | 真实走通 `unsupported-arch`：`failure_profile` 写入 stage=arch + hint，探针行已删除 |
| `iris db stats` | `1 recorded run(s)` / `web reachable 1/1 (100.0%)` |

### 变异验证（5 项，全部如期变红并恢复）

去掉包装层落库调用 / 让 informational finding 成为 primary / 删除 `_KIND_STAGE` 条目 /
去掉 `Failure` 证据的只读包装 / 让直方图二次解析 stage。

## [0.3.6] - 2026-10-02

### 修复（`WEB_SERVER_RESTART` 此前必然救不回，2026-10-02）

- **重启容器不等于重启仿真**：容器的 PID 1 是 `sleep 3600`，QEMU 由
  `docker exec -d` 另起，两个 Dockerfile 都没有 `ENTRYPOINT`/`CMD`，所以
  `docker restart` 只把 `sleep` 拉回来，`run_qemu.sh` 一次都不会重跑——原动作
  必然在 120s 探活窗口内超时，再被 600s 冷却挡住。原测试把 `subprocess.run`
  整个 mock 掉，因此这条路径从未被真正验证过。现在重启后会重新
  `exec /work/scripts/run_qemu.sh <iid> <arch> <port>`。
- **arch 有了落点**：`make_image.sh` 在 `image.raw` 旁写下 `arch` 文件。容器里
  没有别处记录过 QEMU 是用什么架构起的，而这个文件在容器的可写层里，
  `docker restart` 不丢（实测重启前后均在，mtime 不变）。
- **两个前置条件挪到重启之前检查**：重启会杀掉正在跑的 QEMU，事后才发现无法
  重新拉起，等于把"活着但不服务"降级成"什么都没跑"，比调用前更难排查。读不到
  arch 或没有探活端口时，现在直接拒绝且**不碰容器**。
- **读不到 arch 时不再假装成功**：`_read_launch_arch()` 返回空串即视为无法重启，
  记 error 并返回 `False`。

### 新增（launch arch 标记的双端静态守卫）

- **`tests/test_launch_arch_marker.py`**：`make_image.sh` 写的路径与
  `ai_guardian.py` 读的路径是同一件事的两份副本，两边不一致时单测（mock 掉
  docker）、guardian（只记一行 error）、用户（看到一个静默无效的修复）都不会
  察觉。守卫从脚本自身推导写入路径、并从 `run_qemu.sh` 推导位置参数顺序，
  而不是把结论抄一遍——任一端改名或改顺序即测试变红。
- **`tests/test_version_sync.py`**：`pyproject.toml` 与 `iris.__version__` 此前
  已经漂移到 0.3.5 / 0.3.3，两个数字都不能当作"当前版本"。现在两者相等、纯
  SemVer、且 CHANGELOG 必须有对应版本节。

### 实测（TES7002 arm64 真实固件，容器 `iris-qemu-9001`）

| 步骤 | 结果 |
|---|---|
| 起仿真 | 76s 后 HTTP 302，容器内 `arch` = `arm64` |
| 容器内 kill qemu + socat | HTTP 000 |
| `analyze_health()` | `degraded` / `started_but_stopped`，建议 `WEB_SERVER_RESTART` |
| `execute_recovery()` | 77s 返回 `True`，Web 恢复 302，`image.raw` mtime 未变 |
| 负向对照：只 `docker restart` | qemu 进程数 0，87s 后仍 HTTP 000 |

四次守卫变异验证（删除标记写入 / 改名标记 / 交换重连参数顺序 / 把重启提前到前置
检查之前）均如期变红，恢复后全绿。

## [0.3.5] - 2026-10-02

### 新增（`iris extract rootfs` 可指定输出目录，2026-10-02）

- **`--out` / `--force`**：README 一直把 `iris extract rootfs --out ./rootfs_out`
  写进快速上手，但 CLI 从未接受过该参数——照文档执行会直接报 "No such option"；
  即使提取成功，产物也只在 `iris-home/scratch/<固件名>-rootfs` 里，下一步
  `iris emulate run ./rootfs_out` 接不上。现在 `--out` 把提取出的 rootfs 树写到
  调用方指定的路径，文档描述的分步流程真正可用；不给 `--out` 时行为与旧版一致。
- **中间产物仍留在 scratch**：squashfs 切片与 TendaW 的 `-parts` 缓存是可再生的
  临时文件，不跟随 `--out` 搬走。
- **非空目录需显式 `--force`**：scratch 下的派生目录归 IRIS 所有，清掉即可；而
  `--out` 指向的是调用方自己的资产，可能放着别的工作。目录非空时按退出码 2 拒绝
  并**不做任何删除**，只有 `--force` 才替换内容。目标是已存在的文件时同样拒绝。

### 修复（`--out` 本会被静默忽略到 scratch，2026-10-02）

- **`_docker_unsquashfs` 只把目标目录的 basename 交给容器**：docker 单个 volume
  以 squashfs 所在目录为挂载源，代码用 `dest_dir.name` 拼容器内路径，于是 `--out`
  的父目录链被整段丢弃——命令打印成功、退出码 0，rootfs 却落在
  `iris-home/scratch/<basename>`。现在改为计算能同时容纳切片与目标的**最紧公共
  祖先**作为挂载源，两者都按相对路径寻址。
- **跨盘目标不再无界上跳**：公共祖先不存在时（上跳到卷根仍不包含另一个目标），
  立即报 `ValueError` 说明需同盘，而不是死循环。
- **`unsquashfs` 不会为 `-d` 补建父目录**，而 bind mount 只暴露宿主已存在的路径，
  因此写入前先在宿主侧 `mkdir -p` 目标，避免多层 `--out` 静默失败。

### 修复（`--out` 参数遮蔽了 logger，2026-10-02）

- `extract_rootfs()` 内把选项命名为 `out`，遮蔽了模块级
  `out = get_stream_logger()`，函数体第一行就抛
  `AttributeError: 'WindowsPath' object has no attribute 'info'`。ruff 与编译器
  都不会报错（这是合法 Python 的名字重绑定），只有真实执行才会暴露；已补
  `tests/test_cli_output.py` 的 subprocess 用例固定住。

### 实测

真实固件 `US_AC15V1.0BR_V15.03.05.18_multi_TD01.bin`（uimage + squashfs，
ELF 普查 334/334 armel）：`--out .verify_out` 提取出 672 个文件的 rootfs 树且确实
落在 `.verify_out`，scratch 未出现同名误落目录；重复执行被拒（退出码 2，用户文件
未删），`--force` 后重提取成功（退出码 0）；该目录可直接作为
`iris emulate run .verify_out --arch armel` 的输入并起壳。

## [0.3.4] - 2026-10-02

### 变更（所有输出统一带时间戳与色彩，2026-10-02）

- **每条输出都是 `2026-10-02T02:11:00 [info] 消息`**：此前 `emulate` 的进度行带
  `[info]` 前缀而 `extracting rootfs from` / `L3 rules matched` 是裸文本，同一屏里
  两种格式并存。现在时间戳（本地墙钟，ISO 8601 基础形式）与等级标签由
  `iris.log.format_line` 统一渲染，stdlib 与 structlog 两条通道输出同形。
- **等级带颜色**：`debug` 青、`info` 绿、`warn` 黄、`error` 红。是否着色遵循
  `NO_COLOR` > `FORCE_COLOR` > `TERM=dumb` > `isatty()` 的优先级；**不满足条件时
  一个转义序列都不输出**（此前存在无色路径下仍漏出一个孤立 RESET 的情况）。
- **`out.block()` 表格输出**：一条记录 = 一个带时间戳的表头 + 缩进的数据行，
  支持整表单色或逐行着色（`emulate list`、`guardian log`）。逐行着色数量与行数
  不一致时抛 `ValueError`（无色时同样校验），避免颜色与数据错位而不自知。
- **`StatusLine` 的擦除宽度改按可见列宽计算**：SGR 转义字节宽度为 0，按文本长度
  算会擦不干净 27 字符的等级前缀，按含转义的字节数算又会多擦 17 列——两处都曾是
  真 bug，表现为进度行残留或吃掉正常输出。
- **错误信息只走 stderr**：结果块与失败原因改用 `get_error_logger()`，
  `... > out.json` 不再混进诊断文本。
- **唯一的裸 stdout 例外**：`rules apply` 的 JSON 报告用
  `sys.stdout.write(...)` 直接输出，因为 `| jq` 管道必须保持可解析；已在代码注释
  与 `tests/test_cli_output.py` 中作为**显式守卫**固定下来。

### 修复（输出层的裸打印全部归零，2026-10-02）

- **`cli.py` 与 `corpus/manifest.py` 共 113 处 `typer.echo`/`secho` 改造为 logger**：
  裸输出绕过等级、颜色与 stderr 分流，`--json` 之类的机器可读输出因此被进度信息
  污染。`tests/test_cli_output.py` 同时做静态守卫（不允许再出现
  `typer.echo|secho` 或裸 `print(`）与真实 subprocess 执行（`rules list` /
  `corpus list` / `emulate run` / `rules apply`）。

### 修复（armel 固件的仿真兜底完全没执行，2026-10-02）

针对 `US_AC15V1.0BR_V15.03.05.18_multi_TD01.bin`（arch=armel）：120 秒后 HTTP 000，
串口日志反复出现 `Sent SIGTERM to all processes`。定位到四个独立原因：

- **`/etc` 不在 `/etc`**：该固件的 `/etc` 是指向空目录 `/var/etc` 的绝对符号链接，
  真实配置在只读的 `/etc_ro/`，而 `etc_ro/init.d/rcS` 会 `cp -rf /etc_ro/* /etc/`
  把镜像里写进 `/etc` 的东西在运行时丢掉。`inject_boot_hooks.sh` 现在**先探测
  启动配置在哪棵树**（`etc/inittab` 不存在且 `etc_ro/inittab` 存在即切到 `etc_ro`），
  inittab 条目与 rcS 尾钩都随之指向真实位置。
  规则引擎同步适配：`_resolve_etc_layout` 让 `path_exists`/`dir_nonempty` 在
  `/etc` 下确实没有该路径时回退到 `etc_ro/`，`_scope_globs` 让 `etc/init.d/*`
  这类 glob 同时匹配两棵树——`vendor-watchdog-monitor` 与
  `tenda-web-server-forced-start` 两条规则因此重新命中。
- **兜底注入被关在 arm64 分支里**：`inject_boot_hooks.sh` 的调用原本在
  `make_image.sh` 的 `if [ "${ARCH}" = "arm64" ]` 块内，等于**除 arm64 外所有架构
  都没有兜底**。调用已移出该块并注明理由。
- **`iris_net_fix_bg` 找不到自己的邻居**：它硬编码 `/etc/init.d/iris_net_fix`，
  而自己被装在 `/etc_ro/init.d/`，且 inittab 通道运行时 `/etc/init.d` 还不存在
  （要等 rcS 的 `cp`），于是 sysinit 钩子——**唯一不等厂商链的早启动通道**——直接
  报 `can't open '/etc/init.d/iris_net_fix'`。改为从 `$0` 推导目录。
  顺带发现该 busybox **没有 `dirname` applet**（`dirname: applet not found` 会让
  `$0` 的目录塌成空串，转而在 `/iris_net_fix` 找），`acquire_lock` 里同样的
  `dirname` 依赖也一并换成纯参数展开 `${VAR%/*}`。
- **厂商 Web 服务器不在 80 上**：该固件的 `nginx.conf` 写死 `listen 8180;`，
  靠一个 `cfmd` 进程把 80 转发过去，而 `cfmd` 在启动后约 2 秒即 SIGSEGV
  （`cfmd recv segv signals and reboot the system`）——`rm /sbin/reboot` 拦不住
  `reboot(2)`。兜底新增 `vendor_web_port`（只取未注释的 `listen` 指令，避开
  配置里 8000/443/somename 三个注释示例）与 `redirect_to_port80`（优先 iptables
  DNAT，不可用时改写配置并 HUP nginx）。

### 新增（仿真失败的结构化诊断与 reboot 看门狗，2026-10-02）

- **`diagnose_boot_failure`**：HTTP 000 原本要求人工在六千行串口日志里翻找根因。
  现在按链路顺序产出五条 finding（reboot 循环及其**具体触发源**、兜底是否执行、
  非 loopback 地址、网卡驱动、web 进程实际 bind 的端口），每条都说明检查了什么，
  判断错了会表现为"某条探针没命中"而不是自信的错误结论。
  触发源区分 `nvram partition is destory`（flash 分区未被仿真，厂商**主动** reboot）、
  `envram_init: read flash error`、`Could not open mtd device` 与
  `recv segv signals and reboot`（厂商自身 bug），因为它们需要的修复完全不同。
- **reboot 看门狗**：轮询期间统计 guest 的 `firmadyne: sys_reboot` 次数，
  达到 3 次立即判定失败。实测该固件的失败反馈从 120 秒缩短到 **11 秒**。

### 修复（QEMU virtio-mmio 传输层，2026-10-02）

- **`virt` machine 的 virtio-mmio 总线默认 `force-legacy=true`**，把所有设备钉在
  legacy 传输上。`zImage.armel` 内建 `CONFIG_VIRTIO_NET`（已在 vmlinux 中确认
  `virtio_net.c` 的 `__FILE__` 与模块参数字符串），但磁盘能挂载、网卡静默消失：
  没有 `eth0`、ARP 无人应答、120 秒后 HTTP 000。命令行补
  `-global virtio-mmio.force-legacy=false`；对使用 PCI e1000 的 mips machine 无影响。
  注：原判断"armel 内核缺 virtio_net 驱动"是错的，仓库里的 3 份
  `virtio_net.ko` 全是 AARCH64，与 armel 无关。

### 修复（`redirect_to_port80` 会写出非法 nginx 配置，2026-10-02）

- 改写 `listen` 的 sed 替换文本是 `\1listen       80;`，而捕获组 `\1` 本身已经
  包含了 `  listen  ` 前缀，展开后得到 `listen listen 80;`——nginx 会拒绝启动，
  于是"修复"本身变成了"唯一的 Web 服务器消失"。改为 `\1 80;`，
  并加断言确保改写后的行仍然只有一个 `listen` 关键字。


## [0.3.3] - 2026-10-02

### 修复（arm64 固件的 Web 兜底从未执行，2026-10-02）

- **`iris emulate run` 不再永远停在 `HTTP 000`**：某 arm64 固件（TES7002）的
  `/etc/init.d/rcS` 按 `rc0..rc63` 顺序跑厂商脚本，而其中某个脚本在仿真环境下会
  **永久阻塞**（等真实硬件，或等一次阻塞的 mib 调用）。IRIS 唯一的网络/Web 兜底
  挂在 rcS **末尾**，于是被同一个阻塞连带杀死——串口日志里从头到尾没有一行
  `IRIS-NETFIX:`，这正是该判断的决定性证据（实测卡点每次不同，分别停在 rc50、rc63）。
- **兜底改挂 inittab 的 `::sysinit:`，并插在第一个 `::sysinit:` 之前**：BusyBox init
  顺序执行 `::sysinit:` 并逐个阻塞等待，所以追加在 rcS 之后的条目仍然要等整条厂商链跑完
  才有机会执行。新增 `iris_net_fix_bg.sh` 承担后台化——BusyBox 对 process 字段是
  **verbatim exec**，不解释 shell 元字符，写在 inittab 里的尾部 `&` 只是一个普通参数。
  修复后串口稳定出现 `starting pid 396 ... iris_net_fix_bg` **早于** `starting pid 398 ...
  rcS`，`http://localhost:8080/login.html` 返回 200。
- **新增 `scripts/emulate/inject_boot_hooks.sh`**：inittab 注入与 rcS 追踪从
  `make_image.sh` 抽出，使其可针对合成 `/etc` 直接执行验证。两处插入都是**幂等**的
  （先 `grep -v` 清旧标记再插），重复构建镜像不会叠加条目。
- **rcS 逐步追踪**：rcS 把每个 rc 脚本的输出全部丢进 `/dev/null`，卡住的启动和成功的
  启动在日志里长得一模一样。现在每个 rc 前后各打一条 `IRIS-RC: begin/end` 到
  `/dev/console`，能直接看出厂商链走到哪一步、卡在哪个脚本。
  替换文本里的 `&` 必须写成 `\&`：sed 把裸 `&` 当作"整个匹配"，会把 `2>&1` 改写成
  `2>sh $rc_file > /dev/null 2>&11`，症状是 rcS 报 `can't create sh` 而整条链不执行。
- **`iris_net_fix` 加互斥锁**：兜底现在有两个入口（inittab 与 rcS 末尾），不加锁时两者
  都会探测到 :80 为空并各拉起一个 goahead，落败的那个报 `Cannot bind to address *:80`。
  锁用 `mkdir` 原子性实现，`/proc/<pid>` 不存在即视为上次启动的残留锁并清理。
  两处边界：`/var/run` 缺失时 `mkdir -p` 先建父目录（锁本身仍用不带 `-p` 的 `mkdir`，
  `-p` 会让两个竞争者都拿到成功）；锁目录**根本建不出来**（只读 `/var/run`）时按"非冲突"
  处理继续执行——此时静默退让等于悄悄关掉唯一的兜底，且不留任何痕迹。
- **baked 仿真镜像按脚本指纹打 tag**：`Dockerfile.baked` 把 `scripts/emulate/` COPY 进
  镜像，因此镜像**就是**这些脚本的编译产物。原来固定打 `:latest` 且"存在即复用"，
  于是每次改 shell 脚本都静默失效：运行照常成功，容器里跑的是上一版 `make_image.sh`
  ——与"修复没生效"完全无法区分。tag 改为 `iris-emulate-baked:<12位 sha256>`
  （覆盖 `Dockerfile.baked` + `scripts/emulate/*`），构建后清理其余旧 tag。

### 改进（日志格式与流式进度，2026-10-02）

- **全局统一为 `[info] / [warn] / [error]`**：structlog 的 `ConsoleRenderer` 会把级别名
  补齐到固定列宽以做表格对齐，非彩色终端上就表现为 `[info     ]` 这样一串尾随空格；
  而同进程内 uvicorn/FastAPI/root logger 走 stdlib `logging`，输出的是**完全没有级别标签**
  的裸消息，两种形状混在同一次运行里。改用自写 `PlainRenderer`（structlog 侧）与
  `PlainFormatter`（stdlib 侧）输出同一种形状，级别名归一化到封闭词表
  （`warning`/`WARNING` → `warn`，`exception`/`critical` → `error`）。
  `setup_logging` 加 `force=True`：CLI 在同一进程内重复进入时 `basicConfig` 是 no-op，
  否则级别会静默停在第一次的取值。
- **启动等待改为覆盖型进度行**：120 秒的轮询原本每 5 秒追加一行 `[16s] waiting...`，
  把真正解释本次运行的十几行埋掉了。新增 `StatusLine`：终端下用 `\r` 原地重写，并按上次
  宽度补空格（否则更短的新行会把长行的尾巴留在屏幕上）；**非终端降级为逐行输出**——
  被重定向的日志里塞满 `\r` 既不可读也不可 grep。新增 `clear()`：进度行悬在屏幕中间
  没有换行，此时打真实日志会把两者拼在同一行上，打日志前先擦除。
- **`emulate run` 的成功/超时结论回到日志级别**：原先由进度行承载，管道下会被丢弃，
  留下一堆 `waiting` 却没有结论。现在是 `[info] Web reachable at ... after 79s` /
  `[warn] guest web still unreachable on :8080 after 100s`。

### 新增（测试，2026-10-02）

- `tests/test_log.py`：39 例，覆盖级别归一化、两个渲染器、`setup_logging` 幂等
  （经真实 `logging` 调用而非直调 formatter）、`StatusLine` 的终端/非终端两条路径。
- `tests/test_boot_hooks.py`：20 例，**真的执行** `inject_boot_hooks.sh`（宿主 POSIX
  shell）并检查产出的 inittab/rcS——顺序、幂等、厂商条目保留、`2>&1` 未被 sed 破坏、
  `bash -n` 语法校验。
- `tests/test_guest_net_fix_lock.py`：7 例，source 真实脚本后直接驱动 `acquire_lock`：
  首个抢占、第二个退让、死 pid 残留锁回收、无 pid 文件、锁目录建不出来。
- `tests/test_baked_image.py`：15 例，覆盖指纹随脚本/Dockerfile/新增脚本变化、复用与重建
  的决策、旧 tag 清理只删非当前 tag。测试由写文件触发真实 bug：`IRIS-RC-MARK` 守卫被检查
  却从未写入，导致重复构建会把已追踪的行再包一层。

## [0.3.2] - 2026-10-02

### 修复（rootfs tarball 缓存永不失效，2026-10-02）

- **重新打包的判据从"文件在不在"改成"树有没有变新"**：`emulate_firmware` 原本用
  `if not tarball_path.exists()` 决定是否重新打包，而 tarball 落在
  `scratch/emulate-<iid>/<iid>.tar.gz`、`iid` 由 rootfs **路径** md5 稳定推导——同一次
  仿真在任何一次会话里都会命中同一个缓存。而 `iris emulate run <firmware.bin>` 每次都
  会重新提取 rootfs 并重新应用 L3 规则（规则会就地改写文件），于是**刚刚生效的修复被
  静默丢弃**：guest 跑的是几个月前的 rootfs，`diag` 从来没被禁用过，唯一症状就是规则本
  该消灭的那个崩溃循环。
  实测证据：某固件的 `emulate-5255/5255.tar.gz` mtime 停在 9-27，而同一次运行的 rootfs
  规则脚本是 10-2 生成的；tar 内 30400 个条目没有 `firmadyne/iris_rules.sh`，而宿主
  rootfs 里它刚被写好。
- **`_tarball_is_stale` / `_newest_mtime`**：以 mtime 比较代替对两千个文件做哈希，整棵
  树的判定实测 0.4s；树里任何一个文件比 tarball 新就重打包，而规则改过的文件必然带着
  比它更晚的时间戳。无法 `stat` 的条目按"不可信"处理（`os.walk` 会静默跳过它们），pack
  本身 `stat` 不了就当作陈旧——年龄不可知就无法证明它是最新的。
- **复用与重建都进日志**：命中缓存时打印 `Reusing up-to-date tarball ...`，不再静默。

## [0.3.1] - 2026-10-02

### 修复（Windows 宿主上不可解析的固件符号链接，2026-10-02）

- **`iris emulate run` 不再被 `OSError: [WinError 1920]` 打断**：固件 rootfs 天生
  是 POSIX 的（`/sbin -> /bin`、`/tmp -> /var/tmp`、`bin/ash -> busybox`），在 Windows
  宿主上重建后变成目标不可解析的 reparse point，任何 `stat()` 都**抛异常而不是回答
  False**——`WinError 1920`/errno 22 不在 `Path.exists()` 吞掉的 errno 白名单里。
  于是一条死链就能让一次本可正常完成的仿真半途而废（实测卡在
  `sbin/reboot.iris-disabled` 的规则校验上）。
- **新增 `src/iris/fsutil.py`：查询而不是抛异常**。`safe_exists`/`safe_is_file`/
  `safe_is_dir`/`safe_stat_size`/`safe_read_text` 把"看不了"与"不在"归为同一个答案，
  这正是规则判定需要的语义。`safe_present` 额外用父目录列举兜底：`exists()` 对仍占着
  名字的死 reparse point 返回 False，只看它会误判"目录已清干净"。
- **规则引擎改用 `fsutil`**：`path_exists` 检测、`check_files` 后置校验、`file_glob`/
  `file_regex` 作用域遍历全部走加固后的查询，`is_symlink()` 也一并包了保护。
- **符号链接重锚（`_link_target_within`）**：jefferson 提取树的复制不再原样照抄 Linux
  绝对链接，而是重写成树内可达的相对路径（`/sbin -> /bin` 变成 `sbin -> bin`，
  子目录里的 `usr/sbin/httpd -> /bin/httpd` 变成 `../../bin/httpd`）——chroot 内指向
  同一处，宿主上则再也逃不出 rootfs。相对链接同样做归一化并检查 `..` 越界；目标不在树内
  （含盘符型 `C:\bin` 这类一律按逃逸处理）时不建链接——悬空链接比没有链接更糟，这也保持了
  原有"跳过悬空链接"的行为。目录标志按源树的实际类型取值：Windows 把目录标志存在
  reparse point 里，标志错了会让 `bin/ash` 这类文件链接无法按文件读取。
- **提取容错**：`_copy_tree_tolerant` 的循环体整段包 `try/except OSError`，单条不可
  访问条目不再中止整次提取；`_tree_has_content` 改用 `scandir` 显式遍历（`rglob` 会
  静默吞掉落单的链接，导致"只有链接"的树被误当成空树而跳过重新提取）。
- **`shutil.rmtree` 残留风险收口**：`rootfs_extract`/`tenda` 在清缓存时改用
  `safe_rmtree`，删不干净就**报错**而不是留残目录——残目录会让后续每一次提取都失败。
  `rmtree` 的错误回调在 3.11 是 `onerror`、3.12+ 改名 `onexc`（3.14 删除前者），
  按解释器版本选择，不再在容错路径上抛 `TypeError`。
- **其余击穿点一并加固**：`orchestrator` 读取 guest 规则脚本（原先外层只捕
  `RuntimeError`，会裸栈穿透到 CLI）、`auto.prepare_from_rootfs`、
  `cli` 的 `emulate run` 入口与 `emulate status` 的体积统计、`api` 的
  `list_firmware`/`emulate`/`pipeline`（`extract_rootfs` 的 `RuntimeError`/`OSError`
  现在转成正常的失败响应，不再是裸 500）。

## [0.3.0] - 2026-10-02

### 新增（值守观测与自愈闭环，2026-10-02）

- **串口日志增量感知**：`SerialLogAnalyzer.load_log(start_line)` 支持从指定行起读，
  `AIHealthMonitor` 以 `_log_cursor` 记录已消费行数，每轮 `analyze_health()` 只统计
  上轮之后**新增**的日志行；累计值保存在 `monitor.cumulative`。历史告警不再永远
  钉住状态——触发过 watchdog、被修复后安静下来的 guest 正确回落，而非被全量
  重算的计数器按住 critical 不放。日志被截断/重建（新一轮 QEMU）时游标自动归零。
  随此修掉一个真缺陷：`_ensure_loaded` 把"空窗口"误判为"未加载"而从头重载全量
  日志，增量语义被整体击穿——改为显式 `_loaded` 标记。
- **HTTP 探活作为第二信号源**：`--probe-port <port>` 开启后每轮对转发端口发
  curl（复用 orchestrator 的判定规则：HTTP 000 视为不服务），探活失败会覆盖串口
  日志的乐观结论（"already running"描述的是启动时刻，不是此刻）。`probe_port=0`
  保持纯串口语义，行为与旧版一致。
- **`WEB_SERVER_RESTART` 动作（容器级重启闭环）**：Web 启动后意外退出
  （`started_but_stopped`，即"Web 意外退出"）触发 `docker restart` 整容器重启——
  这是运行期唯一真正触达 guest 的修复通道（guest rootfs 在 `image.raw` 里，
  容器内没有挂载，`docker exec` 到不了 guest 进程）。重启后必须在
  `--restart-verify`（默认 120s）内探活成功才算修复；两次重启间有
  600s 冷却，救不活的 guest 不会被无间隔反复重启。
- **动作账本（guardian action ledger）**：`src/iris/monitor/ledger.py`，所有恢复
  动作与诊断追加落 `iris-home/scratch/guardian_ledger.sqlite3`。字段对齐
  `db.models.RepairAction`（source/rule_id/evidence/applied/promoted），为 P3
  "修复沉淀回 L3 规则"预留 `mark_promoted`。账本写入失败只告警，绝不阻断值守。
- **`iris emulate guardian-log`**：查询值守账本（`--iid` 过滤、`--limit` 截断，
  双入口一致）。`guardian-start` 新增 `--probe-port`/`--restart-verify` 两个选项。
- **`tests/test_guardian.py` 扩至 62 例**：新增 `TestIncrementalCounting`（6 例）、
  `TestHttpProbe`（5 例）、`TestWebServerRestart`（7 例）、`TestLedger`（6 例），
  覆盖增量窗口、游标重置、探活覆盖、重启验证失败不记账、冷却否决、账本失败不阻断。

### 变更（值守观测与自愈闭环）

- `WEB_SERVER_DIAGNOSIS` 诊断脚本重写：旧脚本探 `/etc/init.d` 与 `/opt/goahead`——
  这些路径在运行期容器里**本来就不存在**（guest 文件系统在镜像里），结论必然误导；
  新脚本探容器侧真实可见的东西（QEMU 进程是否存活、容器内 :80 是否有人听），
  并在外部探活可用时附加探活结论。
- `started_but_stopped` 从"仅记录"升级为 `degraded` 异常项并纳入动作推荐链
  （`recommend_recovery_action` 新增末位 `WEB_SERVER_RESTART`）。
- `docx/AI值守与稳定性治理.md` 同步：4.3 状态机表（expired 优先 + 增量口径）、
  4.4 五类动作、4.8 新增"执行通道的两层语义"、4.9 动作账本、4.7 已知限制移除
  已过时两条、5.3 增强清单勾掉已落地三项。
- `tests/test_fsutil.py` 新增 29 例（查询不抛、`safe_present` 的可见残留、`safe_rmtree`
  失败上报、链接重锚七种形态含相对链接的目录标志与越界检查、提取容错、
  `_tree_has_content` 的链接/空/不可读场景）；
  `tests/test_rules.py` 新增 `TestUnresolvableSymlinks` 5 例（检测/校验/编辑作用域在死链
  下不抛，且对"只有死链的 rootfs"跑完全部内置规则不炸）；`tests/test_api.py` 新增
  `TestUnresolvableRootfsEntries` 3 例（列表接口跳过死条目、pipeline 把 1920 转成
  失败响应而非 500、emulate 对死链返回 404）。测试总数 226 → 263。
- `tests/test_tarball_cache.py` 新增 12 例：mtime 遍历（空树/最新文件/不可 stat 条目）、
  陈旧判定（pack 缺失、rootfs 缺失、树未变、规则改写后、重新提取后、pack 时间戳更新后、
  pack 无法 stat）、以及两条真正走 `emulate_firmware` 的用例（陈旧时确实重打包、树未变时
  绝不重打包）。测试总数 263 → 275。

## [0.2.1] - 2026-10-01

### 修复

- **AI 值守的 `expired` 被 stale `critical` 永久掩盖**：watchdog/reboot 计数每轮从
  全量日志重算，属"历史"而非"当前状态"——一个曾触发 watchdog、随后被修复、
  静默跑到监控窗口结束的容器，会永远报 critical 而非"窗口已过仍未健康"。
  超时判定改为**最先**评估并压过一切计数器；新增测试驱动真实
  `analyze_health()` 路径（uptime 由 start_time 重算）——直接调
  `_determine_overall_state()` 会跳过重算，测不出此缺陷。
- **ELF 普查对截断文件崩溃**：`_census_elfs` 只捕获 `OSError`，而通过 4 字节
  魔数校验、却在头部中段截断的厂商文件会让 `struct.unpack` 抛
  `struct.error`，一路穿透 `prepare_from_rootfs` 使仿真前置直接失败。
  普查是抽样，单个坏文件不应击穿整树——补 `struct.error`/`IndexError` 并
  记录豁免理由。
- **配置死项清理**：`Settings.timeout_initial` / `timeout_check` / `check_timeout`
  （代码零读取，仿真超时实际由 `--timeout` 参数与 API 请求体逐次指定，
  文档 `docs/04-快速部署.md` 的 env 表同步修正）、`Settings.home` /
  `Settings.images_dir` property（零引用）。
- **全仓空白卫生**：约 40 个文件补缺失的文件末尾换行、清理行尾空白
  （`cli.py` 约 30 行、`pre_init.sh` 等 shell 脚本、`pyproject.toml`、
  `CHANGELOG.md`、`.gitignore` 等）；空的 `__init__.py` 是包标记，不算缺陷。
- **`iris.py` 被当作包导入时必须代理，否则 `python -m iris.cli` 直接失效**：`-m` 会把
  当前工作目录放进 `sys.path[0]`，runpy 解析 `iris.cli` 前先导入父包 `iris`，抢在
  `src/iris` 之前命中调试脚本，报 `No module named 'iris.cli'; 'iris' is not a package`。
  `iris.py` 现按 `__name__` 分两种身份：`__main__` 时转发到 `main()`；`iris` 时把
  `__path__` 指向 `src/iris` 并执行真包 `__init__.py`，使 `iris.cli` / `iris.api`
  等子模块照常解析。
- **pytest 默认 prepend 导入模式会把仓库根目录插到 `sys.path[0]`**：同样让
  `from iris.api.server import app` 命中 `iris.py`，`tests/test_api.py` 收集期报错。
  测试侧改用 `--import-mode=importlib` 并显式声明 `pythonpath = ["src"]`，不再做
  路径注入——顺带让测试进程里的 `iris` 就是安装后的真包，而非代理模块。

### 变更

- **`.merkle-snapshot.json` 为过期工具缓存**：内容仍引用已删除的根目录
  `test_guardian.py`/`test_manual_usage.py`，属智能体工具生成的陈旧快照，
  已在 `.gitignore`，无需处理。
- **`iris.py` 无条件把 `src` 移到 `sys.path` 首位**：editable 安装（`pip install -e .`）
  已经把 `src` 加进 `sys.path`，原先"不在才插入"的写法会跳过，根目录仍排在 `src` 之前，
  `python iris.py` 直接启动失败。改为先 remove 再 insert。

### 新增

- **根目录调试入口 `iris.py`**：与 `iris` 命令、`python -m iris.cli` 完全等价，但作为
  真实文件存在，IDE 的"调试当前文件"可直接打上断点跑通整条 CLI 链路，无需手工配置
  module 与工作目录。同时导出 `app`（Typer 应用对象）与 `main`，便于在调试器里直接构造
  `CliRunner` 用例。
- **`tests/test_debug_entrypoint.py`**（7 例）：把调试入口的两条保证钉成回归——
  脚本形态转发到 CLI 且输出与 `python -m iris.cli` 逐字一致；包形态下 `iris.cli`
  仍解析到 `src/iris/cli.py`、`__version__` 来自真包 `__init__.py`。
- **`tests/test_guest_script_hygiene.py`**（3 例）：区分"宿主渲染的反斜杠泄漏"与
  "规则作者写的合法 POSIX 转义"——续接符、`find` 的 `\(` `\)` `\;` 属脚本语义；
  合成规则场景仍由 `TestGuestScriptIsPosix` 钉死零反斜杠，真机规则场景改为
  白名单正则逐行校验，并断言验证日志路径以 POSIX 字面量出现。

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
