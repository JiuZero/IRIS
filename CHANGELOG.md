# 更新日志

本文件记录 IRIS（IoT Rehosting & Interconnection Simulator，鸢尾）的版本变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.3.30] - 2026-10-08

把「只能手改 `.env`」变成面板可写。0.3.29 的 LLM 层留了个具体的窟窿：六个 LLM 字段
（外加四个运行时字段）只能靠重启、编辑 `.env`、重启来改，而仓库里**根本不存在任何配置写入路径**——
`GET /api/v1/config` 的 `read_only: True` 是硬编码常量，设置面板的注释写着「能改运行中服务环境的
设置面板是没法测试的面板」。那句话在只有 `.env` 的年代是对的。本轮配置项直接加进现有抽屉，
写进 IRIS 自己的 `iris-home/settings.json`。详见 `docs/14-Web设置能力.md`。

### 新增

- **`src/iris/config_store.py`**：`iris-home/settings.json` 的读写层。十项可写字段集中在
  `EDITABLE_SETTINGS` 一张表里（key/label/kind/help/min/max/placeholder），
  **「面板能改什么」全仓库只有这一个定义**——API 从它渲染表单并拒绝表外键，
  前端 `types.ts` 不复制这份名单。写盘走同目录 `.tmp` + `os.replace`，
  半个文件不会留在原地等下一次启动去读
- **`PUT /api/v1/config`**（部分保存）与 **`DELETE /api/v1/config?keys=a&keys=b`**（取消覆盖）。
  两者分开的理由是「不再覆盖这个」和「设成某个值」是两句不同的话，折叠在一起需要一个
  表示 unset 的哨兵值，而文本字段装不下它（`""` 在端点字段上意为「整个 LLM 层停用」）。
  `keys` 是重复查询参数不是逗号拼接，逗号拼过去的 `"a,b"` 会被当成一个未知键
- **`Settings.settings_customise_sources`**：优先级变为
  **init > settings.json > `IRIS_*` > `.env` > 默认值**。面板压过环境变量，因为启动脚本里的
  `IRIS_*` 说的是「这个部署是什么」，面板里存的说的是「这个人刚选了什么」，后者更新。
  落点是 pydantic source 而非构造后的 validator
- **覆盖层文件损坏不拦启动**：降级为 warning 并按环境变量启动。仿真不依赖这些字段，
  为坏掉的偏好文件拒绝开机等于把面板的一次误操作变成故障
- **`tests/test_web_settings.py`**（101 例），多数钉在拒绝上：表外键 422（含危险三项）、
  越界数值、文本型数字、字符串型开关、枚举外的日志级别、一次被拒的表单不留下九个已写入的字段、
  损坏文件响亮报错、密钥不回流、原子写不留 `.tmp`、`types.ts` ↔ Pydantic 双向字段相等

### 变更

- **`GET /api/v1/config`** 由 `read_only: bool` 常量改为 `ConfigResponse`：逐行返回
  `value`（运行值）/ `stored`（文件值）/ `pending_restart`，密钥只报是否已配置。
  删掉了 `tests/test_web_app.py` 里的 `assert body["read_only"] is True`——它把一个
  硬编码的假声明当成了期望行为
- **`SettingsSheet.tsx`** 的配置组由只读改为可编辑（按 `kind` 选控件、脏项计数、
  保存/放弃、每行「取消覆盖」与「待重启」徽章）
- **`RESTART_KEYS` 名单删除**：初版把四个字段列为「需重启」、声称 LLM 字段下次诊断即生效，
  理由是 LLM client 每次现造。**这个理由是错的**——`get_settings()` 是无 reload 的单例，
  client 确实是每次现造，它造自的那份 settings 不是每次重读的。现改为十项全都要重启，
  并用 `_needs_restart()` 回答更有用的问题：「下次启动解析出来的东西是否与这个进程正在用的不同」。
  保存与环境变量相同的值不再标「待重启」，否则这个徽章会被训练成噪音
- **`api_token` / `iris_home` / `database_url` 明确不可写**：改令牌的那个请求本身要过鉴权，
  能改它就能把自己锁在门外（含用来改回去的端点）；后两个指向已装数据的库，
  面板换掉它们是在承诺一次没有任何代码执行的迁移
- **`.gitignore`** 增加 `iris-home/settings.json`、`.tmp` 与 `iris-home/ai-drafts/`
- **`Inspector.tsx` 的「上传上限」**改为从 `rows` 里按 key 取，避免每多一个可写字段
  就再长一个专用字段

### 验证

- 真服务 round trip（`IRIS_IRIS_HOME` 临时目录，端口 8742–8744）：GET 十行 /
  PUT 写盘无 `.tmp` 残留 / **重启后十项全部生效且 `pending` 全为 `False`** /
  `ai-guardian` 从 `planned` 变 `available` / 覆盖层压过 `IRIS_LLM_TIMEOUT_SEC=90` /
  危险三项 PUT+DELETE 均 422 / 无令牌 401 / 被拒的写入不改动文件 /
  逗号拼接的 keys 被当作一个未知键拒绝。复核后删除临时 home（内含明文密钥）
- 门禁：`ruff` 全绿；`1881 passed, 8 skipped`；`npx tsc --noEmit` 与 `npm run build:pkg` 全绿

### 已知边界

- 十项全都要重启，没有热重载（`get_settings()` 单例的直接后果）
- 取消覆盖一律报「要重启」，即使回落值恰好等于运行值——那个判断需要知道 fallback，
  而手里的 settings 对象是通过**正在被删掉的**覆盖层解析出来的
- 保存的密钥无法从面板读回，因此也无法从浏览器侧判断新旧是否相同；比较在服务端完成、只回传布尔

## [0.3.29] - 2026-10-08

给规则层的「我不知道」一个出口。IRIS 的插件按型号特化（六个内置插件里四个绑定了具体厂商的
具体路径），遇到没见过的固件时规则层只能回答「我不知道」，而这份维护成本随厂商数线性增长。
本轮新增的 LLM 层专收长尾：把串口日志与模式计数交给 OpenAI 兼容端点换一份带证据的归因，
把「模型写的规则」落进草稿区等人确认安装——**降维护度的机制是沉淀回规则，不是每次都问模型**。
详见 `docs/13-LLM归因与插件草稿.md`。

### 新增

- **`src/iris/llm/`**：`schema`（模型看到的 JSON 形状 + 三值动作枚举）/ `context`（证据采集）
  / `client`（OpenAI 兼容对话调用 + 围栏容忍 + 重试预算）/ `drafts`（草稿区的保存、安装、拒绝）
  / `diagnose`（编排：先问规则层，再问模型）
- **`POST /api/v1/ai/diagnose`**（`web_app.DiagnosisRequest/Response` + `llm.diagnose.diagnose_instance`）。
  第一步永远是 `AIHealthMonitor.recommend_recovery_action()`，给出建议就直接返回且 `llm_used=false`——
  这是「沉淀回规则」的闭环入口，也是省钱之外的设计本身。模型只在规则层沉默时才被调用，
  且只收到**模式计数与串口日志尾部**，不收到任何凭据
- **五条 AI 路由**：`GET /api/v1/ai/status`、`POST /api/v1/ai/diagnose`、`GET /api/v1/ai/drafts`
  （附 `yaml` 正文，审核要读文档而不是信元数据）、`POST /api/v1/ai/drafts`、
  `POST /api/v1/ai/drafts/{rule_id}/install`、`DELETE /api/v1/ai/drafts/{rule_id}`。
  带参数的安装与拒绝声明在静态路由之后，与 runs 路由同一条声明顺序规则
- **AI 产物三道门**：`iris-home/ai-drafts/`（与 `plugin_dir` 隔离，引擎的加载路径够不着）
  → 既有 `install_plugin` 验证链（模型写的文档被人拒绝，同样被人拒绝）
  → 人工在插件页确认安装。`reject_draft` 只允许拒绝 `pending`：
  已 `accepted` 的草案其插件已在插件目录生效，拒绝它只会让审核记录说「已拒绝」而插件其实还在跑，
  这种拒绝返回 404 并指向插件页的卸载
- **配置**（`config.py`）：`llm_base_url` / `llm_api_key` / `llm_model` / `llm_timeout_sec`
  / `llm_max_retries` / `llm_max_context_chars`。`llm_enabled` 要求端点与模型名**同时**齐备，
  少一个是半配置，只会让每次请求都失败；无端点时整层 `disabled`，诊断接口立刻返回并说明原因，
  **绝不阻塞主仿真**。`base_url` 可指向 ollama / vLLM 等本地端点，离线可用
- **前端**：插件页新增 `AiDraftPanel`（待审草案列表 + 详情弹窗 + 安装 / 拒绝），
  实例页 ActiveTable 每行加「AI 诊断」按钮 + `DiagnosisModal`（可把模型的 `DRAFT_PLUGIN`
  建议直接存成草案）。6 个 `api.ts` 方法与 6 个 `queries.ts` hook 一一对应
- **`web_data.py` 的 `ai-guardian` 能力行改为随配置动态化**：配了端点报 `available`，
  没配报 `disabled` 并说明缺什么——固定成「unavailable 且不含任何模型调用」会让配上端点的用户
  看到一句假话
- `httpx` 升为运行时依赖（此前只在开发依赖里）

### 修复

- **`drafts.reject_draft` 两个真实缺陷**（本轮真实 server 复核时发现，均已补测试）：
  ① 重复拒绝同一草案会二次报「已删除」，把一次正常的重复点击说成数据不一致；
  ② **已 `accepted` 的草案可以被拒绝**，会让审核记录与插件实际生效状态相反

### 明确不做

- **不新增 CLI 触发入口**：诊断只有页面按钮一个入口，没有 `iris emulate --ai-diagnose`
- **不做自由多轮会话**：一次请求一次归因，不保留对话历史
- **不绕过验证链直写插件目录**：草案安装必须走 `install_plugin`
- 不扩 `repair_action.evidence` 列（`String` 非 JSON，塞结构化证据是错类型）

### 测试

- `tests/test_llm_client.py`（21）：请求形状 / 围栏容忍 / 尾随闲话不原谅 / 重试预算 /
  半配置不发 socket
- `tests/test_llm_drafts.py`（31）：草稿区隔离（对照 `effective_rules_dirs` + `list_plugins()`）、
  拒覆盖、孤儿 meta 清理、install 走 loader、promoted 记账、HTTP 端点 401/422/404
- `tests/test_llm_diagnose.py`（21）：规则层先试短路（`_ForbiddenClient` 证明不调模型）、
  prompt 材料真实性、日志截断、三种互斥结果、DB 不可读降级
- `tests/test_llm_api.py`（53）：6 端点 401/200、status 三态不泄 key、`asyncio.to_thread`
  静态 + 运行时验证、路由声明顺序、Caller 签名静态检查、8 模型 `extra="forbid"`、
  capabilities 动态化、`types.ts` ↔ Pydantic 双向字段、动作枚举双向、设计令牌
- `tests/test_web_data.py`：`test_ai_guardian_is_declared_unavailable_and_says_why`
  改为 `test_ai_guardian_follows_the_endpoint_rather_than_a_constant`
- 5 项变异验证全部确认会红后还原：去掉规则层短路（4 红）/ 草稿区与 `plugin_dir` 合并（11 红）/
  放宽动作枚举（2 红）/ install 绕过 `install_plugin`（4 红）/ 重复 reject 返 200
- 全量 `pytest -q`：**1779 passed, 8 skipped**（`ruff check src tests` 全过）

## [0.3.28] - 2026-10-08

可视化数据面补齐：主页此前能回答「跑得怎么样」，但答不出「哪些固件」「跑了多久」「失败落在哪个架构」。本轮补三个视图（语料矩阵 / Web 可达耗时分布 / 失败聚类矩阵），三个视图的每一个总数都复用 `run_stats` 或其已解析的 stage，与统计卡、失败直方图同源，避免两处对同一批运行给出两套数字。

### 新增

- **语料矩阵**（`GET /api/v1/stats/corpus`，`iris.db.runs.corpus_profile` + `web_data.corpus_view`）。
  一行一个固件：架构、运行次数、Web 可达次数、最近一次的主因。`arch` 取该固件各次运行**实测到**的值，
  运行间不一致标 `mixed`、缺失标 `?`，不取众数——`run_stats` 是按每次运行的架构计的，
  面板若按另一个口径报架构，两张卡就会对同一批运行各说各话。
  `image_id` 为空的运行（`attribute_to_image` 匹配不上时**故意**存 NULL）独立成组，
  不摊到任何固件上：错归属会静默污染每一行，缺归属是看得见的
- **Web 可达耗时分布**（`GET /api/v1/stats/latency`，`iris.db.runs.latency_profile` + `web_data.latency_view`）。
  按架构给 min / 中位 / p90 / 最长。分位数取**最近秩**，不做插值与平均：
  `[1, 2, 100]` 的中位是 2（真跑过 2s 那次），不是 34（没有任何一次跑过）。
  `time_web` 的真实语义已核清：它是整轮仿真墙钟耗时（含容器启动、镜像构建、QEMU 与 guest 启动），
  不是 web 探针应答时刻，且**仅在 web 面有响应时写入**，因此未跑通的运行不贡献样本；
  这部分运行数由 `unmeasured` 显式报出，避免「没有慢的运行」这种误读
- **失败聚类矩阵**（`GET /api/v1/stats/failure-matrix`，`iris.db.runs.failure_cross` + `web_data.failure_matrix_view`）。
  stage × 架构交叉表。stage 直接取 `run_stats().failure_stages`（与失败直方图同一份解析），
  informational 类原因（`network-fallback-ok` 等）排除——把「网络兜底生效」计成失败
  是把一个能工作的语料涂成坏的。同一 kind 在多行出现时按 `run_stats` 解析出的那一个 stage 归类，
  解析不出 stage 的失败信号数由 `unclassified` 显式报出，不静默丢弃
- **前端三个面板**（`web/src/pages/Dashboard.tsx` 新增 `CorpusPanel` / `LatencyPanel` / `FailureMatrixPanel`）。
  每个视图都把自己的口径 `note` 渲染到面板底部。耗时用页面 SVG 自绘 min→max 跨度并标出中位与 p90
  （`ui.tsx` 新增 `SpanBar`，颜色走 `currentColor` + 文字工具类，不引图表库、不写死颜色）。
  三个视图不轮询：样本只在一次运行结束时变化，那是导航而不是等待，统计卡本身已经在 3s 轮询
- **`ArchBar` 从 `Dashboard.tsx` 提到 `ui.tsx`**：评测集与语料矩阵现在都是「一行一架构 + 同一条比例条」，
  两份实现会漂移，漂移后同一屏上同一批运行会显示两个比例

### 测试

- `tests/test_web_corpus_views.py` 新增 **68 用例**：同源（`totals` 必须等于 `run_stats`）、
  informational 排除、最近秩分位数（`[1,2,100]` → median 2）、未归属不摊派、
  截断与真实计数分离、三端点 401 + 签名里必须写 `Caller`（`get_type_hints` 读的是 postponed
  注解，直接比对注解对象会永远落空）、空库读作空且仍带口径说明、8 个响应模型 `extra="forbid"`、
  **前端 `types.ts` 与服务端 pydantic 模型字段集合双向相等**（8 组参数化，该文件无生成器，
  单边改名只会得到一个 200 和一个空白格）
- 变异验证（各自指名会红的用例，恢复后全绿）：分位数改均值 →
  `test_the_median_is_a_time_a_run_actually_took`；去掉 `_informational()` 排除 →
  `test_the_worked_network_fallback_is_not_a_failure`；矩阵自行逐行解析 stage →
  `test_two_rows_of_one_kind_do_not_split_into_two_stages`；`truncated` 写死 `False` →
  `test_a_truncated_sample_says_so_and_keeps_the_real_count`；`totals` 改按固件行汇总 →
  `test_the_run_total_is_the_one_run_stats_counts`
  （后两项首次尝试的变异体与原实现等价、未变红，已改到能真正分歧的形态再验）
- 全量门禁：`1653 passed / 8 skipped`（较 0.3.27 净增 68 用例）、ruff 全绿、`npx tsc --noEmit` 零错误
- 真实运行复核：真起 `iris web --no-browser --port 9137 --api-token …`，三个端点**无 token 均 401、
  带 token 均 200**，note 均非空。真实库上读到：语料 1 个已归属固件（`G1V31si.bin` / mipsel / router）
  + 2 次未归属运行（其 `arch` 真实呈现为 `mixed`），耗时 arm64 98s / armel 50s、`unmeasured=1`，
  失败矩阵 network 与 service 两个 stage 均落在 mipsel、`unclassified=0`

### 已知

- 本轮 Docker daemon 未运行（`npipe:////./pipe/dockerDesktopLinuxEngine` 不存在），真实 QEMU 仿真复跑未验证；
  真实浏览器渲染亦未逐像素核对（内置浏览器只回报「已打开」，不返回渲染结果或截图），
  但页面加载后服务端日志确认三个新端点被真实浏览器请求并返回 200
- `emulation_run.time_ping` 在全仓无任何写路径（只有 `web_data.py` 读、`models.py` 声明），恒为 NULL；
  本轮因此不依赖它，若将来接入分层链路探针需先补写路径
- 三个端点只在 `iris web`（工作台装配）下可达，`iris serve start` 挂的是未装配的 API app，
  对这三个路径返回 404 —— 这是两个命令的既有分工，不是本轮引入

## [0.3.27] - 2026-10-08

真实运行验证发现的回归修补：0.3.25 声称的「web 服务日志落盘」在 `iris serve` / `iris web` 上**从未生效**，本轮用真实进程复现、修掉，并补上能抓住它的守卫。共三处独立成因，最深的一处会让服务启动后所有结构化日志直接抛异常。

### 修复

- **uvicorn 的 access log 从不落盘**（`src/iris/log.py` `uvicorn_log_config()`）。
  三个 uvicorn logger 各自只挂 `handlers: ["console"]` 且 `propagate: False`，因此启动与
  access 记录永远到不了 root 的 `RotatingFileHandler`。而 `serve` 期间 CLI 启动提示走
  `StreamLogger`（设计如此，直写 stdout）、成功请求只产生 uvicorn access log，
  `iris.log` 在典型场景下恒为 0 字节。改为这三个 logger 不带自己的 handler、
  `propagate: True`，由 root 统一落地：一次处理一份记录，将来往 root 加第二个目的地
  无需改这个 dict，且不必为同一文件再开第二个句柄
- **`dictConfig` 关掉 root 的 file handler 后，structlog 全线抛异常**（`src/iris/log.py`
  新增 `_HandlerStream`）。uvicorn 的 `Config.configure_logging` 会执行 `dictConfig`，
  非增量路径调用 `logging.shutdown` 关闭全部已注册 handler；sink 若持有 stream 对象，
  server 启动后第一条 IRIS 日志就是
  `ValueError: I/O operation on closed file`，日志文件「启动前可用、启动后正好丢掉最该留的记录」。
  实测确认：`dictConfig` 之后 root handler 仍在列表里但流已关闭。改为每次写时经 handler
  取流（handler 自己会重开，与 `logging` 的 emit 行为一致）；丢一行日志比整个调用链抛异常好
- **`use_colors` 让 uvicorn 在 dictConfig 内 `KeyError`**（`log.py`）。
  `Config.configure_logging` 在 `use_colors` 为 bool 时写
  `formatters["default"]["use_colors"]` 与 `["access"]`，当前 config 缺这两个键 →
  在绑定端口之前就崩。config 补两个 `PlainFormatter` 别名；`PlainFormatter.__init__`
  接受 `use_colors`（`dictConfig` 会把它当构造参数转发），IRIS 自己的 `color` 优先

### 测试

- `tests/test_log_to_file.py` 21 → 25：`test_a_real_iris_serve_lands_its_access_log_in_the_file`
  起**真实 `iris serve` 子进程**、发真实请求、读回 `iris.log`（原 21 例只覆盖 structlog 与
  stdlib 两条路径，uvicorn 的 dictConfig 路径从未被触达）；另加
  `test_a_uvicorn_record_reaches_a_file_written_by_setup_logging`、
  `test_structlog_still_writes_after_dictconfig_closed_the_handler`、
  `test_uvicorn_can_write_use_colors_into_this_config`
- 变异验证（各自指名会红的变异，恢复后全绿）：
  `propagate` 改回 `False` + 自带 console handler → 4 处变红（真实 serve 子进程读到的
  `iris.log` 是 0 字节）；sink 改回裸 `handler.stream` → 1 处变红（`ValueError` 复现）
- 全量门禁：`1585 passed / 8 skipped`（较 0.3.26 净增 4 用例）、ruff 全绿
- 真实运行复核：`iris serve` 与 `iris web` 各起独立隔离 home，`iris.log` 分别 692 / 513
  字节，含带时间戳的 `GET /api/v1/health` 200 行，无 ANSI 序列

### 已知

- 本轮 Docker daemon 未运行（`npipe:////./pipe/dockerDesktopLinuxEngine` 不存在），
  真实 QEMU 仿真复跑与前端浏览器渲染未验证；本轮全部改动限于日志通道，与二者无关

## [0.3.26] - 2026-10-07

「内核实现优化」评估的可落地部分：落点核实推翻了「选型规则化进 YAML 规则引擎」的原预设（规则引擎是 guest 侧自愈语义，启动配置选型塞进去属语义错位，且 QemuConfig 表已是单一权威来源），实际落地为**内核资产完整性守卫 + panic 归因细化 + 变更纪律文档化**。改内核代码（B 路线）、厂商内核补丁（C）、QEMU fork（D）按评估结论不做。

### 新增

- **内核资产存在性守卫**（`tests/test_qemu_config_matches_script.py` 新增 `TestKernelAssetsExistOnDisk`，7 用例 + 3 skip）。
  新增架构 = 表行 + `case` 分支 + `binaries/` 内核文件三件套，此前文件是三者中唯一无守卫的：
  表里写了 `zImage.armel` 而文件没放（或写错名、或零字节占位）时，只有容器内 QEMU 的报错能说明原因。
  守卫断言每个 `QemuConfig.kernel_file`/`initramfs` 在磁盘上真实存在且非零字节。
  变异验证：表里把 `vmlinux.mipsel.4` 改成不存在的 `.5` → 3 处变红
- **panic 归因细化**（`src/iris/emulate/orchestrator.py` 新增 `_PANIC_CLUES`/`_panic_clue`）。
  `No working init found`（iid 9591 实测）与 `Unable to mount root fs`/`VFS: Cannot open root device`
  都把 guest 停死，但调用的工作相反——前者是内核起来了找不到 init（查 rootfs 挂载与注入的 init），
  后者是 rootfs 卷从未挂上（查 root= 背后的块设备与驱动）。finding 的 message 按panic 行文本
  追加对应方向，evidence 新增 `panic_class`；两类真实标记之外保持泛化措辞，不虚构原因
- **部署文档补「内核与资产变更纪律」**（`docs/04-快速部署.md` 3.1 节）。四条：新增架构三件套
  （漏件守卫变红）、替换资产须刷新 sha256/重建 initramfs/重建 baked 镜像、交付前全语料回归
  （单轮 150–420s 量级，默认 600s 已留余量）、不为单台固件改内核
- **部署文档资产表与配置表的清单一致守卫**（`tests/test_qemu_config_matches_script.py`）：
  文档 3.1 表漏列或误列任何 `qemu_config` 引用的内核资产即变红——资产名现在有四方镜像
  （表、脚本、目录、文档），每两方之间都有对照

### 修复

- **`test_fresh_pack_is_reused` 声明了「no Docker required」却真需要 Docker**（`tests/test_tarball_cache.py`）。
  该用例「Nothing downstream is mocked on purpose」走到 `docker build`，Docker daemon 未运行时
  全量门禁红（本次实测暴露）。修法是把 bail 点放在 `_build_baked_image`（复用判定此时已做出），
  并新增「unchanged tree 保持原 pack」断言；文件头「no Docker required」声明回归为真

### 测试

- 全量门禁：`1581 passed / 8 skipped`（较 0.3.25 净增 12 用例）、ruff 全绿、`tsc` 零错误
- panic 细化与资产守卫均做变异验证（panic 分类禁用 → 3 变红；资产名漂移 → 3 变红），恢复全绿

### 已知

- 未在真实 Docker/QEMU 环境复跑仿真（daemon 未运行）；「为 mipseb/mipsel 补 modern 内核」
  需下载/编译资产，本机网络不可达，未实施

## [未发布] - 2026-10-06（参赛交付轮）

本轮不涉及功能代码，全部是参赛交付包所需的素材、品牌与文档改动。

### 变更

- **Web 品牌标识**（`web/public/logo.png`、`web/public/favicon.png`、
  `web/index.html`、`web/src/layout/Header.tsx`）。源图 `logo.png` 为 1535×1535 RGBA
  （1.9 MB），不适合直接引用；本次压缩为 256×256（16 KB，用于 Header 与 og:image）
  与 64×64（3 KB，用于 favicon）两版，量化后平均 RGB 偏差 2.24/255，肉眼不可辨。
  Header 第 31 行的 lucide `Server` 图标替换为 `<img src="/logo.png" alt="IRIS" />`
  （`h-6 w-6`），`Server` 从 import 中移除以满足 `noUnusedLocals`；
  `index.html` 补 favicon / apple-touch-icon / og:title / og:description / og:image。
  **注意**：`web/dist` 与 `src/iris/web/dist` 均为构建产物且被 `.gitignore` 排除，
  改动需 `cd web && npm run build:pkg` 回灌后才对 `iris web` 生效。
- **README 重写为参赛叙事**：新增「① 痛点 / ② IRIS 的答案 / ③ 证据」三段主线，
  把失败可归因前置；「已知限制」整节移出主叙事，改写为「设计边界」六条
  （放在能力矩阵之后、快速开始之前），细节展开落到新增的
  [`docs/12-设计边界与技术限制.md`](docs/12-设计边界与技术限制.md)。
- **统一对外数据口径**：删除「Web 可达 3/5、目标 ≥80%、当前 60%」这类互相打架的旧口径，
  全文只保留一套——有效语料 9 台，提取成功与进入仿真 IRIS 8 / FirmAE 5，
  Web 可达 IRIS 4 / FirmAE 2；逐台判定 IRIS 占优 4、平 4、FirmAE 占优 0、无法判定 1。
  目标（≥80% / ≥60%）从「现状」旁边移到「落地目标」，并明确标注「这是目标」。
- **显著化前端构建前置步骤**：README 的「Web 工作台」段加入警告块，说明
  `npm run build` 不够、必须 `npm run build:pkg`，否则 `iris web` 返回 503。
- **`.gitignore` 补 `web/vite.config.ts.timestamp-*.mjs`**：vite 读配置时的临时产物，
  异常退出会残留，属噪声，不入库。

### 本轮实跑验证（供交付证据留档）

| iid | 固件 | arch | 结果 | 备注 |
|---|---|---|---|---|
| 6630 | D-Link DIR-868L revB | armel | ✅ HTTP 200 @50.2s | 命中规则 `dev-extended-nodes`；宿主在 guest 子网内重新落点后建立转发 |
| 512 | Tenda G1V31si | mipsel | ❌ `link-no-service`（311.1s） | ping 通、web 不通；串口报 `no web server fallback available`，与 `docs/08` §3 结论一致 |

## [0.3.25] - 2026-10-06


从 21 份实跑日志里归纳出的 P0/P1/P2/P3 清单一次性全量落地。核心是**一处把
240s 超时归咎于端口的假结论**：iid 6715 的 guest 地址恰好等于代码里的默认假设，
于是「地址变了吗」那道门一直没开，socat 转发从未建立，`curl 127.0.0.1:{port}`
必然 000，而失败归因把原因写成了「anything other than :80」。

### 修复

- **guest 地址等于默认假设时转发从未建立**（`src/iris/emulate/orchestrator.py`）。
  判据从 `if detected and detected != guest_ip` 换成新纯函数 `_forward_target(detected,
  forwarded_to)`：只有 loopback 或 None 不转发，**地址等于假设照样建转发**。
  启动循环的 `socat_updated: bool` 一并拆成 `forwarded_to: str | None`，
  把「地址测到了没有」和「转发建成了没有」两个语义分开——原来那个布尔量同时背了两件事，
  正是它让「测到了」看起来像「转发好了」
- **失败归因不再无条件指控端口**（同文件）。拆出 `_read_binds()` 与 `_web_findings()`：
  bind 从串口日志里读，并支持三种格式（`inet_bind[PID: N (proc)]`、
  `net_bind: bind IP:PORT`、`Listen on IP:PORT`），按出现位置排序去重；真实比对 `:80`，
  含 80 → `WEB_UNREACHABLE`（措辞明说端口不是原因），不含 80 且有 bind → `WEB_WRONG_PORT`，
  连 bind 都没有 → `WEB_UNREACHABLE`（此时不能说端口错）。
  原代码里那句注释与旧测试 `test_wrong_web_port_is_the_whole_story`
  从没比对过 `:80`，却把「`bound nginx:80` + 该文案」断言成期望行为
- **`POST /api/v1/pipeline` 漏掉架构归一化**（`src/iris/api/server.py`）。
  `POST /api/v1/emulate`（:281）与上传启动（:720）都先 `normalize_arch` 再查白名单，
  pipeline 没有；而 `info.arch` 是普查标签、普查说「aarch64」，QEMU 配置的键是「arm64」，
  于是同一份固件走 `iris emulate run --arch aarch64` 能跑、走 pipeline 被拒成
  `unsupported-arch: 'aarch64' not in supported ['armel', 'arm64', 'mipseb', 'mipsel']`
- **启动超时默认值收敛为单一来源**（`src/iris/config.py`）。新增
  `DEFAULT_BOOT_TIMEOUT_SEC = 600`，替换 `server.py`/`cli.py`/`orchestrator.py` 共 5 处
  落点；前端新增 `web/src/lib/constants.ts`，`LaunchDialog.tsx` 两处字面量改为引用。
  依据写在常量注释里：实测上界 420s（iid 6715），600s 留 43% 余量。
  上传启动的 `_UPLOAD_LAUNCH_TIMEOUT_SEC` 900 → 1200，否则它会小于新默认值
- **内核级崩溃证据缺失且 Oops 被当成失败**（`src/iris/failures.py`）。
  新增 `GUEST_KERNEL_PANIC`（失败）与 `GUEST_KERNEL_OOPS`（入 `INFORMATIONAL_KINDS`），
  判据来自真实日志：`emulate-9591` 第 254 行 `Kernel panic - not syncing: No working
  init found` 且全文 0 个 inet_bind（致命），而 `emulate-6715` bind 在 536 行、Oops 在
  678 行、`emulate-5192` bind 498 / Oops 645（都是先绑定后 Oops，服务仍可达）。
  所以「有 Oops 就判失败」是假阳性，改为 panic=失败、oops=informational 记录
- **`_boot_findings` 对健康 guest 误报 `WEB_UNREACHABLE`**（同文件）。
  它无从知道 web 是否可达。新增 `web_reachable: bool | None` 贯穿
  `_boot_findings`/`diagnose_boot_failure`/`_failure_diagnosis`：None=log-only 仍跑探针、
  True=跳过 web 探针、False=照跑
- **构建产物为空仍继续往下走**（`orchestrator.py` / `scripts/emulate/make_image.sh`）。
  新增 `_reject_empty_artifact()` 接在 `_create_tarball` 与 `docker cp` 之后；
  `_tarball_is_stale` 把 0 字节排除在复用之外（mtime 再新也不复用）；
  `make_image.sh` 顶部加 `[ ! -s "${TARBALL}" ]` → stderr 报错 + `exit 3`，
  位置在 `qemu-img create` 与 `mkfs` 之前
- **loop 卸载无兜底**（`make_image.sh`）。加 `MNTED` 变量 + `cleanup()` + `trap cleanup EXIT`，
  内容为 `umount || umount -l || true`；显式卸载后置 `MNTED=0`，避免 trap 二次尝试

### 新增

- **日志可落盘**（`src/iris/log.py` / `config.py`）。`log_to_file: bool = False` +
  派生属性 `log_file`（`<iris_home>/logs/iris.log`），`RotatingFileHandler` 5MB×3。
  实现约束两条，都是实测逼出来的：structlog 经自己的 `logger_factory` 直写 stdout、
  **不经过 root handlers**，所以只给 root 挂 FileHandler 只能抓到 uvicorn；
  而重路由 structlog 走 `logging` 会让 `record.getMessage()` 接管整行、丢掉所有 extras 字段、
  破坏输出形状。解法是给 `PlainRenderer` 加 `sink` 参数，在渲染时额外写一份**无色**副本，
  控制台形状不变；有 sink 时 `cache_logger_on_first_use` 必须关，否则旧 logger 保留旧 renderer
- **uvicorn 日志接入同一套格式化**（`log.py`）。新增 `uvicorn_log_config()`（dictConfig，
  `disable_existing_loggers: False`，formatters 用 `PlainFormatter`），
  `cli.py` 三处 `uvicorn.run` 带上它
- **构建输出与 stderr 分离**（`make_image.sh` / `orchestrator.py`）。脚本顶部
  `exec 3>&1 1>&2` + `progress()`，10 处进度行改走 fd 3，stdout 只留
  `==== Image built: ${IMAGE} ====`；新增 `_build_failure_detail()`（双流合并取尾部 800 字符），
  成功路径改 `logger.info` 打 stdout 全文
- **前端失败标签补齐到服务端全集**（`web/src/lib/format.ts`）。`FAILURE_LABELS` 由 8 项补为 30 项：
  原来 4 项服务端已不存在、22 项缺失（含 `GUEST_KERNEL_PANIC` 与 `network-fallback-ok`）
- **稳定性复现文档补一节**（`docs/04-快速部署.md`）。新增「MSYS 路径转换的作用范围」：
  转换只发生在「从 Git Bash 手敲 bash 命令」这一层，Python `subprocess.run([...])` 传
  `/work/scripts/x.sh` 原样送达（`OSTYPE=cygwin` 下实测），所以手工重跑容器内脚本必须
  `MSYS_NO_PATHCONV=1`

### 测试

- `tests/test_boot_diagnosis.py` 53 → 71：新增 `TestForwardTarget`（7 用例）、真实 6715
  多端口夹具、busybox bind 格式、bind 与 Oops 的日志顺序；
  `TestGuestKernelCrashIsNotAnInvisibleVerdict`（14 用例）带 `_NO_INIT_PANIC` /
  `_OOPS_AFTER_BIND` 两份真实日志。变异验证：还原「地址相等门控」→ 2 变红；
  去掉 `:80` 真实比对 → 2 变红；去掉 web_reachable 守卫 → 2 变红；
  Oops 移出 informational → 1 变红
- 新增 `tests/test_boot_timeout_default.py`（16 用例）：实测上界 420s、25% 余量、
  orchestrator 签名、`EmulateRequest`、OpenAPI 两端点、CLI `OptionInfo`、
  前端常量与后端一致、请求 ceiling > 默认 + 180
- 新增 `tests/test_failure_labels.py`（5 用例）：前后端标签集合双向相等 + 新 kind 必命名 + fallback 保留
- 新增 `tests/test_image_build_script.py`（24 用例）：tarball 前置校验、trap、输出分离
- 新增 `tests/test_log_to_file.py`（21 用例），含**真实子进程**把 stdlib 与 structlog 两行写进同一文件
- 新增 `tests/test_arch_normalization_sites.py`（14 用例）：驱动 pipeline 到抽取步骤
  （aarch64/arm64le 放行、sparc64 仍拒、拒绝文案报归一化后的名字），加源码守卫
  （白名单查表前必须归一化）。变异验证：去掉归一化 → 4 变红
- `tests/test_guest_addr_marker.py` 的 `test_only_a_measurement_is_recorded` 改写为
  `test_only_a_forward_that_was_established_is_recorded`：原用例把「等于假设就不写标记」
  钉成期望行为，而那个文件存在的全部意义就是不写假设。变异验证：还原旧门控 → 1 变红
- 全量门禁：`1569 passed / 5 skipped`、ruff 全绿、`tsc` 零错误

### 已知

- 本批未在真实 Docker/QEMU 环境跑一次完整仿真（环境限制），前端渲染效果仍无法在浏览器验证
- `make_image.sh` 改动需重建 baked 镜像才生效（`_baked_scripts_fingerprint` 会因脚本变更自动换 tag）

## [0.3.24] - 2026-10-05

历史运行记录补上「状态」列：每条记录现在能回答它对应的实例**现在**是什么状态。

### 新增

- **历史运行记录表新增「状态」列**（`src/iris/api/web_data.py`；`web/src/pages/Instances.tsx`、
  `web/src/components/RunRecord.tsx`、`web/src/lib/types.ts`、`web/src/lib/format.ts`）。
  三态由服务端判定并封闭为 `RunState`：`running`（活跃表有行）、
  `stopped`（活跃表无行但 `scratch/<iid>/` 还在——stop 只停容器、刻意留着串口快照）、
  `deleted`（两者皆无，产物已清理）。
  判定用**两次批量读取**（活跃表一次查询 + scratch 一次列目录）而不是每行一次，
  分页 20 行时不会把同一目录读二十遍。
  详情端点 `GET /api/v1/runs/{id}` 同步返回该字段：前端 `RunDetail extends RunItem`，
  类型要求必填，否则类型与线上契约会说法不一。
  文案与徽章色调收敛到 `web/src/lib/format.ts` 的共享映射，表格与详情弹窗复用同一份
- **`scratch` 目录两种命名形态在注释里写明**（`_scratch_run_dirs`）：只有纯数字 iid
  目录是实例产物，`*-rootfs` 那一类固件目录是语料，混进来会让「产物没了」误判

### 测试

- 新增 `TestRunStateInHistory`（5 用例，端点级驱动）：三态各一例、活跃表优先于
  产物、详情端点与列表口径一致；scratch 目录在用例里重定向，避免断言宿主历史。
  变异验证：把判定优先级颠倒（先看产物再看活跃表）后仅
  `test_the_active_table_outranks_the_artefact` 变红，恢复后全绿

## [0.3.23] - 2026-10-05

整洁核查批次：逻辑完整性核对 + 死代码与临时文件清理，外加两处自审发现的文案与链接缺口。

### 修复

- **`emulate run` 端口耗尽的错误提示里 `--port XXX` 是占位符**（`src/iris/cli.py`）。
  这是发给用户的错误文案，`XXX` 让「照提示操作」无从下手，改为 `--port <port>`；
  端口范围 `[8080,8199]` 的表述不变
- **检查器「查看完整日志」链接缺 `tab=console`**（`web/src/layout/Inspector.tsx`）。
  链接文本承诺看完整日志，落地却是详情页的默认页签；补上 `?tab=console` 后
  从任何页面点它都落到串口页签

### 清理

- 全量核对：`1455 passed / 5 skipped`、ruff 全绿、`tsc` 零错误；Settings 六个
  配置字段与三个派生属性逐个交叉验证（src 引用 + tests 引用 + `IRIS_*` 环境变量），
  无死配置项；业务代码无裸 `print`（`log.py` 的 `print(file=stream)` 是日志机制本身）；
  无临时/调试文件残留；第 5 批新增符号（`get_live`/`live_state`/`subscriber_name` 等）
  的定义点、调用点与测试引用齐全

## [0.3.22] - 2026-10-05

五条反馈。核心是**让「实例已停止」成为一个能传达到的状态，而不是一个不断重试的失败**，
外加两处由用户观察暴露出的真实缺陷：终端的重复输出，和串口订阅者之间的互相顶替。

### 新增

- **实例资源占用端点返回 `state`**（`src/iris/api/web_app.py`、`web_data.py`、
  `src/iris/db/active.py`；`web/src/lib/types.ts`、`web/src/layout/Inspector.tsx`、
  `web/src/hooks/queries.ts`、`web/src/pages/Instances.tsx`）。
  `GET /api/v1/instances/{iid}/stats` 此前对「已停止」与「地址写错」一律回 404，
  前端只能把它渲染成「docker stats 不可用」——而 docker 从未失败过，失败的是所有权校验，
  标题因此在指着一个没出问题的组件。现在回三态：`owned` 给读数，`gone` 回 **200**
  且带 `state="gone"`，`not-yours` 仍是 404。**中间那态必须是 200**：404 同时也是
  「这个 id 从来不存在」的答案，而这两种情况的正确反应正好相反（重试 vs 停止）。
  `not-yours` 保持 404 是因为 200 会把「活跃」与「已死」变成可枚举的差别。
  `iris.db.active.get_live` 按 iid 取行而不按 caller 过滤，调用方仍须自行比对
  `client_id`；`release` 刻意把「不是你的」与「没有」合成一个 None，停止请求必须这样，
  而一个轮询资源的面板不需要——它分不出这两者就无法渲染任何一个。
  响应模型 `extra="forbid"`，字段集合由测试从外部断言。
- **终端页显示「本连接已收字节」并说明输出来源**（`web/src/pages/TerminalPage.tsx`）。
  此前只有「丢弃字节」一个链路指标，无法区分「链路正常但 guest 安静」与「链路断了」。
  另加一句说明：`IRIS-RC:` 与固件自身的启动告警属正常日志。

### 修复

- **终端里每行日志出现两次**（`web/src/pages/TerminalPage.tsx`）。
  `write` 读的是组件级共享 ref，而一个已关闭的 socket 在收到 close 帧之前仍会继续
  触发 `onmessage`——此时 ref 已被重连创建的新终端顶替，于是旧 socket 的字节被写进
  新终端。开发模式的 StrictMode 双挂载必然触发，手动点「重连」同理；boot 期数据量最大，
  所以重复几乎必然被看到。现在 `write` 闭包捕获本次 effect 创建的 terminal，
  并用 `live` 标志在 cleanup 后拒绝写入，物理上不可能写错对象。
- **同机第二个终端标签页会顶掉第一个，随后第一个的清理又把第二个摘掉**
  （`src/iris/api/web_terminal.py`、`serial_bridge.py`；新增 `tests/test_web_terminal.py`）。
  订阅者以 `client.host` 为键，而浏览器里每个标签页的 peer 都是同一台机器：
  第二个连接在注册表里覆盖第一个，第一个断开时的 `unsubscribe` 再把第二个摘掉——
  剩下的那个终端界面上仍显示「串口就绪」，却再也收不到字节，看起来正是「通讯不稳定」。
  键改为 `peer#序号`。`pump_subscriber` 另补异常保护：发送失败会结束该协程并记 warning，
  此前是静默终止，而这个 task 在 endpoint 的 finally 里被 await，异常还会从那里冒出来。
  该文件此前**零测试**；新增 8 个用例，其中 4 个在把命名临时退回旧写法后会失败（已验证）。
  注意：跨 `TestClient` 的两个 websocket 会各自带一个 portal，关掉一个会连带销毁桥所在的
  事件循环，所以「关一个、另一个仍收得到」只能在桥层测，不能靠两个 TestClient 会话。
- **文件选择框内不显示文件名**（`web/src/components/ui.tsx`、`web/src/tokens.css`）。
  原因是重置逻辑而非浏览器缺陷：为了支持同一文件重复选择，选中后立刻
  `event.target.value = ''`，原生控件随之回到占位文案，于是页面上呈现出
  「框内写着未选择文件、文件名跟在后面」——这是该设计下必然可见的结果，不是样式失误。
  现在原生 input 透明地铺在自绘控件之上（`position:absolute; inset:0; opacity:0; z-index:1`）：
  它仍是点击落点与文件来源，可见部分全部自绘，文件名因此落在框内。
  `z-index` 是显式写死的：自绘层是 flex 容器（CSS 归类为 block-level），
  叠放次序不值得留给绘制顺序去推断。focus 环画在自绘层上（相邻兄弟选择器），
  因为 `opacity:0` 会把原生 outline 一起带走。`value` 重置保留，两处调用方
  （新建实例窗口、插件中心）同时受益。
- **资源占用与串口快照在实例停止后仍显示旧信息**（`web/src/layout/Inspector.tsx`、
  `web/src/hooks/queries.ts`）。轮询没有终止条件：react-query v5 的 `refetchInterval`
  定时器与错误状态无关，无条件 `setInterval`，所以 404 每两秒打两次（全局 `retry: 1` 再翻倍）。
  现在 `refetchInterval` 改为函数形式，`state === 'gone'` 即停；串口快照在拿到后停
  （它是运行结束时落盘的一次性产物）。停止成功后 `removeQueries` 掉该实例的资源缓存
  （失效会再发一次已经没有实例可问的请求），并在它正是当前 pin 时清 pin。
  检查器改为顶层读一次 stats 交给两个面板复用。串口快照**保留**并标注「已归档」：
  它是失败归因要读的证据，删掉才是倒退，要改的是它自称是什么。

### 变更

- **新建实例成功后跳转到实例信息页**（`web/src/components/LaunchDialog.tsx`、
  `web/src/pages/InstanceDetail.tsx`）。原先只把结论留在窗口里并给一个链接。
  跳转**只能**发生在接口返回之后：服务端在 `emulate_firmware` 返回**之后**才调
  `_remember` 注册实例（`src/iris/api/server.py:523-533`），按下去就跳会落到一个
  声称「未托管」的页面上。结论随 router 的 location state 一起过去——
  启动耗时、解包统计、命中规则这些数据服务端并不保留，只此一份。
  刷新后 state 消失属预期，页面不依赖它也能完整工作。

### 文档

- 记录本次两条「不做什么」的理由：`GET /api/v1/console/{iid}` **不**补 `require_owned`
  ——运行历史（`/api/v1/runs`）本就是全局可见的，而 `emulation_run` 没有 `client_id` 列，
  按活跃表补校验会让停止后的快照彻底读不到。
- 第五条的 `lookup webTimeout failed` 已定位为固件自带 goahead 的一次性启动告警
  （`libgo.so` 里的 `lookup %s failed` 格式串与 `webTimeout` action 名，
  10 个实例的串口快照各出现 1 次），与 IRIS 链路无关；用户看到的「重复」来自上面那条
  前端缺陷。

## [0.3.21] - 2026-10-05

四条反馈。核心是**让规则库从只读清单变成可扩展的接口**，并把两份文档归回 `docs/`。

### 新增

- **外部规则插件可上传安装与卸载**（新增 `src/iris/api/plugins.py`；
  `src/iris/config.py`、`src/iris/rules/engine.py`、`src/iris/emulate/auto.py`、
  `src/iris/cli.py`、`src/iris/api/web_data.py`、`src/iris/api/web_app.py`；
  `web/src/pages/Plugins.tsx`、`web/src/lib/api.ts`、`web/src/lib/types.ts`、
  `web/src/hooks/queries.ts`；新增 `tests/test_api_plugins.py`）。
  插件落在 `<iris_home>/plugins/`（`Settings.plugin_dir`），与 git 跟踪的 `rules/` 分开：
  内置目录在源码树里，往里写要么污染仓库要么直接失败，而 `iris_home` 是本项目里
  唯一为「用户产生的数据」准备的地方，副作用是 `IRIS_IRIS_HOME` 会把已装插件一起带走，
  隔离验证因此只需改这一个变量。引擎经 `load_all_rules(settings.effective_rules_dirs)`
  读两个目录，顺序即优先级；`Rule` 新增 `source_file`/`source_dir`，因为唯一可靠的归属是
  「从哪个文件读出来的」，靠 id 反推文件会在 id 与文件名不一致时静默丢规则。
  运行期若 id 冲突（有人手工塞文件），先到者保留、后到者记 warning 跳过。
  界面：`POST /api/v1/plugins`（multipart，有界流式读取，超限 413）与
  `DELETE /api/v1/plugins/{name}`，均挂 `Caller` 依赖；插件卡片标注来源，
  只有外部的可卸载——卸载键放在详情窗口而非卡片上，因为卡片是 `<button>`，
  按钮里再放按钮会被浏览器整个丢掉。
  **上传即安装，不接受「装上了但不干活」**：引擎对不认识的键是「记告警并忽略」，
  所以安装器在校验语法之后还要落盘重新加载一次，**有任何告警就删文件并把告警原文退回**。
  一份把 `file_glob` 拼成 `fil_glob` 的文档如果只做语法校验会被报为成功，
  而它的条件永远不会被求值。
- **插件开发文档**（新增 `docs/09-规则插件开发指南.md`）。
  可用键逐个说明（含 `within` / `executable` 的修饰对象、`etc/` → `etc_ro/` 自动回退、
  `all`/`any` 的范围合并规则）、安装校验链 11 步、动作四种的完整参数、排错对照表。
  两处反直觉的事实按实现写实：`stage` **当前只记录与展示，不参与任何筛选**；
  `file_glob` 单独使用时恒成立，是否命中体现在作用范围上。

### 修复

- **嵌套条件里的错键不再静默失效**（`src/iris/rules/engine.py`）。
  `_unknown_keys` 原先只检查 `detect`/`actions` 列表的顶层元素，不进 `all`/`any` 子条件。
  而子条件与顶层条件由同一个 `_evaluate` 求值、只认同一批键，所以
  `all:` 里把 `file_glob` 拼成 `fil_glob` 会让那个子条件永久为假，且**任何地方都不记一条日志**——
  一条「需要 inittab 存在**且**装了守护进程」的规则就这样静静永不触发。
  现在递归下去，路径带上 `detect all any` 这样的来源标记。内置 6 条规则实测零新告警。
- **插件目录缺失不再被当成「没有任何规则」**（`src/iris/emulate/auto.py`）。
  两处调用点的守卫从 `if rules_dir.is_dir()` 改为「任一目录存在」，
  否则一个只装了插件、没带内置规则目录的部署会整段跳过规则应用。
- **插件移除拒绝路径穿越，而不是归约后删掉另一个文件**（`src/iris/api/plugins.py`）。
  `Path("../../rules/x.yaml").name` 是 `x.yaml`；沿用安装器的做法，先检查名字
  归约前后是否一致，不一致就拒绝。否则一次 DELETE 会静默删掉插件目录下的同名文件并返回 200。

### 变更

- **「设置」的令牌功能键移到输入框下方**（`web/src/components/SettingsSheet.tsx`）。
  此前输入框与「显示 / 复制 / 清除」并排（`flex items-end`），380px 的列宽下输入框只剩
  约 200px，粘贴一个令牌要横向滚动。堆叠后这一列里最宽的东西就是用户输入的东西。
- **插件卡片移除「完整信息」字样，保留箭头与整卡可点**（`web/src/pages/Plugins.tsx`）。
  角落的「完整信息 →」是在给一个整张卡片已经是按钮的控件写标签；箭头留下，
  继续承担「这条边后面还有东西」的方向提示，卡片的 `aria-label` 承担同一件事。
- **`docx/` 两份文档迁入 `docs/`，目录取消**：`AI值守与稳定性治理.md` → `docs/10-AI值守与稳定性治理.md`，
  `免安装使用指南.md` → `docs/11-免安装使用指南.md`，README 目录树与文档索引同步更新。
  本文件历史条目里的 `docx/` 路径保持原样——那些条目记录的是当时发生的迁入动作。

## [0.3.20] - 2026-10-05

四条界面反馈。核心是**承认三件此前被当成小事的事：卡片可以是一扇门、侧栏不该有第二种状态、
设置本来就不是一页**。

### 修复

- **插件卡片等高，完整描述点击可见**（`web/src/pages/Plugins.tsx`）。
  此前卡片描述全文渲染，长的三四行、短的一两行，网格高度由最长的一条决定，
  于是同一行里相邻卡片能差两行高。现在用 CSS `line-clamp-2` 截断：按**渲染行数**而不是
  字符数截，第二行永远是满的，字号与密度变化都不会让省略号落错位置；完整文本留在
  `title` 属性里。**整张卡片是按钮**（`aria-label="查看 … 的完整信息"`），点击开详情窗口，
  里面有完整描述、全部匹配条件、修复动作、修复后校验、加载告警原文，以及记账数的口径说明。
  角落不放「详情」按钮：在一张预览已经占了两行的卡片上，它要和预览抢注意力，
  而卡片本身已经是一个可读的整体。
- **侧栏不再有收起态**（`web/src/layout/Sidebar.tsx`、`web/src/layout/Shell.tsx`、
  `web/src/store/ui.ts`）。移除收起按钮、「展开侧栏」入口、`Ctrl/Cmd+B` 快捷键，
  以及 store 里的 `sidebarCollapsed`/`toggleSidebar`。侧栏恒显示，宽度仍可由拖拽手柄
  在 220–360 之间调。理由不是「收起不好看」：侧栏里每一项都是发起动作前的入口，
  收起等于把它们藏起来，而藏起来的那份收益只是主区多出 264px。
- **设置改为右下角浮层面板**（新增 `web/src/components/SettingsSheet.tsx`，
  删除 `web/src/pages/Settings.tsx`）。此前设置是一整页独立界面，而参考示范里
  设置是**右下角 380px 的浮层**：非模态、点页面任意处关闭、按 Esc 关闭，
  不夺走主区也不改变滚动位置。现在照此实现，分三个分节（外观 / 有效配置 / API 令牌），
  外观分节含「当前主题 · 名称」行、12 主题网格、密度与字体选择、动效开关。
  Esc 用 `window` 上的捕获阶段监听并 `stopPropagation`，因此它先于详情窗口的
  document 捕获生效——两层浮层叠加时关掉的是上面那层。
  触发点从路由改为 store 里的 `settingsOpen`：底栏设置按钮、命令面板「设置与主题」
  （已从「导航」组移入「动作」组）、总览令牌错误态的「打开设置填入令牌」。
  `/settings` 路由保留为一个薄壳，只为接住旧书签——打开浮层后立即重定向回总览，
  且**不带 cleanup**，否则重定向本身会把刚打开的浮层关掉。
- **界面中文描述句末不再有中文句号**（8 个前端文件 + 2 处后端下发文案）。
  共 30 处，其中多处是**句中**句号（例如「没有记录到失败信号。一次失败的运行通常有多个叠加原因…」），
  只删字符会把两句话粘成一个病句，因此按语义改写为冒号或分号。
  后端只有两处是真下发到界面的文案（`_LINK_DETAIL_NOTE` 与 `denominator_note`），
  一并清理；`src/` 下其余 120 余处都在 docstring 与注释里，不进界面，不动。
  `web/src` 现已零残留。

## [0.3.19] - 2026-10-05

四条界面反馈。核心不是样式，是**一个此前没被承认的事实：全站的下拉都是操作系统的**。

### 修复

- **下拉框自绘，替换原生 `<select>`**（`web/src/components/ui.tsx`）。
  原生 `<select>` 的展开列表由操作系统绘制——系统配色、系统字体、系统行高、
  系统高亮，任何样式表都够不着它。`appearance: none` 只能让**收起态**变成主题色，
  展开态仍是系统面板，这正是「选项卡设计不美观（原生）」那条反馈的成因。
  新的 `Select` 是一个 button 触发器 + portal 到 `document.body` 的 listbox：
  按触发器的 `getBoundingClientRect()` 定位，下方空间不足时向上翻转，滚动或缩放时重定位。
  行为对齐原生那一套：点击/Enter/Space/↓ 开列表，↑↓/Home/End/PageUp/PageDown 移动高亮
  且首尾循环、跳过禁用项，Enter/Space 提交，Esc 关闭且**不提交**，Tab 关闭并让焦点继续，
  指针悬停与键盘高亮是同一个信号。焦点始终留在触发器上，高亮项用 `aria-activedescendant` 指名。
  Esc 事件被 `stopPropagation` 拦下——它在新建实例窗口里，那里的 Esc 属于窗口本身，
  「选完这一项」和「关掉整个窗口」不该是同一个答案。
  三个调用点全部改用新接口（`web/src/components/LaunchDialog.tsx`、
  `web/src/pages/Instances.tsx` 的架构与失败类型筛选）；`web/src/tokens.css` 里
  `.field-select` 与全站最后一处原生 `<select>` 一起移除。
- **新建实例窗口的字段行严格水平对齐**（`web/src/components/LaunchDialog.tsx`、
  `web/src/tokens.css`）。此前是 `flex flex-wrap items-end`，两个缺陷叠加：
  四列控件 624px 加间距 648px 超过弹窗 body 的 632px，于是**「启动超时」被挤到第二行**；
  即使不换行，`items-end` 也不可能对齐，因为带 hint 的字段是三行、不带的是两行，
  按底部对齐反而把有 hint 的控件整体抬高。现在是一个 grid：label、控件、hint 是三条行轨，
  每个字段 `grid-row: 1 / span 3` 全部跨过这三条轨（subgrid，附 `repeat(3, auto)` 回退），
  有没有 hint 都落在同一基线上；首列 `minmax(0, 1fr)`，不换行。
  按内容分 3 列与 4 列两套模板，因为架构框只在两条上传路由出现——
  一套模板会让「宿主端口」在切换来源时横向滑动。弹窗宽度从 `max-w-2xl` 放宽到 `max-w-3xl`。
- **侧栏顶部移除搜索框与 IRIS 标识**（`web/src/layout/Sidebar.tsx`、
  `web/src/layout/Shell.tsx`）。搜索入口此前在两处并存（侧栏「搜索与跳转」与顶栏
  「命令面板」都是 Ctrl+K），品牌字标也在顶栏与侧栏各一份。现在侧栏第一个控件就是
  「新建实例」，搜索统一由顶栏「命令面板」承担（图标从 `CircleDot` 换成 `Command`，
  与文案一致）；`Sidebar` 的 `onOpenPalette` prop 一并删除。

### 新增

- `Select` 的 `options` 改为 `{value, label, hint?, placeholder?, disabled?}` 数组。
  失败类型筛选项因此能同时显示中文名与 API 用的代码（`二层：ARP 无应答` ↔ `link-no-arp`），
  勾选其一不必猜选完会传回什么。`placeholder` 标记「这是提示不是已选值」，
  未选择时以 `--text-faint` 显示，避免表单看起来已经答完。

### 诚实记录

- **隔离变量的真名是 `IRIS_IRIS_HOME`，`IRIS_HOME` 从来不是配置项。** 本轮做端到端验证时
  又一次用 `IRIS_HOME=<临时目录>` 起了 `iris web`，`/api/v1/config` 读回的
  `database_url` 仍是 `sqlite:///iris-home/iris.db`——服务连的是真实库，所幸该实例
  `read_only: true` 且全程只发 GET，事后核对真实库 sha/mtime/五张表行数均未变。
  0.3.18 那次事故的真实原因至此才说清：不是「变量管不到库路径」，而是变量名就写错了，
  两个缺陷各自都足以让「改一个变量就是全隔离」看起来成立。CHANGELOG 0.3.18 段落里
  同样写错的「只设 `IRIS_HOME`」已一并更正。

## [0.3.18] - 2026-10-05

配置隔离的根因修复。上一版加了「清空仿真记录」，我在端到端验证它时把真实语料库清空了：
设 `IRIS_HOME=<临时目录>` 以为万事隔离，但 `database_url` 是独立字段、默认值硬编码
`sqlite:///iris-home/iris.db`，语料与 scratch 搬走了、数据库没搬。清空前留的副本恢复了
81 条记录中的 80 条（`emulation_run` 1–81 连续、`failure_profile` 187 条、`repair_action` 17 条、
`image` 10 条、`brand` 7 条均未受影响，`integrity_check` 为 ok），**run 82 及其失败画像永久丢失**。
本版修掉的是那个让这看起来像隔离的默认值，而不是补一句操作提醒。

### 修复

- **`IRIS_DATABASE_URL` 未设置时由 `IRIS_IRIS_HOME` 推导**（`src/iris/config.py`）。
  `database_url` 的默认值改为空字符串，由 `model_validator` 在取不到值时拼出
  `$IRIS_IRIS_HOME/iris.db`；显式设置仍然优先，PostgreSQL 部署与自定义文件路径都不受影响。
  默认安装的行为逐字节不变（`Settings().database_url` 仍为 `sqlite:///iris-home/iris.db`），
  变的只有「改了 home 就真的完整隔离」这一条。同一份配置里两个设置各自命名同一处存储、
  却各自独立读取，是最坏的一种默认形状：把其中一个指向临时目录看起来像全隔离，
  而下一个命令写下去的仍然是真身。

### 新增

- **`tests/test_config_isolation.py`**（8 例）。四个分组各钉一个方向：默认值与文档一致
  且相对工作目录、搬迁 home 时数据库跟着搬、scratch 与数据库指向同一处 home、
  显式 URL（PostgreSQL 与自定义路径）不被推导顶掉。CLI 端到端那两例走真实子进程并
  **只设 `IRIS_IRIS_HOME`**（`IRIS_DATABASE_URL` 显式从环境里摘掉），断言 `iris db init`
  建出的文件落在临时 home 内、且打印的路径就是那个文件——单元构造证明不了
  `IRIS_IRIS_HOME` 有没有真的到达应用层，而这正是当初失效的那一环。
  两次变异验证均确认变红后恢复：把默认值改回硬编码字面量 → 4 例红（两个跟随用例 +
  两个 CLI 用例），去掉「仅在空时推导」的条件 → 2 例红（显式配置组）。

### 诚实记录

- 这次清空不是功能缺陷：`clear_runs` 按设计工作、子表零残留、幂等，测试与守卫都覆盖到了。
  缺陷在验证手段上——破坏性端到端验证没有先证明目标是隔离副本。
  仓库里原本只有 `tests/conftest.py` 一道 session 级隔离，它管得住 pytest，管不住手工
  起服务的验证；现在配置层不再允许「看起来隔离了」，两者是同一条防线的两端。

## [0.3.17] - 2026-10-05

工作台信息架构重排，并把仿真记录变成可读可删的。上一版把宿主读数放上了总览页，
但「新建实例」仍是一块钉在总览页上的面板——换句话说，要启动一次仿真就得先回到
总览；而「清理记录」根本不存在，历史表只会越长，跑了同一固件六十次之后每个累计
数字都读不出意义。本版把启动收进窗口、把记录拆成可读与可删两块，并把「这个构建
是什么」的两块内容从设置页里搬出来独立成页。

### 新增

- **启动仿真改为居中窗口**（`web/src/components/Modal.tsx` 新增，
  `web/src/components/LaunchDialog.tsx` 由原 `LaunchPanel.tsx` 改来）。在当前界面
  打开，不跳转：侧栏按钮、命令面板、实例页空状态三处触发器都能开同一个窗口
  （`useUiStore.launchOpen`）。窗口带焦点陷阱、Esc 关闭、关闭后焦点归位。
  启动判定留在窗口里不自动关闭——「Web 不可达 · link-no-arp」正是要读的那句话。
- **`DELETE /api/v1/runs/{run_id}` 与 `DELETE /api/v1/runs`**。删一条与清空全部，
  都带 `Caller` 依赖。前者不存在时回 404（与 emulate 路由同理由，不做成 id 存在性
  探针），后者回 `{"removed": n}` 计数而非布尔，因为「本来就没有」和「删除失败」
  是两种状态。`src/iris/db/runs.py` 的 `delete_run` / `clear_runs` **显式先删
  `failure_profile` 与 `repair_action` 子表**：两张表都声明了 `ondelete="CASCADE"`，
  但本引擎从不发 `PRAGMA foreign_keys=ON`，级联只是声明不是行为；靠级联会留下孤儿行，
  而 `run_stats` 统计的是每一行 `failure_profile`，孤儿会让总览的失败计数在运行早已
  不存在之后继续上涨。已登记的固件语料不受影响。
- **`GET /api/v1/rules`**（`web/src/pages/Plugins.tsx` 插件中心）。直接经
  `iris.rules.engine.load_rules` 读 `rules/*.yaml`，不维护第二份清单：`rules/` 里
  加一条就出现，删一条就消失。**账本数字叫 `applied`/`promoted` 而非「命中次数」**
  ——`repair_action` 记的是「已执行并记下账的修复」，规则引擎逐次运行的对账报告并
  不留存，两个数字不是一回事，页面如实这么写。规则由 IRIS 自带，页面用一句话说明
  **当前不支持从外部安装**，并因此不提供上传入口。
- **工作策略页**（`web/src/pages/WorkPolicy.tsx`）。能力矩阵（连同判定依据）从
  设置页搬来，另加创新特性与已知限制。放在一起的理由是这三块回答的是同一个问题的
  三面：这个构建是什么、因此能做什么、因此不能做什么。
- **历史记录行可点开详情窗口**（`web/src/components/RunRecord.tsx`）。点表格任意
  一行弹出该次运行当时记录的全部信息：结论、运行参数（含 guest IP、HTTP/ICMP 探通
  耗时）、四层链路逐层状态与依据、失败画像（含日志指纹与明细 JSON）、修复账本。
  链路结论由随运行落盘的探测证据**重建而非重测**——十分钟前启动过的 guest 已经
  没法再探测一次，在历史判定下摆一个新读数是在比较两件不同的东西。
- **历史记录删除交互**（`web/src/pages/Instances.tsx`）。每行一个删除按钮删单条，
  面板工具栏一个「清空全部」，两者都走确认窗口，且确认文字分两种：删单条说的是
  这一条，清空说的还包括「总览的可达率是全库累计口径，删完会变」。

### 移除

- **总览页的「环境状态」与「新建实例」区块**。宿主读数移入侧栏（那里本来就是它
  的位置，也是唯一一处），启动移入窗口。总览页现在只回答「它做过什么、做得多好、
  为什么失败、下一步去哪」。
- **设置页的「能力矩阵与判定依据」与「已知限制」**，以及总览页重复的能力矩阵面板。
  同一个能力矩阵曾出现在三个地方，那意味着有三个地方会忘记更新。
- **侧栏底部的能力清单卡片**，以及顶栏的「关于本构建」文字链接。侧栏每一项都是
  关于工作本身的问题，「换个主题」不是其中之一；设置因此移到**底栏最右侧的纯图标**。
- **`/#launch` 锚点**（连同 `Shell.tsx` 里的手动滚动逻辑）。启动不再是一处页面内的
  目标，锚点无处可指。

### 修复

- **设置入口与工作入口混在侧栏**：设置原是侧栏第三项，与「总览」「实例记录」并列。
  现在侧栏四项全是工作相关的页面（总览 / 实例记录 / 插件中心 / 工作策略），
  设置作为底栏最右侧的纯图标，图标带 `title` 与 `aria-label`，当前页用
  `aria-current` 标出而非填色方块——没有文字的按钮，填色状态读不出来。

### 测试

- `tests/test_run_recording.py` 新增 `TestClearingHistory`（8 例）：单条删除清干净
  自己的画像与账本；删除后失败直方图停止计入该运行；不存在的 id 回 `False` 而非
  静默成功；清空全部计数准确且空库回 `0`；子表零残留；删一条不影响别的运行的证据；
  固件语料在清空后仍在。
- `tests/test_web_app.py` 新增 `TestDeletingHistory`（6 例）与 `TestRulePlugins`
  （5 例），含两条删除路由都挂 `Caller` 依赖的路由表断言，以及 `rules/` 不可读时
  回空列表而非 500。
- 守卫做过变异验证：临时移除子表删除、把过滤列从 `run_id` 改成 `id`、移除清空路径
  的子表删除，三次都确认对应用例变红后恢复。

## [0.3.16] - 2026-10-05

宿主环境读数与信息架构调整。上一版把上传启动补上了，但总览页仍然只有历史统计——
一个只有累计数字的工作台看起来和 mock 数据没有区别，因为读者无从判断这些数字
是在什么机器上跑出来的。本版让宿主自己报出来，并按「这台机器现在能做什么、
它做过什么、怎么开始做、为什么失败」重排版面。

### 新增

- **`GET /api/v1/system`**（`src/iris/api/host_metrics.py`）。请求时现采的宿主
  资源读数：`cpu_pct`、`cpu_cores`、`mem_used_mb`、`mem_total_mb`、`mem_pct`、
  `uptime_sec`、`scratch_free_gb`，外加服务自己的 `version` 与 `uptime_sec`。
  **没有任何缓存的历史读数**——数值在请求时从 `psutil` 取，取不到就是 `null`。
- **总览页「环境状态」层**（`web/src/pages/Dashboard.tsx`）。CPU 与内存两条量表
  加一列数字：内存占用、暂存盘剩余、宿主运行时长、服务运行时长。放在统计卡
  之前，因为本页每个数字都是「这台机器做过的工作」的断言，没有环境读数时
  34% 可达率只是一句话。
- **侧栏「性能与内存」**（`web/src/layout/Sidebar.tsx`）。CPU 与内存两条窄量表，
  与总览页共用同一个 `useSystem()` 查询，不会出现两个页面对不上。
- **`Meter` 组件**（`web/src/components/ui.tsx`）。`value` 为 `null` 时不画填充、
  数字显示破折号，并带 `aria-valuetext="未测量"`——一条空的进度槽看起来就是 0%。

### 修复

- **总览页把「未测量」写成 0**：四张统计卡用 `String(data.total ?? 0)`，而服务端
  `null` 的含义是「这张表读不出来」，渲染成 `0` 等于宣称数据库是空的。
  改为直接用服务端值，由 `available` 闸门决定是否出卡。
- **`/#launch` 锚点无效**（`web/src/layout/Shell.tsx`）。浏览器自带锚点滚动滚的是
  文档，而本项目的文档从不滚动——`main` 的子节点才是溢出容器，于是 URL 变了、
  页面没动。改为在滚动容器内查 `#launch` 并 `scrollIntoView`。

### 调整

- **实例创建从「实例」页移到总览页**（`web/src/components/LaunchPanel.tsx`）。
  创建一次仿真是本产品的主要动作，而 `/instances` 回答的是「本服务跑过什么、
  结果如何」——两张表挤在一个页面里，创建表单把记录挤到折叠线以下。总览页的顺序
  是环境状态 → 统计 → 新建实例 → 证据 → 快速开始。空状态与命令面板的文案与入口
  同步跟上。
- **侧栏贴合范例结构**：保留顶部搜索框（它是最快的跳转路径，为一个动作按钮把它
  下移等于拿三键快捷方式换一次点击），其下是「新建实例」按钮，再是导航。
  「运行中」改称「最近实例」，与命令面板的分组名一致。
- **`formatBytes` 收敛到 `web/src/lib/format.ts`**：原本 `FileInput` 与上传结果行
  各有一份，同一个文件会印成 `12 GiB` 和 `12.0 GiB` 两处。

### 诚实边界

- **CPU 占比首次采样是 `null` 而非 0**：占比需要两次 `cpu_times()` 差值，首帧没有
  窗口。构造函数里预热会在几毫秒的窗口上算出「0%」，而那恰恰读起来像「空闲」。
- **`psutil` 现为显式依赖**：宿主上原本已装但未在 `pyproject.toml` 声明。不用它而用
  `os.getloadavg` 会把功能钉死在类 Unix 系统上（Windows 不可用，且分母是 1 分钟
  平均负载而非瞬时占比，语义也不同）；wmic / vmstat 则是把功能钉死在两个系统。
- **磁盘只测暂存目录所在卷的剩余空间**：每个容器的 rootfs 归档都落在这里，这才是
  会满的盘。宿主 home 目录不是。

### 兼容

- 数据库 schema 未变更。新增环境变量：无。`scratch_dir` 不存在时回退到其父目录取卷。

## [0.3.15] - 2026-10-05

上传启动与一轮界面校准。上一版把工作台立起来了，但「启动一次仿真」只认暂存目录里
`iris extract` 已经解好的 rootfs——手里只有一个 tar 或一个厂商 bin 的人，页面上没有路
可走，只能回到命令行。本版补上这两条路，并按评审意见重做了背景、控件圆角、文字层级
与导航。

### 新增

- **`POST /api/v1/emulate/upload`**（`src/iris/api/server.py`）。multipart 上传
  rootfs 归档或厂商固件镜像并直接启动，取代「先把文件放到暂存目录」这一步。
  `kind` 取 `auto`（按 tar 魔法判别）/ `rootfs` / `firmware`，`arch` 留空则自动判定，
  另有 `port` 与 `timeout`。iid 由内容 md5 派生，响应在 `EmulateResponse` 之外追加
  `source`、`name`、`host_port`、`members`、`total_bytes`、`links_created`、
  `links_skipped`、`rejected_members`、`matched_rule_ids` 与 `notes`。
- **安全解包**（`src/iris/extract/rootfs_archive.py`）。拒绝 `..`、绝对路径、Windows
  盘符；`_contained()` 对解析后路径再判一次是否仍在目标树内；软链重锚到树内，
  逃逸或悬空则跳过并计数；拒绝 FIFO/设备等特殊文件；成员数（20 万）与解包总字节
  （2 GiB）双上限。根前缀自动识别并剥除，`dest_dir` 先清空再落盘而不是与旧内容合并。
- **前端启动面板三来源**（`web/src/pages/Instances.tsx`）。已提取 rootfs / rootfs
  归档 / 厂商 bin 三个分段共用同一组端口与超时输入；上传分支显式声明 `kind`，
  让选错的文件按格式报错而不是被静默解到另一棵树上。结果行会显示实际发布的
  宿主端口、被跳过的软链数与被拒成员数——guest 起不来时，这是第一个该看的地方。
- **`FileInput` / `Segmented` 组件与 `.field` / `.segment` / `.nav-item` /
  `.nav-card` 样式**：搜索框、数值框、下拉与文件选择器现在共用一个控件壳，
  圆角、边框、聚焦环与数字框的步进器去留都只在一处决定。

### 修复

- **`host_port=0` 被直接交给 `docker create -p 0:0`**：不发布任何可用端口，
  于是启动会耗尽整个 boot 超时去探测一个从未开放的端口，最后报 `web-unreachable`
  并把矛头指向 guest。新增 `_resolve_port()` 统一处理，`/emulate`、`/pipeline`
  与 `/emulate/upload` 三条启动路由全部经过它。
- **`prepare_from_rootfs` 硬编码 `dry_run=True`**：上传路径的规则修复只报告不落盘，
  guest 拿到的是未修复的 rootfs。新增 `dry_run_rules` 参数（默认 True，CLI 行为
  不变），上传路径传 False。

### 界面校准

- **去掉背景方格**：body 的 `--surface-grid` 图层与 `.panel-grid` 类整体移除——
  13 处 `--grid-line` 声明与其全部使用点一次清空，全库检索零残留——空状态改用
  虚线内嵌面板表达
- **圆角统一到 10px / 卡片 14px**，与范例界面一致；数字框的原生步进器一并去掉，
  它是让表单显得陈旧的主因之一。
- **文字层级按范例校准**：两套浅色主题的四级文字改为 `#111827` / `#374151` /
  `#6b7280` / `#9ca3af`。daylight 主题实测对比度 17.2:1、10.0:1、4.7:1、2.5:1
  （github-light 略高）——最弱一级只用于脚注与占位符，五处表头已从最弱一级
  提到次弱一级
- **选项卡改为主题化的分段控件**：实例页四个 tab 从下划线式改为圆角分段，
  选中态由填充色与边框承担，原先那种「只有一条 accent 下划线」的信号在浅色
  主题下几乎不可见。
- **左侧栏导航项与卡片窗口**：选中项改为填充面 + 3px accent 竖条，15% 透明度的
  accent 底色在浅色主题上会让当前项变成全页最难读的一行；「运行中」与「能力」
  两个区块改为带边框的内嵌卡片。
- **文案精简与去句末句号**：33 处句末标点清除，另改写 9 处过长或仍带句号的说明。

### 诚实边界

- **Windows 上无法重建软链**：NTFS 没有执行位与符号链接，创建软链需要开发者模式
  或提权 shell。解包时软链会被跳过并计数，`notes` 里逐条说明——rootfs 主要靠软链，
  缺失会直接导致启动失败，所以这条提示不是客套话。
- **上传启动没有进度可报**：三条启动路由都是同步阻塞到仿真结束，没有 job id 可轮询，
  页面只显示已等待秒数。一个什么都不报的进度条是带百分号的谎话。
- **实测只覆盖到失败路径**：用构造的 rootfs 归档走通了上传、解包、规则匹配、
  端口发布与四层探测，`success` 为 `false` 且 `error` 如实指出 boot hooks 未运行。
  真实固件的成功路径由 `tests/test_upload_launch.py` 用 recorder 覆盖。

### 兼容

- 数据库 schema 未变更。`python-multipart>=0.0.9` 早已在依赖里，本版只是第一次
  真正使用它。
- 新增环境变量：无。上传体积受既有的 `api_max_upload_mb` 约束。

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
  本库当前 81 条记录中 4 条可还原（数字随历史清理变化，取自 iris-home/iris.db 实测）。
  全通的运行不探测，
  探测不可用的运行不记证据；
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
