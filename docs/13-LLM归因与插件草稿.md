# 13 — LLM 归因与插件草稿

> 本文说明 0.3.29 新增的 LLM 层：**它解决什么问题、明确不做什么、怎么配、怎么用、
> 以及它的安全边界落在哪几道门上**。

---

## 1. 为什么加这一层

IRIS 目前的插件是**按型号特化**的。六个内置插件里有四个绑定了具体厂商的具体路径：

| 插件 | 特化到什么程度 |
|---|---|
| `shadow-jffs2-opt` | Tenda RP3 / TC3T14C 的 JFFS2 布局 |
| `generic-diag-crash-fix` | TES7002 的 diag SIGSEGV |
| `tenda-web-server-forced-start` | TES7002 的 goahead 启动时机 |
| `vendor-watchdog-monitor` | TES7002 aarch64 GPON 看门狗 |

而 `mtd-name-lookup-guard` 里写着一行明确的放弃声明：**「per-firmware 分区号无法安全
推断」**。这行注释就是问题本身——遇到一个没见过的固件，规则层的答案是「我不知道」，
而「我不知道」的维护成本随着厂商数线性增长。

这一层的目标因此不是替代规则层，而是**给规则层的「我不知道」一个出口，并把出口的
产出沉淀回规则层**。

---

## 2. 三条架构裁定（决定了下面所有行为）

### 2.1 规则层先试，命中就短路，不调模型

`POST /api/v1/ai/diagnose` 的第一步永远是 `AIHealthMonitor.recommend_recovery_action()`。
它给出建议就直接返回，`llm_used=false`。

这不只是省钱的优化，是设计本身：**降维护度的唯一机制是「沉淀回规则」**。如果每次失败
都问一次模型，那就不是「AI 归因」而是「每次失败收一次 API 费」，而「修复一旦被接受就
由规则层零成本接管」这个闭环也就没有意义了。

### 2.2 动作只有三个，不是五个

```
NONE                 只归因，无需动作
WEB_SERVER_RESTART   重启容器
DRAFT_PLUGIN         编写规则插件（人工确认后下次运行生效）
```

守护值守的其余动作（watchdog 恢复、资源清理、diag 禁用、web 诊断）**参数都由串口日志的
模式计数驱动**。而调 LLM 的前提恰恰是模式计数没给出建议——让模型提供这些参数，就成了
第二处「从更少的证据推导同一组参数」的地方。剩下的只有三个不需要参数的动作。

设计稿里还有一个 `params` 字段，已经删除：那是纯多余的攻击面。

### 2.3 执行通道的天花板不因换模型而变

运行期唯一可达的修复是**容器重启**。guest 的 rootfs 打包在 `image.raw` 里，
`docker exec` 触达不到。所以这一层给出的 `WEB_SERVER_RESTART` 建议不会让天花板抬高，
它在 `docs/10-AI值守与稳定性治理.md` 记录的天花板之内。

---

## 3. 安全边界：三道门，缺一不可

```
模型输出 PluginDraft
      │
      │ 门 1：写入 iris_home/ai-drafts/（与 plugin_dir 物理隔离）
      ↓    引擎只从 rules_dir + plugin_dir 加载，这里读不到
   插件页「AI 规则草案」Panel —— 显示 YAML 全文 + 来源 + 置信度
      │
      │ 门 2：人工点「接受并安装」
      ↓
   install_plugin() —— 与浏览器上传完全相同的校验链
      │  未知键 → 422 + 引擎原话；加载产生任何告警 → 拒绝
      │  磁盘上不留文件
      ↓
   iris_home/plugins/<rule_id>.yaml —— 下次运行生效
      │
      │ 门 3：记 repair_action(source="llm", promoted=True)
      ↓    这次故障下次由规则层零成本处理；本次动作留有账可查
```

三条断言在测试里逐条钉死（`tests/test_llm_drafts.py`）：

- 草稿目录不在 `settings.effective_rules_dirs` 里，且保存后 `plugins.list_plugins()`
  返回空——**两目录合并的实现会让这条直接变红**；
- 未知键的草案安装后 `plugins/` 目录根本不存在——**绕过 `install_plugin` 的实现会让这条变红**；
- 接受后 `repair_action` 有一行 `source=llm, promoted=True`。

被拒绝的草案会**继续列出**并标为「已拒绝」。「这里什么都没有」和「有人看过并说了不」
是关于同一次运行的两个不同事实，后者才是可审计的那一个。

---

## 4. 离线可用性是硬约束

端点是 OpenAI 兼容的 `/chat/completions`，`base_url` 可配，所以本地模型与托管模型
配置方式完全相同：

```bash
# 本地 ollama
export IRIS_LLM_BASE_URL=http://localhost:11434/v1
export IRIS_LLM_MODEL=qwen2.5-coder:14b

# 托管端点
export IRIS_LLM_BASE_URL=https://api.example.com/v1
export IRIS_LLM_API_KEY=sk-...
export IRIS_LLM_MODEL=gpt-4o-mini
```

| 配置项 | 默认值 | 作用 |
|---|---|---|
| `IRIS_LLM_BASE_URL` | 空 | OpenAI 兼容端点；空则整层 disabled |
| `IRIS_LLM_API_KEY` | 空 | 非空才发 `Authorization` 头 |
| `IRIS_LLM_MODEL` | 空 | 与 base_url **都**配置才启用 |
| `IRIS_LLM_TIMEOUT_SEC` | 60.0 | 单次请求超时 |
| `IRIS_LLM_MAX_RETRIES` | 1 | 传输错误与 5xx 的重试预算 |
| `IRIS_LLM_MAX_CONTEXT_CHARS` | 12000 | 串口日志尾部进 prompt 的字符上限 |

**未配置时整层 disabled，且绝不阻塞主仿真。** `GET /api/v1/ai/status` 报
`disabled`（两端都空）、`unreachable`（只有 base_url）或 `ready`；能力清单里
`ai-guardian` 一行在未配置时保持 `planned`，并写明「确定性自愈可用；LLM 归因未接入」——
把整行标成不可用会**低估已经能用的东西**。

状态端点只报「密钥是否配置」，**从不返回密钥本身**。这个端点喂的是页面，而页面是凭据
最容易失控落地的地方。

---

## 5. 怎么用

### 5.1 诊断（实例页，手动触发）

1. 实例列表 → 目标行 → **AI 诊断**
2. 窗口显示三种互斥的结果之一：
   - **规则层已给出建议**（绿色）——没调模型，`llm_used=false`
   - **模型已介入**（iris 色）——显示归因、建议动作、置信度、建议核查项
   - **模型调用失败 / LLM 层未启用**（黄/红）——显示具体原因

诊断**不执行任何动作**。重启建议不会自动重启；插件草案要人点「保存为草案」。

### 5.2 审核（插件页）

插件页新增「AI 规则草案」Panel：

- 每张卡片显示 rule_id、目标阶段、来源实例、模型置信度、状态徽章（待审/已接受/已拒绝）
- 点开是 Modal：**YAML 全文**（不是摘要——读不到原文的审核是橡皮图章）、归因说明、来源
- 「接受并安装」→ 走上传同款校验链；422 时原样显示引擎的抱怨
- 「拒绝」→ 删文档、留 meta 标 `rejected`

### 5.3 沉淀生效

接受后的规则在**下次运行**生效，不影响当前这次。同类故障下次由规则层零成本处理，
`repair_action` 里有 `source=llm, promoted=True` 的行可查。

---

## 6. 模型看到什么

prompt 由 `iris.llm.context.build_prompt()` 构造，纯函数、可单测。它拿到：

| 材料 | 取不到时 |
|---|---|
| 实例编号、架构 | 架构渲染成 `?` |
| 串口日志尾部（截断到上限，并声明是否还有更早内容） | 「未取到：串口日志不存在或不可读」 |
| 守护模式计数（`iris.monitor.ai_guardian` 的 PATTERNS） | 「无命中模式」 |
| Web 服务状态（按串口日志判定） | `unknown` |
| 最近一次报错的上下文 | 不渲染该节 |
| rootfs 结构摘要（etc/init 脚本、web 服务二进制位置） | 「未取到：找不到提取出的 rootfs 目录」 |
| 运行历史（最近 3 次） | 「运行历史不可读」 |
| 既有规则插件清单（提示勿重复） | 「规则插件库不可读」 |

**缺失一律渲染成「未取到」而不是占位符。** 一个伪造了材料的 prompt 会让模型归因出
材料并未描述的故障——这是这个功能唯一可能「自信地错」的方式。

prompt 里同时声明了引擎允许的键（顶层 6 个、`detect` 7 个条件键、`actions` 4 个动作键），
因为引擎对一个不认识的键的回答是「记一条告警然后忽略」。

### 6.1 响应的容忍边界

- **容忍** ```` ```json ```` 围栏（本地服务器普遍会加）
- **不原谅尾随闲话**——需要「抢救」的响应应该是**被人看到失败**，而不是被静默修好
- `extra="forbid"`：决策带一个没人声明的键 → 拒；`confidence` 出界 → 拒；action 不在
  三个值里 → 拒

`temperature=0.0`：诊断要可复现，重试两次得到两个不同诊断会让账本失去意义。

---

## 7. 端点清单

全部在 `/api/v1/ai/*` 下，全部显式挂 `Caller`，响应模型全部 `extra="forbid"`。
静态路由（`/drafts`）声明在按 id 路由（`/drafts/{rule_id}/...`）之前。

| 方法 | 路径 | 作用 | 会写什么 |
|---|---|---|---|
| GET | `/api/v1/ai/status` | 该层会不会跑；只报密钥是否配置 | 无 |
| POST | `/api/v1/ai/diagnose` | 一次诊断：规则层先试，未命中才调模型 | 无（只读） |
| GET | `/api/v1/ai/drafts` | 待审草案清单（含 YAML 全文） | 无 |
| POST | `/api/v1/ai/drafts` | 保存一份草案 | 草稿目录 |
| POST | `/api/v1/ai/drafts/{rule_id}/install` | 人工接受 | 插件目录 + `repair_action` |
| DELETE | `/api/v1/ai/drafts/{rule_id}` | 人工拒绝 | 删草稿文档、留 meta |

诊断走 `asyncio.to_thread` 离开事件循环：一次调用是分钟级超时的阻塞 HTTP，卡在事件循环上
会冻结慢端点下的**所有**路由——这是这个功能唯一可能把工作台一起带下去的方式。

---

## 8. 明确不做的事

| 不做 | 为什么 |
|---|---|
| AI 自由多轮会话 | 不可测试（无确定输入）、不可审计（账本记的不是会话）、不可降级（会话挂起不是异常） |
| AI 直写插件目录 | 三道门的第一道就破了 |
| AI 提供 guardian 其余动作的参数 | 第二处参数推导，证据比第一处更少 |
| 模型自动触发诊断 | 默认手动（页面按钮）；每次失败一次调用是账单不是功能 |
| 自动验证通过即安装 | MVP 是纯人工审核；「自动通过」列为 P2 可选 |
| CLI 触发入口（`iris emulate --ai-diagnose`） | 本轮只做页面按钮入口。**CHANGELOG 如实记录了这一点**，设计稿提到过但未实现 |
| 扩 `repair_action.evidence` 列 | 该列是 `String` 不是 JSON；AI 决策详情序列化进 evidence 文本 |

---

## 9. 相关代码

| 位置 | 内容 |
|---|---|
| `src/iris/llm/schema.py` | `PluginDraft`、`LLMDecision`（动作三值 + `extra=forbid`） |
| `src/iris/llm/context.py` | `DiagnosisMaterials`、`build_prompt()` |
| `src/iris/llm/client.py` | `LLMClient`、`LLMError`（单轮、重试预算、`transport=` 可注入） |
| `src/iris/llm/drafts.py` | 草稿存取与「走验证链」的安装、promoted 记账 |
| `src/iris/llm/diagnose.py` | 编排：规则层先试 → 模型 → 降级 |
| `src/iris/api/web_app.py` | 6 条路由与 8 个模型 |
| `tests/test_llm_{client,drafts,diagnose,api}.py` | 125 个用例 |

规则文档格式本身见 [`09-规则插件开发指南`](09-规则插件开发指南.md)——模型写的草案要通过
那一套校验，所以那一节的「可用键」表就是这一层 prompt 里声明的那份表。