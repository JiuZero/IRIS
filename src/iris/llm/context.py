"""One diagnosis prompt, built from materials that were already collected.

This module never reads a log, a database or the filesystem itself: it turns
materials that the caller gathered into the text one model round trip reads.
That split is what makes the prompt testable without a container, a database or
an endpoint -- and it is what keeps the prompt's inputs honest, because a caller
that did not manage to read the serial log passes the fact along instead of a
fabricated placeholder.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "DIAGNOSIS_CONTEXT_KEYS",
    "DiagnosisMaterials",
    "build_prompt",
]


@dataclass(frozen=True)
class DiagnosisMaterials:
    """What was actually read about one failing instance.

    Strings arrive already formatted and already truncated; ``None`` means "the
    collector could not get this", which is rendered as a stated absence rather
    than a placeholder a model would treat as data.
    """

    iid: int
    arch: str
    serial_tail: str | None
    serial_truncated: bool
    crash_context: str | None
    pattern_counts: dict[str, int]
    web_server_status: str
    rootfs_summary: str | None
    history: str
    plugins_summary: str
    max_context_chars: int = 12000


#: The prompt names these, so the JSON keys a model sees are declared once here
#: rather than inline in the f-string -- the same constant the output
#: requirements list below quotes, so the two cannot drift.
DIAGNOSIS_CONTEXT_KEYS = (
    "diagnosis", "action", "plugin_draft", "confidence", "verify_plan",
)


def build_prompt(m: DiagnosisMaterials) -> str:
    """The one round trip's full prompt. Deterministic in its inputs."""
    counts = "\n".join(
        f"- {name}: {count}" for name, count in sorted(m.pattern_counts.items())
    ) or "- 无命中模式"
    sections = [
        "你是嵌入式固件（IoT 路由器）仿真专家。下面是一次失败的固件仿真运行的诊断材料。",
        "请分析根因，并从受限的动作集中做出决策。",
        "",
        "## 实例与固件",
        f"- 实例编号：{m.iid}",
        f"- 架构：{m.arch or '?'}",
        f"- Web 服务状态（按串口日志判定）：{m.web_server_status}",
        f"- 历史运行摘要：{m.history}",
        "",
        "## 守护模式计数（本窗口）",
        counts,
        "",
    ]
    if m.serial_tail is not None:
        sections += [
            f"## 串口日志尾部（已截断到 {m.max_context_chars} 字符"
            + ("，另有更早内容未列入" if m.serial_truncated else "，全文在内")
            + "）",
            "```",
            m.serial_tail,
            "```",
            "",
        ]
    else:
        sections += [
            "## 串口日志尾部",
            "（未取到：串口日志不存在或不可读）",
            "",
        ]
    if m.crash_context:
        sections += ["## 最近一次报错的上下文", "```", m.crash_context, "```", ""]
    sections += [
        "## rootfs 结构摘要",
        m.rootfs_summary if m.rootfs_summary is not None
        else "（未取到：找不到提取出的 rootfs 目录）",
        "",
        "## 既有规则插件（决策请勿与它们重复）",
        m.plugins_summary,
        "",
        "## 输出要求",
        "只输出一个 JSON 对象，不要输出其它文字，字段固定为：",
        f"{', '.join(DIAGNOSIS_CONTEXT_KEYS)}",
        "",
        "- diagnosis：根因分析（中文，引用日志中的具体证据）",
        "- action：从这三个值里选一个：NONE / WEB_SERVER_RESTART / DRAFT_PLUGIN",
        (
            "  - 既有规则插件已覆盖的修复、或需要改 rootfs 才能修复的：按它们的性质说明，"
            "但 action 选 DRAFT_PLUGIN 并给出插件草案，交由人工确认安装（下次运行生效）"
        ),
        "  - 重启容器可能恢复的（如 web 服务进程退出）：WEB_SERVER_RESTART",
        "  - 只需归因、无需动作的：NONE",
        (
            "- plugin_draft：action 为 DRAFT_PLUGIN 时必填，否则为 null。"
            "字段固定为 rule_id / stage / description / yaml。"
        ),
        (
            "  - yaml 是完整规则文档文本：顶层只允许 id / description / stage / "
            "detect / actions / post_action_verify 键"
        ),
        (
            "  - detect 条件键：always / path_exists / dir_nonempty / file_glob / "
            "file_regex / all / any；actions 动作键：write / edit / comment_lines / "
            "guest_shell"
        ),
        "- confidence：0.0 到 1.0",
        "- verify_plan：动作或插件生效后应检查什么（字符串数组，中文）",
    ]
    return "\n".join(sections)