# 更新日志

本文件记录 IRIS（IoT Rehosting & Interconnection Simulator，鸢尾）的版本变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.3.14] - 2026-10-04

浏览器工作台。动机是评审路径太长：看仿真状态要查 SQLite、看串口要 `docker exec`、
看链路要读 `failure_profile` 的 JSON、看资源要 `docker stats`。本版把四者搬到一屏，
并且**不新增第二条数据口径**——所有数字都来自既有表与既有函数。

### 新增

- **`iris web` 子命令**（`src/iris/cli.py`）。一条命令起工作台并打开浏览器：
  `--host` / `--port` / `--api-token` / `--no-browser` / `--reload`。不接受 `--config`
  （配置沿用 `.env` + `IRIS_` 前缀，多一个入口就多一处不一致）。非 loopback 绑定且
  无 token 时拒绝启动（exit 2），与 `serve start` 同一道门禁。
- **前端 `web/`**（React 18 + TypeScript + Vite + Tailwind + Zustand + TanStack Query
  + Radix + xterm.js）。路由 `/`、`/instances`、`/instances/:id`（链路/串口日志/
  运行记录/操作四 tab）、`/instances/:id/terminal`、`/settings`。
  字体自托管（`@fontsource/*`），无外网 CDN。
- **交互式串口通道**（`src/iris/api/serial_bridge.py`、`web_terminal.py`）。
  `run_qemu.sh` 改用 `-chardev socket,...,logfile=` + `-serial chardev:iris_serial`
  （`IRIS_SERIAL_PORT` 必填，1024–65535 越界即拒），编排器在回环地址上发布串口并把
  `iid → 端口` 落库；WebSocket 桥做扇出、输入权仲裁与背压。guest 字节走二进制帧，
  控制帧走 JSON 文本帧（服务端按首字节判别）。
- **容器资源采样**（`src/iris/emulate/container_stats.py`）：CPU%、内存、内存上限、
  串口端口与通道可用性，2 秒 TTL 缓存避免多个面板各起一次 `docker stats`。
- **工作台 API**（`src/iris/api/web_app.py`、`web_data.py`）：`/api/v1/stats`、
  `/stats/eval-set`、`/knowledge/root-cause`、`/config`、`/console/{iid}`、
  `/instances/{iid}/stats`、`/runs/export.csv`、`/capabilities`，以及 SPA 静态服务
  （含深层路由回退与路径穿越防护）。
- **`GET /api/v1/runs/{id}` 增加 `link` 字段**：由落库的探测摘要还原四层状态与首个
  断点，并带 `note` 声明未落库的部分（见下「诚实边界」）。
- **外观体系：12 套主题 + 密度/字体/动效三开关**（`web/src/tokens.css`、
  `web/src/store/appearance.ts`）。一套 CSS 变量、十二个 `:root[data-theme]`
  覆盖块——`:root` 自身就是默认的 `iris` 主题，所以新增主题只改一处，
  不可能引入组件读不到的变量。共 12 套：10 套深色（iris / midnight / nord /
  tokyo-night / dracula / gruvbox / monokai，以及 aurora / sunset / ocean 三套渐变
  背景）与 2 套浅色（daylight / github-light）。浅色主题的语义色与文字色按浅底
  重新调过，不是把深色值反转。
  - **密度**（紧凑/舒适/宽松）缩放 `html` 的字号。Tailwind 的字号与间距刻度是 rem，
    所以一个声明即可整体生效；面板宽度、终端 80×24 这类 px 值刻意不动。
  - **字体**（系统无衬线/等宽/衬线）与**动效开关**（关闭后停用文档内全部动画）。
    系统 `prefers-reduced-motion` 始终优先于界面开关。
  - 设置页新增「外观」面板（主题网格带三色预览），命令面板新增「切换主题」
    「切换界面密度」两条命令。
  - 首屏防白闪：`index.html` 内联脚本在样式加载前套用已保存的外观，
    与 store 共用同一组 `localStorage` 键与同一套白名单。
  - xterm 调色板改为从 CSS 变量解析并随主题重绘（此前硬编码 iris 深色，
    浅色主题下会是一块浅底浅字的方块）；光环动画取 `currentColor`，
    脉冲状态点不再一律是绿色环。

### 修复

- **`/api/v1/runs/export.csv` 422**：路由声明顺序错误，`{run_id}` 把 `export.csv`
  当成 run id 匹配掉了。改为 `/api/v1/runs/export.csv` 并**声明在 `{run_id}` 之前**。
- **工作台路由漏挂鉴权依赖**：`/capabilities`、`/runs`、`/console/{iid}`、`/config`
  等新增路由未声明 `Caller`，任何人都能读。全部改为强制鉴权依赖（不需要的命名为
  `_caller: Caller`）。
- **评测集口径与统计卡不一致**：分子原先取自 `runs_page(limit=500)`，超过 500 条后
  分子冻结而分母继续增长。改用 `run_stats` 同源统计，并新增 `scope`、`web_reach_rate`、
  `by_arch`、`items_note` 字段把口径写在返回值里。
- **SPA 静态目录在 install 时被固定**：`dist_dir()` 原在注册路由时求值，`npm run build`
  发生在服务启动之后时就永远 503。改为每请求解析。
- **`dist_dir()` 只认源码树布局**：`parents[3]/web/dist` 在安装后的 wheel 里不存在，
  注释却写着「安装后也在同一相对位置」。改为按候选顺序探测（包内 `iris/web/dist`
  优先于仓库根 `web/dist`），支持 `IRIS_WEB_DIST` 覆盖；`pyproject.toml` 增加
  package-data，`npm run build:pkg` 把产物暂存进包内（wheel 实测携带 66 项）。
- **打包里出现空 chunk**：`manualChunks` 配了 `echarts` 与 `@xterm/addon-web-links`，
  但源码从未 import 两者（架构图用的是 CSS 宽度条）。删除该依赖与该配置。
- **前端指向 `/docs` 的链接走客户端路由**：命中 router 的 catch-all 后回到仪表盘。
  改为普通 `<a href="/docs">`（后端 FastAPI 的 OpenAPI 页真实存在，200）。

### 诚实边界

- **终端尺寸固定 80×24**，页面的 resize 请求会收到 `applied: false` 的说明而不是被
  悄悄忽略：QEMU 串口没有窗口尺寸通道。
- **输入权需显式 claim**：hello 帧里的 holder 是对端主机名，浏览器标签页无法区分，
  所以「谁能打字」是仲裁结果。
- **四层链路覆盖率有限**：只有四层中出现阻断、且探测跑完的运行才落库三键摘要。
  当前库 81 条记录中 4 条可还原。全通的运行不探测，探测不可用的运行不记证据；
  每层原始探测文案与「探测不可用」原因从未落库，因此这些字段恒为空，`note` 字段
  在页面上逐条说明。
- **`ai-guardian` 能力声明为 `unavailable`**：它是确定性自愈器，进程内不含任何模型
  调用。徽章文案与证据由测试钉住。

### 兼容

- 数据库 schema 未变更（`run_detail` 只是多读一次已有的 `detail` JSON）。
- `run_qemu.sh` 的串口参数需要 `IRIS_SERIAL_PORT`；编排器已同步提供，直接跑脚本的
  人需自行导出该变量，否则脚本拒绝启动而不是静默丢日志。
- 环境变量新增 `IRIS_WEB_DIST`（前端产物位置覆盖），其余沿用既有 `IRIS_` 前缀。

## [0.3.13] - 2026-10-03

产品化改造，优先级 P0–P3。核心动机是实测记录已经在**两次**推翻自己的结论后没人
同步（`docs/eval-log.md` 说 DIR-868L 不可达，旁边的失败表却已记 HTTP 200），而
README 还写着「服务起、VLAN 路由不通」。本版让声明可被核对、让判定可被重算。

### 安全

- **API 默认只监听本机**（`serve start` 的 host 默认 `0.0.0.0` → `127.0.0.1`），
  且非 loopback 且未配置 token 时**拒绝启动**（exit 2）而非静默暴露。README 此前
  写「docs: http://127.0.0.1:9000/docs」，把一个全网可达的服务描述成本机服务。
- **token 鉴权**（新增 `iris/api/auth.py`）。`IRIS_API_TOKEN` 或 `--api-token` 配置，
  接受 `Authorization: Bearer` 与 `X-IRIS-Token`，`hmac.compare_digest` 恒定时间比较；
  调用方标识取 token 的 sha256 前 16 hex，**不存明文**。health 免鉴权（供探活）。
  同一 token 的调用方之间按 iid 做归属校验，越权与不存在**同为 404**，不泄露 id 存在性。
- **上传大小上限**（`IRIS_API_MAX_UPLOAD_MB`，默认 64）。分块读取并在超限时返回 413，
  此前 `firmware.read()` 无上限。

### 状态可恢复

- **活跃仿真落库**（新增 `ActiveEmulation` 表与 `iris/db/active.py`）。此前
  `_active_emulations` 是进程内 dict，`serve restart` 后所有在跑的仿真从列表里消失、
  且无法停止。历史（`emulation_run`）与活跃分表：混用会让重启丢仿真、并污染 `db stats` 口径。
- **`container_alive` 改为三态**（`bool | None`）。探测异常时旧实现返回 `False`，
  而 `reconcile` 把 `False` 当「容器已死」→ docker 一次抖动就会误删存活容器的记录，
  让调用方失去停止能力。现在 `None`（未知）保留行，仅 `is False` 时删除。

### 声明与实现对齐

- README 定位速览去掉「LLM 双轨」，新增**能力边界**小节（LLM 未接入、不含模型调用、
  拓扑单平面无无线、x86 不在范围）与 **API 交付面**小节（默认绑本机、token、归属隔离、
  上传上限、重启可见）。量化目标拆为「当前实测 3/5」与「目标 ≥80%（尚未达成）」。
- 实测表按 0.3.12 同批刷新（62.2 / 49.9 / 53.0 / 312.8 / 246.0s），并标注 WRT1200AC 与
  R7800 为**环境适配失败（宿主内核 `validate_nla` BUG，项目内不可修）**。
- API `version` 不再硬编码 `0.1.0`，改为跟随包版本；新增守卫禁止 `src/` 出现三段式
  版本字面量。
- 新增 `tests/test_docs_claims.py`（15 项）：未接入的 LLM 不得被声称接入、README 每个
  耗时数字必须能在 `docs/eval-log.md` 找到（双向溯源）、非 loopback 的 serve 示例必须
  同时设置 token。

### 评测基线

- 新增 `iris corpus eval` 与 `iris/corpus/baseline.py`。分母 = **声明了 `expect_web`
  且被实测**的条目；未声明或未实测一律 `skipped`，不进任何分母。分母为 0 时明确告警
  「这不是 0% 成功率」。
- **三种口径分开报**：`web_rate`（含环境失败，用户依然没拿到设备）、`capability_rate`
  （剔除环境失败，只衡量宿主健康时的能力）、`regressions_against`（逐设备点名，
  因为 `3/5 → 4/5` 可以藏着一修一坏）。
- `FirmwareEntry` 新增 `expect_web` / `timeout_sec` / `local_file` / `expectation_note` /
  `db_match`。`db_match` 显式声明与 `image` 表 filename 的对应关系：清单键
  （`dlink-dir868l-revb`）与存储文件名（`DIR868L_B1_FW205WWb02.bin`）不共享任何命名方案，
  模糊匹配把一台设备的结果错接到另一台上比没有数字更糟。
- 报告 JSON 遇到未知 verdict **抛错而非降级**：旧版本读新版本的基线时若静默降级，
  每一台设备都会显示成未实测，等于凭空造出一个通过。
- `m0-baseline.toml` 全部 10 个条目补齐排除理由与 `db_match`；两条环境失败按实测标注
  `env-broken`。实测：`web_rate=50%` / `capability_rate=100%`（该口径下 IRIS 未失分）。

### 链路分层主动探测

- 新增 `iris.emulate.linkprobe`：**实测** route / ARP / ICMP / service 四层，产出链路
  分层表，并直接命名断点所在层。0.3.12 那次「路由正确、ARP `REACHABLE`、ping 无应答、
  curl `000`」的手工排查从此固化为代码——它当时被读成「固件有问题」，实际是宿主桥地址
  落在 guest 子网之外，而路由器按设计静默丢弃这类报文。
- 新增 4 个 `FailureKind`（`Stage.NETWORK`）：`link-no-route` / `link-no-arp` /
  `link-no-icmp` / `link-no-service`。hint 直接写明实测根因，例如 `link-no-icmp`
  指向「源地址不在 guest 自己的子网内」。
- **三态而非两态**：每层是 `ok` / `blocked` / `unknown`。探针跑不起来（容器无 `ping`、
  docker 不可用）与「报文真的死了」是两件事，混同会凭空造出网络故障。实测镜像内
  **`nc` 与 `arping` 不存在、`ping` 存在**，故 L2 用 `ip neigh`、L4 用 `curl`。
- **ARP 在 ICMP 之后读**：邻居表是「发包」的副产物，先读 ARP 会在健康链路上得到空表，
  从而把 L4 的问题错记到 L2 头上。`INCOMPLETE` / 全零 MAC / `FAILED` 三种实测形态
  都判为 `blocked`，空表判为 `unknown`。
- **curl 退出码只记录不解读**：实测对不可达地址返回 7 或 28、对可达但端口关闭也返回 7，
  同一码覆盖两种完全不同的原因，因此由上层的层结论来判定。
- 任何 HTTP 码（**含 403 / 404**）都算「有东西应答了」——guest 在猜错的端口上跑着登录页
  仍是可用设备，把它记成不可达正是「端口猜错」变成「固件坏了」的路径。
- 失败诊断顺序：实测结论在前、日志推断在后，且 verdict 的 detail 会显式写出
  **哪一层没能测到**（无邻居表条目时无法区分「没有这个地址」与「ARP 无人应答」）。
- `emulation_run.ping_reachable` / `ip` 两列此前存在于 schema 却无任何写入路径，现由
  分层探测填充；两列均可空，因为「没测过」与「测了没有」必须能区分。
- API `EmulateResponse` 新增 `link` 字段（分层表），`state` 用 `LayerState` 枚举而非
  字符串，未知状态直接校验失败而不是原样透传。
- 关键命令与解析器**均在 `iris-emulate:latest` 内实跑校验**，测试夹具是捕获的真实输出
  而非凭记忆编写。

### 失败知识闭环

- **`repair_action` 此前有表无写入路径**：六列齐全、零写入代码，L3 规则每次仿真都在
  触发却没有任何一行记录，于是「施加这条规则是否改变了结果」连人都无法回答。
  现由 `emulate_firmware(applied_rule_ids=...)` 在**该 run 那一行**上落账
  （`source="rule"`，`emulate run` 由 `prepared.matched_rule_ids` 透传）。
  只记真正命中的规则，并去重、剔除空白 id：被提出但没被采纳的修复不是动作。
  API 的两个仿真端点不跑 L3 规则（它们接受已提取的 rootfs 直接开仿），账本为空是实况。
- **新增 `iris db cards`**（`iris/db/knowledge.py`）：把 `failure_profile` 按 kind
  聚成根因卡片——多少次 run、哪些镜像、哪些架构、首次到最后一次、这些 run 上触发过
  哪些规则。`--promote-only [--recent N]` 只留**最近 N 次失败 run**里仍在出现的 kind，
  即"还需要写确定性规则的清单"；不带参数时另外打印已退出窗口的 kind，这是修复是否
  生效的第一个可见信号。实测 73 次历史 run：N=10 留 3 类，N=3 只留 `web-wrong-port`。
- **信息类被排除**（复用 `db.runs` 的同一份封闭集合）。`network-fallback-ok` 的含义
  是"注入的网络兜底**按设计生效了**"；按根因排序它会以 36 次排第一并把自己的意思
  反过来。空 signal 跳过，NULL `started_at` 的 run 计数但不进时间线，
  `web_reachable` 为 NULL（从未探测）不计为恢复。
- **门禁是"最近还在发生"而非"是否已恢复"**。本语料没有任何成功 run 带过失败行，
  `recovered` 对每个 kind 都答"否"，拿它当门禁等于把所有 kind 都排上。窗口按**失败
  run 条数**而非天数或 kind 数计：本语料的 run 间隔在分钟到小时级，天数窗口会把两天
  的历史整体判成"活"或"死"；而 kind 数窗口会在同一分钟多个 kind 共用 last-seen 时失真
  （语料里 `no-guest-ip` 与 `no-network-driver` 的 last_seen 完全相同）。
- **刻意不自动化**：不因历史行自动施加修复。判定规则有效的唯一证据是活体运行上的
  `Rule.post_action_verify`，被记住的成功不是它。输出是排序清单，规则仍由人写。
- 已知缺口（如实记录，不掩饰）：`RuleReport.touched_files` 不落库——`prepare_from_firmware`
  只带出命中的 rule id，报告本身不外传，因此回答不了"这次修补动了几处"；
  卡片上的 `recovered` 恒为 0，那是数据的实况而非结论。
- 新增 `tests/test_failure_knowledge.py`（35 项）与 `test_run_recording.py` 的
  `TestTheRepairLedgerIsWrittenOnTheRun`（2 项，走 orchestrator 而非直接调
  `record_repairs`——缺的那一环从来不是账本函数，而是没人把 rule id 交给它）。
  10 项变异验证全部由测试捕获。

### 运行期写入不再丢弃 + 进 guest 的通道

- **`run_qemu.sh` 改用持久状态盘**。此前每次启动都把 `image.raw` 复制一份到
  `/tmp/qemu-<iid>.raw` 交给 QEMU、退出时删除，于是 guest 的一切写入只落在临时副本上，
  随 QEMU 退出一起消失——重启（**包括值守的 `WEB_SERVER_RESTART`**）等于从出厂镜像
  重开。现在是 `state.raw`：不存在才复制、退出不删，`image.raw` 保持出厂状态。
  两个后果都是想要的：被强杀弄脏的状态盘可以删掉回到出厂镜像而不必重烤，
  重烤也永远不会覆盖已注入的修补。
- **`make_image.sh` 重烤后 `rm -f state.raw`**。`run_qemu.sh` 只在缺失时创建状态盘，
  留着旧的就会用新镜像配旧状态盘启动。
- **QEMU 退出后 `e2fsck -p`**。值守的 `docker restart -t 10` 是 SIGKILL，ext2 没有日志
  可回放，下一次挂载会失败——看起来就像固件坏了。用 `|| echo` 保证它不会带走后面的
  TAP 清理（`set -e` 下非零 e2fsck 会跳过清理，下一次启动就建不出桥）。
- **新增 `iris guest ls/get/put/reset`**（`src/iris/emulate/guestfs.py`）：在特权仿真
  容器内对 guest 镜像做 loop 挂载。这是**第一条能直接看 guest 里有什么的通道**，此前
  所有关于 guest 的判断都来自串口日志推断。读操作一律 `ro` 挂载；`state.raw` 在 QEMU
  运行时拒绝访问而不是尝试（把运行中 guest 打开读写着的文件系统再挂一次会损坏它，
  损坏会很久以后才以"无法解释的启动失败"出现）；不做任意命令执行。
- **Git Bash 的路径重写会被还原**。`iris guest ls 1 /etc/passwd` 在 Windows 上到达
  Python 时是 `C:/Program Files/Git/etc/passwd`，不加处理则**每一条文档里的例子都失败**，
  且报错指向 guest 而不是 shell。只有 Git 安装前缀会被还原，`C:/temp/x` 仍被拒绝。
- **实测（2026-10-04，DIR-868L / iid 6630 真实容器，双重证据）**：QEMU 运行中读出厂镜像，
  `guest get /firmadyne/init` 读出 `infer_init` 的结果 `/sbin/init`（此前任何代码都看不到
  这个文件）；运行中读 `state.raw` 被正确拒绝；停 QEMU 后 `guest put` 改写
  `/etc/init.d/iris_net_fix` 并插入一条 `echo`；重启 QEMU（`Reusing existing state disk`）
  后**串口日志出现 `IRIS-REPAIR-PROOF`**——guest 自己执行了注入的代码；补上首启那一版
  桥地址后 `curl` 得 **HTTP 200**。状态盘逻辑本身另在容器内单独验证过：写入的标记跨
  "两次启动"保留，出厂镜像不被污染。
- **新发现，本版已修**：`WEB_SERVER_RESTART` 重跑时不重复首启那次"把宿主桥地址补进 guest
  自己子网"的观测（0.3.12 的 DIR-868L 修复），桥地址退回默认的 `192.168.1.254/16`，
  实测重启后 `curl` 为 `HTTP 000`，手动补 `ip addr add 192.168.0.254/24 dev br6630`
  后立刻 `HTTP 200`。根因是首启检测到的 guest 地址没有落盘（`arch` 落盘了，地址没有）。
  修法见下一节。
- 边界写明而非留给用户发现：通道看到的是**磁盘上的文件**，不是运行中 guest 的视图；
  值守**仍然用不上**这个通道（它要求 QEMU 已停止，而值守动作都发生在 QEMU 运行时），
  所以值守侧 `n=0` 依旧读作 UNKNOWN。
- 新增 `tests/test_guest_fs.py`（43 项）与 `tests/test_run_qemu_flags.py` 的
  `TestTheStateDisk` / `TestTheBakedImageIsRebuiltClean`（7 项）。16 项变异验证全部捕获，
  其中 3 项第一轮漏网（读挂载的 `ro`、mode 的八进制校验、Git Bash 路径还原）是因为
  测试没覆盖到，已补测试后重跑通过。

### 重启复现首启的宿主观测

- **首启认出的 guest 地址落盘**。`orchestrator._record_guest_ip` 在**检出值与假定值不同**
  时把它写进 `/work/scratch/<iid>/guest_ip`——与 `arch` 标记同一目录、同一用途：容器重启
  后没有人在旁边看串口，这是唯一还能说清"guest 在哪"的地方。无条件写会把
  `192.168.1.1` 这个假定盖成"实测"，所以写点必须在检出的那一个分支里。
- **`run_qemu.sh` 按「显式第 4 参 → 标记 → 假定」取址**。于是 `docker restart` 加
  guardian 的三参数重跑会自己复现首启那次"把宿主桥补进 guest 子网"的观测，而不是退回
  `192.168.1.254/16`。取址顺序也让已经在手的人（首启的 orchestrator、手工重启）不被
  一个旧文件指挥。
- **标记里的东西会被 shell 算**：它直接喂给 `awk` 算宿主地址和 `ip addr add`。所以写入端
  校验（四段十进制、非 `127.`，新的 `_is_dotted_quad` 同时替换掉 `_place_host_on_guest_subnet`
  里那份更宽松的 `isdigit` 版本），读取端再校验一次；**读取端校验失败退回假定而不是让启动
  失败**（`set -e` 下 `awk` 的非零退出会直接带走整次启动）。
- **地址来自 guest 自己的 printk**，所以写文件走 `sh -c` 把地址作为**位置参数**传，
  不拼进脚本文本。
- **`make_image.sh` 重烤时连同 `state.raw` 一起删掉标记**：它描述的是被替换掉的那张镜像
  的网络。
- **实测（2026-10-04，DIR-868L / iid 6630，同一容器、同样三参数重跑，带对照）**：
  有标记 → `Guest address taken from /work/scratch/6630/guest_ip: 192.168.0.1`、
  桥为 `192.168.0.254/16`、`HTTP 200`；把标记挪走作对照 → `host=192.168.1.254
  guest=192.168.1.1`、桥为 `192.168.1.254/16`、`HTTP 000`。同块状态盘、同一次启动流程，
  唯一差别是那个文件，所以是因果。首启本身也复测通过（HTTP 200 @62.1s）。
- **边界写明**：标记只在首启真的检出地址时才有；没检出的固件重启后仍走假定路径，与今天
  一致、不算退化。但「标记缺失」与「guest 确实在假定地址上」在容器里长得一样，判不出来，
  不要把前者当成后者的证据。
- 新增 `tests/test_guest_addr_marker.py`（26 项）：写端单测、跨语言契约（路径两侧各自推导
  后比对）、以及**把 `run_qemu.sh` 真正跑起来**的读端行为测试（网络工具替换成 shell 函数
  记录器，只重写 `WORK_DIR` 一个路径）。12 项变异验证全部捕获，含"两侧各自改名"、
  "读端退回默认"、"去掉 `|| true`"、"把假定值当实测写盘"、"写失败仍报成功"。

### 对比语料扩充：4 台厂商固件（vendor buildroot）加入双侧实测

- **新增语料**：Tenda G1V31si（mipsel）、Tenda i27V11br（加密 FIT）、Tenda RP3V30
  （armel 多分区）、Tenda US 版 TES7002（**arm64**）。此前 6 台语料里 5 台是 OpenWrt
  24.10 官方快照，对厂商 buildroot 变体的覆盖不够——本节把这条局限往回推了一格。
- **IRIS 侧 6 台串行实测**（timeout 300s，iid 7001-7006），数据来自 `emulation_run`
  真实落库而非手工快照：`link-no-service` × 2（G1V31si、RP3V30）、`link-no-arp` × 2
  （WRT1200AC、R7800）、**成功 × 1（US 版 TES7002，HTTP 200 @96.8s，`arch=arm64`）**、
  提取失败 × 1（i27V11br，rc=2 / 1s 早退）。这正是 0.3.13 分层判定的价值：旧口径
  只有「HTTP 000」，现在能区分「二层不通」与「二层通但 guest 内无服务」。
- **修正 0.3.12 的两条表述**（`docs/08-与FirmAE对比.md` §3.1(b-1)）：WRT1200AC /
  R7800 的 `HTTP 000` **不代表 web 服务没起来**——串口里 `uhttpd` 三次
  `inet_bind ... port:80/443` 真实发生（t=131.3s / 123.4s），兜底自己也判定
  「vendor web server is already running, leaving :80 to it」。失败位置是
  **地址丢失后的二层不通**（`link-no-arp`），不是服务未启动。根因（重宿主内核
  `validate_nla` BUG，`pc=c01abe18`）不变，变的只是失败位置的精确描述。
- **新增失败分类 `link-no-service` 的含义**：G1V31si / RP3V30 的网络层是健康的
  （IRIS 自己配的 `192.168.1.1` 被内核接受，RP3V30 上兜底 telnetd:7002 甚至完全
  跑通），失败在**固件里根本没有 web 服务**，而兜底的 `no web server fallback
  available` 说明它也补不上。这是语料属性 + 有意的能力边界，**不是缺陷**。
  **兜底范围按实码写清**（`scripts/emulate/iris_net_fix.sh:659-688`）：它是一条
  `if/elif` 链，只认 `/opt/goahead/goahead` + `route.txt` 与 `/usr/bin/boa` +
  `/etc/boa/boa.conf` 两种硬编码组合，**不扫 `/bin`、不找 busybox 的 httpd applet**；
  US 版 TES7002 能成正是因为它是 Tenda 固件、带 `/opt/goahead`。
- **新发现、未修的真实缺陷**（详见 `docs/08` §9）：`_infer_arch()`
  （`src/iris/extract/firmware.py:290-291`）把「squashfs 容器端序 → mipsel/mipseb」
  当成通用兜底，丢掉了「必须先是 MIPS」的前提，于是裸 squashfs 的 **arm64** 固件被
  `extract inspect` 误报为 mipsel；`emulate --arch auto` 因走解压后 ELF census
  （实测 640 个 ELF 条目中 aarch64 × 639）判对并跑出 HTTP 302。**两端不一致**，
  `/pipeline` API 与 `extract add --no-verify` 会消费到错误值，而
  `tests/test_firmware.py:215` 把这个错误行为写成了期望值。本轮只记录不修。
- **如实记录的口径限制**：提取失败**不落 `emulation_run`**（i27v11br 在库里没有对应
  行），因此「跑了几台」不能只查该表——这条也记为待改进项。

### 对比文档更新（`docs/08-与FirmAE对比.md`）

- §2 补上 arm64 的**同语料直接对照**：US 版 TES7002 在 IRIS 侧 HTTP 200 @96.8s，
  FirmAE 侧无法仿真——不再只是「IRIS 历史上跑通过 arm64」。
- §3.1 新增 (c) `link-no-service` 分类（含逐台串口证据表）与 (b-1) 修正。
- §4 改为「提取能力边界」，新增 §4.2 厂商加密 FIT（i27v11br）——**两侧都做不到**，
  是语料属性而非任何一方的短板。
- §9 新增「本轮新发现：**未修**的代码缺陷」，含根因精确到行、5 个消费方的影响面、
  修正方向（证据分级的单一判定函数）与 FirmAE 的对照数据。

## [0.3.12] - 2026-10-03

按 0.3.11 与 FirmAE 的实测对比结论回头修缺陷。**同语料仿真成功率从 2/5 变为 3/5：
DIR-868L 由失败转为 HTTP 200 @53.0s**，且过程中推翻了上一轮自己写下的两条结论。

### 修复

- **宿主桥必须在 guest 自己的子网内**（`orchestrator._place_host_on_guest_subnet`）。
  `run_qemu.sh` 把宿主放在「假定的 guest 地址减一、掩码 /16」，这对路由器不成立：
  DIR-868L 的 LAN 是 `192.168.0.0/24`，而宿主在 `192.168.1.254/16`。
  分层探活实测：路由正确、ARP `REACHABLE`（L2 通）、ping 无应答、curl `000`；
  在同一座桥上 `ip addr add 192.168.0.254/24` 后 ping 与 curl 立刻正常。
  即帧到了、guest 应答了，但源地址不在它的子网内被静默丢弃——路由器本就如此设计。
  内核会自行按目的子网挑选源地址，因此**已经跑着的 socat 转发无需改动**即生效。
  两条工程细节：加地址在起转发**之前**（否则转发存在但打不通）；`ip addr add`
  的 `File exists`（退出码 2）视为成功，真实失败只记 debug 不升级为 boot 失败。
  **已知边界**：掩码固定 /24，因为 IRIS 唯一能读到的地址声明（内核 `inet_insert_ifa`
  printk）不带前缀长度。这是假设不是推导，非 /24 的 guest LAN 不覆盖。
- **两个诊断探针同时误判**（`orchestrator._NIC_PRESENT` / `_HAS_NON_LO_IP`）。
  前者只认行首 `eth0:` 与 `dev eth0`，不认 `device eth0 entered promiscuous mode`、
  `dev:eth0.1`、`8021q: ... device eth0`；后者只认 `inet_insert_ifa: dev X`，
  不认 FirmAE 改写过的 `__inet_insert_ifa[PID: 10045 (ip)]: device:br0`。
  两者**互相印证**，于是 DIR-868L 被写成「没有网卡且没有地址」，而它其实 br0=192.168.0.1、
  httpd 已绑 `:80`。补齐拼写，并加排除规则：同一行写着 `not found` / `no such device`
  时不计入网卡证据。
- **`iris_net_fix_bg.sh` 末尾缺换行**。BusyBox ash 丢弃脚本最后一行，而那正是启动兜底的
  那一行；launcher 在此之前完全静默，症状表现为「boot hooks 没跑」而 hook 安装其实正确。
- **guest 脚本注释里的 shell 元字符会被当代码解析**。实测 `iris_net_fix.sh` 头部注释
  结尾的 `&` 让脚本停在第一条语句之前（逐行探针定位）。已清理两个 guest 脚本注释中的
  反引号、`${`、`$(`、`&`、`<`、`>` 与非 ASCII 字符。
- **残缺 BusyBox 的参数展开**。该固件上 `${var%/*}` 展开为空、`${var:-x}` 返回空，
  `$(...)` 行为异常。默认值写法全部改为 `$VAR` + `[ -n "$VAR" ] || VAR=x`；
  bg launcher 求自身目录改为宿主侧 `sed` 烘焙路径占位符；`acquire_lock` 取父目录
  改用 `sed`。新增守卫禁止这两个家族的展开重新出现（正则含数字变量名）。
- **`head` applet 在该固件上不存在**。新增纯内建的 `first_line()`，替换 4 处 `head`。
- **`/dev/console` 显式打开在该固件上失败**，且打不开的重定向会把命令一起带走。
  `log()` 改为两通道回退：先显式写 console，失败则继承 stdout。
- **baked image 加 `--pull=false`**：拉取失败时降级为本地重建而非整轮失败。
  这是防御性加固，不是某个实测故障的修复。

### 诊断结论更正

- **WRT1200AC / R7800 的根因已定位，且两台同因**：t≈1.4s eth0 拿到 192.168.1.1，
  t≈102–122s `netifd (1056): undefined instruction: pc=c01abe18` +
  `kernel BUG at firmadyne_kernel-v4.1/lib/nlattr.c:41`（`PC is at validate_nla`、
  `LR is at nla_parse`，`e7f001f2` 是 `udf` 陷阱而非真的非法指令）。
  两台的 `pc` 与文件行号完全相同。后果链：netifd 在持有 rtnl 锁时被内核 BUG 打死 →
  之后 `ifconfig eth0 192.168.1.1` 永久阻塞（实测卡满 5s `run_bounded` 上限）→
  guest 无地址。**属重宿主内核自身的缺陷，不在 IRIS 代码内可修。**
  上一版写的「kernel panic」与「netifd 持有 device lock」分别是症状与错误归因。
- **DIR-868L 上兜底脚本原理上跑不起来**，已如实记录为能力边界：该固件的 BusyBox 是
  厂商极简定制版（缺 `head tail sort uniq dirname iptables nc` 等 applet，
  `${var%/*}` 展开为空），**且 ash 不支持 shell 函数定义**——只要脚本里存在任何函数
  定义，其后代码就不执行，而 `iris_net_fix.sh` 通体基于函数。
  但这台固件不需要兜底即可成功（转发直接指向 guest 自己的 httpd），
  因此不为此重写无函数版本。

### 实测（iid 6711–6715，`--timeout 300/240`）

| 固件 | arch | 结果 | 耗时 |
|------|------|------|------|
| Newifi D2 | mipsel | ✅ HTTP 200 | 62.2s |
| Archer C7 v2 | mipseb | ✅ HTTP 200 | 49.9s |
| DIR-868L revB | armel | ✅ HTTP 200（0.3.12 由失败转成功） | 53.0s |
| WRT1200AC | armel | ❌ HTTP 000 | 312.8s |
| R7800 | armel | ❌ HTTP 000 | 246.0s |

### 测试

- 新增 `tests/test_guest_subnet_placement.py`（16 项）与
  `tests/test_boot_diagnosis.py::TestGuestNicAndAddressProbes`（7 项，样本为真实串口日志行）。
- guest 脚本守卫扩到 16 项，新增末尾换行、注释元字符（含非 ASCII 分支）、
  默认值展开三类；新增 `first_line` 与 `log()` 回退的 3 项测试。
- 全量 `pytest 818 passed, 4 skipped`，`ruff` 全绿。
- 本轮新增守卫均做变异验证：注释里放非 ASCII、恢复 `${VAR:-}` 形式，先变红 = 有效。

### 文档

- `docs/08-与FirmAE对比.md`：§3 表、§3.1（两条结论的推翻过程与证据链）、
  §5、§6.2–6.4、§7、§8、§9 全部按实测重写，并新增「本文被修订过两次结论」的抬头声明。
- `docs/eval-log.md`：M1 表按 0.3.12 重跑更新，失败原因更正为内核 BUG 而非 panic。

## [0.3.11] - 2026-10-03

为了把 IRIS 与 FirmAE 放在同一份实测数据上对比，本轮先在 WSL2 里源码构建了 FirmAE
并跑完 5 台同语料固件，再把 IRIS 侧在同一份语料上重跑——因为 `emulation_run` 表里
M0 语料一条记录都没有，`docs/eval-log.md` 那张 IRIS 表是人工快照，与实测不同源。
结果暴露出一个**一直存在、此前无人发现的启动链回归**：IRIS 自己的兜底注入会顶掉
OpenWrt 的整条厂商启动链。

### 修复

- **procd 固件上不再注入 sysinit 条目**。`procd_inittab_run()` 遍历 action 列表，
  命中即 `break`（`sysinit`/`shutdown` handler 没有 `multi` 标记），而 `runrc()`
  要求 `<process> <S|K> <param>` 三段齐全。注入的两段式
  `::sysinit:/etc/init.d/iris_net_fix_bg` 因此成为唯一的 sysinit action，
  `runrc` 报错 return——**IRIS 自己的兜底没跑，厂商的 rcS 也因 `break` 永远不执行**。
  症状是 guest 干净启动、无 netifd 无 uhttpd，串口只有一行
  `procd: valid format is rcS <S|K> <param>`。
  `inject_boot_hooks.sh` 现在按 procd 自己的要求读回 inittab（`sysinit`/`shutdown`
  行是否带 `<S|K> <param>` 尾参）判定 init 家族；是 procd 就改挂 `/etc/rc.d/S99iris_net_fix`
  ——那是 procd 唯一会走的通道，它没有 rcS 文件可追加。
- **`ln -s` 失败不再中断整个注入**。此前 `set -e` 下链接建不出来就让脚本非 0 退出，
  后面的 rcS tracing 与 tail hook 全部不执行。现在只打印明确警告（宿主不能建符号链接
  → guest 将拿不到兜底），既不伪装成功也不吞掉后续步骤。
- **陈旧注入行会被清理**。同一份 rootfs 若在「被当作 BusyBox」期间被注入过，
  识别为 procd 后必须把那两行撤掉，否则一条永远跑不了的死条目留在 inittab 里。

### 验证

- `tests/test_boot_hooks.py::TestProcdInittab` 新增 10 项，全部跑真实脚本
  （合成 `/etc`，读回 inittab）。
- 5 项变异验证如期变红：`seen>=4`（8 红）、只认 sysinit 不认 shutdown（1 红）、
  去掉 action 过滤（8 红）、链接目标改成 bg 版（2 红）、去掉陈旧条目清理（1 红）。
- 真机回归：Newifi D2 `emulation success`，`HTTP 200 after 75s`，串口出现
  `IRIS-NETFIX: final: eth0 up with IP`——兜底经 rc.d 通道生效。baked image 指纹
  `9358dc207ef7` → `b2112090961b`，旧 tag 由构建流程自动清理。

### 文档

- 新增 `docs/08-与FirmAE对比.md`：两侧环境、架构支持矩阵、逐台对比、
  失败分类、反向结论（FirmAE 在 DIR-868L 赢、IRIS 在 Newifi D2 与两台 UBI 提取上赢）、
  耗时不可比的说明、全部偏离声明。
- `docs/eval-log.md` 的 FirmAE baseline 表由整表 `_待填_` 回填为实测值，
  并标注前两列是 IRIS 侧取值、后几列是 FirmAE 侧取值。

### 已知缺口（本轮只记录，未修）

- 无 `/etc/inittab` 的固件（如 DIR-868L）上兜底退化为单一通道，而那唯一通道被放在
  rcS 末尾、被 `/etc/init0.d/rcS` 饿死——`IRIS-NETFIX` 0 行。这违反了本项目自己
  早已写下的「兜底不得以厂商 rcS 完成为门控」。
- `iris_net_fix.sh` 的 `has_ip()` 硬编码 `grep "inet addr"`，busybox ≥1.20 的
  `ifconfig` 输出是 `inet 192.168.1.1`（无 `addr`），在 Alpine 与 OpenWrt 上都误判。
- WRT1200AC / R7800 在 IRIS 侧仿真的失败根因未完全定位，详见对比文档 §3.1b。

## [0.3.10] - 2026-10-02

上一轮在 `iris_net_fix.sh` 里修掉了「声称成功、实测失败」，同一类缺陷在 `ai_guardian`
这一侧还完整留着，本轮把它修完了。核心结论一句话：**探针在容器里，而被修的东西在 guest
的 `image.raw` 里，所以这些自愈动作从来就不可能生效——但每次启动都被记成了两次成功修复。**

### 修复

- **自愈动作无条件报成功**。`_WATCHDOG_SCRIPT` / `_CLEANUP_SCRIPT` 末尾是无条件的
  `echo ...-APPLIED; exit 0`，而两个脚本遍历的是 guest 的二进制与进程路径——这些路径在
  容器里永远不存在。三个脚本现在改为输出**实际计数**：
  `WATCHDOG-FIX-APPLIED n=<数>` / `RESOURCE-CLEANUP-APPLIED n=<数>` / `DIAG-DISABLED n=<数>`。
- **三种状态不再混为一谈**。`n=0`（看了，什么都没有）、没有 `n=`（探针根本没跑成）、
  `n>0`（真的动了东西）分别对应不同处理：前两者都不记成功，第三个才写 ledger。
  `_exec_in_container` 返回 rc≠0 时同样不算修复。
- **`_exec_in_guest` → `_exec_in_container`**。旧 docstring 写着 "inside the running
  container" 却返回 "the guest's own exit status"，而同一类的 `_docker` docstring 已经
  正确地写着「no docker exec can touch its processes」——两处自相矛盾，而所有脚本都建立在
  错的那一处上。
- **不可达的动作不再每 30 秒重试**。修好返回值之后，`recommend_recovery_action` 会对一个
  注定失败的动作反复推荐，`start_continuous_monitoring` 循环每轮都往 append-only ledger 里
  塞一条失败记录。现在探针报 `n=0` 时把该动作记入 `_unreachable_actions` 并附原因，
  推荐时跳过；全部不可达时打出原因并返回 `None`（`recommend_recovery_action` 由
  一串提前 return 改为候选列表 + 过滤，优先级顺序不变且有测试守住）。
- **`_CLEANUP_SCRIPT` 少一个字母的变量名**。默认值赋给了 `IRIS_GUARDIAN_PROCESSES`，循环
  读的却是 `IRIS_GUARDAN_PROCESSES`（少一个 I），未设置时展开为空 → for 循环一次都不跑 →
  `n=0`。这个 bug 是被本轮新增的测试逼出来的：它和「guest 里确实没有可杀进程」输出完全相同。
- **按 pid 计数而非按进程名**。`monitord` 有三个进程就是杀了三个；少报的数字和虚报的成功
  是同一类谎报。

### 测试

- `tests/test_guardian_repairs_are_honest.py`（33 项，1 项 skip）：**真实执行三个 shell
  脚本**（写文件 + 真实 bash + 环境变量覆盖 + prelude 注入 shell 函数替身）。
  原有 `tests/test_guardian.py` 的相关用例全部用 `FakeExec` 伪造 stdout 返回
  `"WATCHDOG-FIX-APPLIED\n"`——一个恒 `echo X; exit 0` 的脚本能通过全部这些测试，这正是
  缺陷能存活的原因。mock 掉被信任的对象等于没测。
- **6 项变异验证全部如期变红**，其中 M6（把 `mv ... && count++` 改成 `mv ...; count++`）
  **先变绿**，暴露了「rename 失败仍被计为已禁用」这个真实盲区，补了两项测试（watchdog 与
  diag 各一项，用 `mv(){ return 1; }` 替身）后复跑变红。
- 符号链接用例在本机 skip：Windows 上 `ln -s` 创建的是文件副本而非符号链接，`find -type l`
  恒为空，会让该分支被当成「找到 0 个」而静默通过——正是本套件要区分的那两种答案之一。

### 已知局限（如实记录，未修）

- 三个修复脚本仍然只能作用于**容器**可见的对象。要真正修 guest，需要能进 guest 的通道
  （例如上一轮那个只在部分固件上可用的 telnetd，或 QEMU guest agent）。本轮不假装这个
  通道存在：探针报告 0 时说的是「guest 状态 UNKNOWN」，不是「guest 是干净的」。
- `kill -9` 计数是「信号送达数」，不是「确认已死的进程数」——脚本在容器侧无法复验目标是否
  真的消失。

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
