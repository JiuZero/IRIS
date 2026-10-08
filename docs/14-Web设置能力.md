# 14 · Web 设置能力：把「只能手改 .env」变成面板可写

## 1. 上一轮留下的是什么

0.3.29 落地了 LLM 层，六个字段随之进入 `Settings`：`llm_base_url`、`llm_api_key`、
`llm_model`、`llm_timeout_sec`、`llm_max_retries`、`llm_max_context_chars`。它们有 docstring、
有默认值、有单测，唯独没有一条路能让人在不打开编辑器的情况下改。

盘查的结果比「缺功能」更难看：仓库里**根本不存在任何配置写入路径**。

- `GET /api/v1/config` 的 `read_only: True` 是硬编码常量（`web_app.py:597`），
  它报告的是一个不打算改变的事实；
- `SettingsSheet.tsx:32-33` 的注释写着「能改运行中服务环境的设置面板是没法测试的面板」
  ——在只有 `.env` 的年代这句话是对的；
- 全仓库没有 `set_settings`、没有 `.env` 写入、没有配置类 PUT 路由。

于是那六个字段的实际使用方式是：重启、编辑 `.env`、重启、看 `GET /api/v1/ai/status`。
对一个**已经跑起来了、就等着你填端点**的评委来说，这四步里每一步都在让人怀疑是不是自己装错了。

## 2. 一句话决策

**把配置项直接加进现有的 `SettingsSheet.tsx` 抽屉，写进 IRIS 自己的 `iris-home/settings.json`，
写盘后诚实标注「需重启」。**

不是新页面。设置是对「你正在看的这页」的提问，把它做成路由就意味着答案和页面各隔一次点击。
也不是热重载。热重载要动的是那个单例，而单例的用途就是「一个进程一套配置」。

## 3. 三条让「浏览器能写配置」成立的安全性质

### 3.1 写的不是 `.env`，是 IRIS 自己的文件

`.env` 是运维的文件，和 CLI 共用，一个写歪的键会弄坏镜像名或数据库 URL。
新文件叫 `iris-home/settings.json`，只装下面那张表里的字段——写错的爆炸半径就是设置面板本身。

落地在 `src/iris/config_store.py`。`EDITABLE_SETTINGS` 是一张十行的
`EditableSetting` 表（key / label / kind / help / min / max / placeholder），
**「面板能改什么」在整个仓库只有这一个定义**：API 从它渲染表单、拒绝表外键、
前端 `types.ts` 里不复制这份名单。表按子系统分组而非字母序——用面板的人在找「那个 LLM 的」，
字母序会把六个字段拆到三个位置。

### 3.2 覆盖层在环境变量之上，不是在之下

`Settings.settings_customise_sources` 的优先级变成：

```
init > iris-home/settings.json > IRIS_* 环境变量 > .env > 默认值
```

面板**压过**环境变量。理由是这两者是两种不同性质的陈述：启动脚本里的 `IRIS_*` 说的是
「这个部署是什么」，面板里存的说的是「这个人刚选了什么」，后者更新。
它留在 `init_settings` 之下，所以测试里 `Settings(llm_model=...)` 依然算数。

落点是一个 pydantic source（`_OverlaySource`）而不是构造后的 `model_validator`——
区别在于「这个字段来自这里」与「不管覆盖层说什么这个字段就是这个值」。

> **踩坑**：初版在 `_OverlaySource` 内部用 `Settings(skip_overlay=True)` 定位 `iris_home`，
> 于是 `settings_customise_sources` 再次触发 `_OverlaySource`，无限递归。正解是独立的探针类
> `_HomeProbe`：同样的 `_SETTINGS_CONFIG`，但**没有** overlay source，所以不可能递归。

覆盖层顺带定了另一个行为：**文件损坏不拦启动，降级为 warning 并按环境变量启动**。
仿真本身不依赖这些字段，为一个坏掉的偏好文件拒绝开机，等于把面板的一次误操作变成一次故障。

### 3.3 密钥只写不读

`llm_api_key` 与 `api_token` 同一个约定：报告「是否已配置」，永不报告值。
`_setting_row` 里它的 `value` 恒为 `None`、事实走 `secret_configured`、
`pending_restart` 恒为 `False`——比较一个保存的密钥和运行中的密钥意味着读两个值，
而这个端点从不读凭据。

文件进 git 的话就是明文密钥，所以 `.gitignore` 加了 `iris-home/settings.json`
（和写盘用的 `.tmp`）。

## 4. 危险三项为什么不开

| 字段 | 不开的理由 |
|---|---|
| `api_token` | 改它的那个请求本身要过鉴权。能改它，就能用一个已知令牌的会话把自己锁在门外——包括那个用来改回去的端点。 |
| `iris_home` | 数据根。面板换掉它是在承诺一次迁移，而没有任何代码执行迁移；换完就是「语料不见了」。 |
| `database_url` | 同上，而且指向的是已经装了数据的库。 |

这三个键不在表里，所以 `normalise_patch` 与 `clear_settings_file` 都返回 422，
一次报出全部越界键（改一个字段一个来回比一次报全部更难受）。

## 5. 「需重启」这条线，本轮改过一次结论

初版把 `log_level` / `log_to_file` / `api_max_upload_mb` / `download_mirror`
列为「需重启」，其余的号称下次诊断就生效。理由是 LLM client 每次调用现造。

**这个理由是错的。** `get_settings()` 在 `config.py:263` 只被赋值一次，守卫是
`if _settings is None`，全仓库没有 reload。client 确实是每次现造，它**造自的那份
settings 不是每次重读的**。真实验证时 PUT 一个 LLM 字段返回 `restart_required: false`，
而紧接着 GET 显示该行 `pending_restart: true`——两句话互相打脸。

所以结论改成：**十项全都要重启**，并且 `RESTART_KEYS` 这个名单删掉了。

替代物是 `_needs_restart(keys, settings, stored)`，它问一个更有用的问题：
「下次启动解析出来的东西，和这个进程正在用的，是否不同」。只有一种情况能不加猜测地回答：
文件现在写的值恰好就是进程在用的值。保存 `llm_timeout_sec=60` 进一个本来就跑 60 的进程，
什么也没变，标「待重启」只会教人忽略这个徽章。

取消覆盖一律报「要重启」：下次启动回落到 `IRIS_*` 或默认值，而这两个都从手里的
settings 对象里读不出来——那个对象本身是通过**正在被删掉的**覆盖层解析出来的。

## 6. 三个动词，两个原因

`PUT`（部分保存）与 `DELETE`（取消覆盖）分开，而不是让 PUT 兼管后者。
「不再覆盖这个」和「设成某个值」是两句不同的话，折叠在一起需要一个表示「unset」的哨兵值，
而一个文本字段装不下这个值——`""` 是「端点留空 = 整个 LLM 层停用」，`None` 是 JSON 里没有这个键。

DELETE 的 `keys` 是**重复查询参数**（`?keys=a&keys=b`），不是逗号拼接：
服务端读的是 list，逗号拼过去的 `"a,b"` 会变成一个未知键 422。

## 7. 写盘要原子

同目录 `.tmp` + `os.replace`。写一半被打断留下的截断 JSON，下一次启动读不了，
而这个失败是隐性的，直到有人发现某个设置悄悄不生效了。

## 8. 测试

`tests/test_web_settings.py`，101 个用例，绝大多数钉在**拒绝**上而不是快乐路径上：
表外键 422（含危险三项）、越界数值、文本型数字、字符串型开关、枚举外的日志级别、
一次被拒的表单不能留下九个已写入的字段、损坏文件响亮报错、密钥不回流、原子写不留 `.tmp`。

`tests/test_web_app.py` 里删掉了 `assert body["read_only"] is True`——它把一个硬编码的
假声明当成了期望行为，正是本轮要消除的东西。

## 9. 真实运行复核

起真服务（`IRIS_IRIS_HOME` 指向临时目录，端口 8742–8744），不是 TestClient：

- GET 返回 10 行，`writable: true`，密钥 `value=None`；
- PUT 写盘、`os.replace` 生效、无 `.tmp` 残留；
- **保存与环境变量相同的值 → `restart_required: false`；保存不同值 → `true`**；
- **重启后十项全部生效，`pending` 全为 `False`**，`ai-guardian` 能力从 `planned` 变 `available`；
- 覆盖层压过 `IRIS_LLM_TIMEOUT_SEC=90`（保存的 45 胜出）；
- 危险三项 PUT/DELETE 均 422，无令牌 401，被拒的写入没有改动文件；
- 逗号拼接的 `keys=a,b` 被当作一个未知键拒绝。

复核完删掉临时 home——里面有明文密钥。

## 10. 前端

`SettingsSheet.tsx` 的配置组从只读重写为可编辑：按 `kind` 选控件（secret 用
`type="password"` 且永不预填、空值不提交）、脏项计数、保存/放弃按钮、每行一个
「取消覆盖」和「待重启」徽章。

一处对后端语义的追平：secret 行的「是否有覆盖」判据是 `secret_configured` 而不是 `stored`。
后者的 `stored` 是存在性标志、`value` 恒空，按值比较会报「无覆盖」——
于是一个密钥能从面板设进去，却再也删不掉。

面板的分组提示原写「启动参数与 .env 优先级更高」，而实际覆盖层在它们之上，一并改掉。

## 11. 已知边界

- 十个字段全都要重启，没有热重载（这是 `get_settings()` 单例的直接后果，不是偷懒）。
- 取消覆盖一律报「要重启」，即使回落值恰好等于运行值——那个判断需要知道 fallback，
  而手里的对象是通过被删掉的覆盖层解析的。
- 保存的密钥无法从面板读回，也因此无法判断「新旧是否相同」，比较在服务端完成、只回传布尔。