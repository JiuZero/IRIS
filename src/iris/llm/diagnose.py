"""One diagnosis, end to end: rule engine first, model only for the long tail.

The order is the design. The deterministic rule engine is tried first and a
recommendation from it short-circuits the round trip entirely -- the model is
for the signals the pattern counts cannot name, not a second opinion about
signals they already did. Calling the model on every failure would turn "AI
attribution" into "an API bill per failed run", and the whole point of the
draft-and-promote loop is that a repair, once accepted, is handled by the rule
engine for free from then on.

Every collector here degrades on its own: a missing serial log, an unreadable
rootfs and an empty plugin directory each pass their absence along as a stated
absence rather than a placeholder. A prompt that fabricates the material it
claims to have read would make the model attribute a failure that the materials
do not describe, which is the one way this feature could be confidently wrong.

Nothing here executes anything. The outcome is a decision -- restart suggestion,
plugin draft, or attribution only -- and the side effects are separate,
explicit endpoints: a draft is saved by a button, a restart is performed by a
person. A diagnosis endpoint that mutated state on its own would make the
review a formality, and the review is the safety property.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from iris.config import get_settings
from iris.llm.client import LLMClient, LLMError
from iris.llm.context import DiagnosisMaterials, build_prompt
from iris.llm.schema import LLMDecision
from iris.log import get_logger
from iris.monitor.ai_guardian import AIHealthMonitor, SerialLogAnalyzer

logger = get_logger(__name__)

__all__ = [
    "DiagnosisOutcome",
    "diagnose_instance",
]


@dataclass(frozen=True)
class DiagnosisOutcome:
    """What one diagnosis produced, including every fact about how it ran.

    ``llm_used`` is false for a rule-engine hit and for a disabled layer; the
    reason travels in ``rule_recommendation`` or ``disabled_reason``, because a
    caller that cannot tell "rules handled it" from "nothing ran" cannot render
    the outcome honestly either.
    """

    iid: int
    llm_used: bool
    decision: LLMDecision | None
    rule_recommendation: str | None
    disabled_reason: str | None
    error: str | None
    note: str


def diagnose_instance(iid: int, *, client: LLMClient | None = None) -> DiagnosisOutcome:
    """Diagnose one instance: pattern counts, then the model for what they miss.

    ``client`` is injectable for the tests and for a caller that holds its own
    configured client; without one, the settings' LLM block builds it, and a
    half-configuration (endpoint without a model, or either empty) reports
    itself disabled rather than failing on every request.
    """
    settings = get_settings()
    materials = _collect(iid, max_context_chars=settings.llm_max_context_chars)

    monitor = _monitor_for(iid)
    recommendation = monitor.recommend_recovery_action() if monitor else None
    if recommendation is not None:
        return DiagnosisOutcome(
            iid=iid, llm_used=False, decision=None,
            rule_recommendation=recommendation, disabled_reason=None, error=None,
            note=(
                "规则层已从串口模式计数给出恢复动作，未调用模型；"
                "模型只在规则层未命中的长尾信号上介入"
            ),
        )

    llm = client if client is not None else _client_from_settings(settings)
    if llm is None or not llm.enabled:
        reason = "LLM 未配置：需要 IRIS_LLM_BASE_URL 与 IRIS_LLM_MODEL"
        return DiagnosisOutcome(
            iid=iid, llm_used=False, decision=None,
            rule_recommendation=None, disabled_reason=reason, error=None,
            note="确定性规则未命中，且 LLM 层未启用；诊断止于采集到的材料",
        )

    prompt = build_prompt(materials)
    started = time.monotonic()
    try:
        decision = llm.decide(prompt)
    except LLMError as exc:
        logger.warning(f"LLM diagnosis for instance {iid} failed: {exc.reason}")
        return DiagnosisOutcome(
            iid=iid, llm_used=False, decision=None,
            rule_recommendation=None,
            disabled_reason=None, error=exc.reason,
            note="模型调用失败，已降级为仅采集；可修正端点后重试",
        )
    logger.info(
        f"LLM diagnosis for instance {iid}: action={decision.action} "
        f"confidence={decision.confidence:.2f} in {time.monotonic() - started:.1f}s"
    )
    return DiagnosisOutcome(
        iid=iid, llm_used=True, decision=decision,
        rule_recommendation=None, disabled_reason=None, error=None,
        note=(
            "模型单轮诊断完成；任何动作都需人工确认——重启建议在实例页执行，"
            "插件草案在插件页审核安装（下次运行生效）"
        ),
    )


def _collect(iid: int, *, max_context_chars: int) -> DiagnosisMaterials:
    """Read everything reachable about one instance, degrading per collector.

    Each absence is passed along as ``None`` and rendered as a stated absence in
    the prompt, so the model is never shown a placeholder pretending to be data.
    """
    settings = get_settings()
    scratch = settings.scratch_dir

    serial_tail: str | None = None
    serial_truncated = False
    crash_context: str | None = None
    pattern_counts: dict[str, int] = {}
    web_server_status = "unknown"

    log_path = _serial_log_path(iid, scratch)
    if log_path is not None:
        analyzer = SerialLogAnalyzer(log_path)
        if analyzer.load_log():
            total = len(analyzer.lines)
            window = analyzer.lines
            text = "".join(window)
            if len(text) > max_context_chars:
                text = text[-max_context_chars:]
                serial_truncated = True
            serial_tail = text
            crash_context = analyzer.get_latest_crash_context()
            pattern_counts = {
                name: analyzer.count_pattern_occurrences(name)
                for name in SerialLogAnalyzer.PATTERNS
            }
            if analyzer.count_pattern_occurrences("web_server_active") > 0:
                web_server_status = "active"
            elif analyzer.count_pattern_occurrences("web_server_start") > 0:
                web_server_status = "started_but_stopped"
            else:
                web_server_status = "not_started"
            _ = total

    return DiagnosisMaterials(
        iid=iid,
        arch=_arch_of(iid, scratch),
        serial_tail=serial_tail,
        serial_truncated=serial_truncated,
        crash_context=crash_context,
        pattern_counts=pattern_counts,
        web_server_status=web_server_status,
        rootfs_summary=_rootfs_summary(iid, scratch),
        history=_history_summary(iid),
        plugins_summary=_plugins_summary(),
        max_context_chars=max_context_chars,
    )


def _serial_log_path(iid: int, scratch: Path) -> Path | None:
    """The host-side serial snapshot, same three candidates the guardian walks."""
    for candidate in (
        scratch / str(iid) / "qemu.serial.log",
        scratch / f"emulate-{iid}" / "qemu.serial.log",
        scratch / "qemu.serial.log",
    ):
        if candidate.is_file():
            return candidate
    return None


def _monitor_for(iid: int) -> AIHealthMonitor | None:
    """A monitor that has run one analysis pass over this instance's serial log.

    ``analyze_health`` is called rather than the analyzer being poked directly:
    the recommendation is a method of the *monitor*, and it reads the status the
    pass fills in. A monitor with a loaded analyzer but no pass has a status of
    ``unknown`` and would recommend nothing at all -- which is not a conservative
    outcome, it is the rules layer silently dropping out of the diagnosis.

    The monitor's own ledger is left off: a diagnosis is not a recovery action, and
    booking it as one would double-count it in a ledger meant to record repairs.
    ``http_probe_port`` stays 0 for the same reason -- the container's web port is
    not necessarily the guest's, and probing the wrong one would turn a log
    reading into a false ``started_but_stopped``.
    """
    scratch = get_settings().scratch_dir
    if _serial_log_path(iid, scratch) is None:
        return None
    monitor = AIHealthMonitor(iid, scratch)
    monitor.analyze_health()
    return monitor


def _arch_of(iid: int, scratch: Path) -> str:
    """The architecture recorded for this instance's most recent run, if any."""
    try:
        from sqlalchemy import select

        from iris.db.engine import get_engine, make_session
        from iris.db.models import EmulationRun

        engine = get_engine(get_settings().database_url)
        with make_session(engine) as session:
            run = session.scalars(
                select(EmulationRun)
                .where(EmulationRun.iid == iid)
                .order_by(EmulationRun.id.desc())
            ).first()
            return run.arch if run is not None else ""
    except Exception:  # noqa: BLE001 - one missing field never fails a diagnosis
        return ""


def _history_summary(iid: int) -> str:
    """The recorded runs of this instance, for the model to reason over."""
    try:
        from sqlalchemy import select

        from iris.db.engine import get_engine, make_session
        from iris.db.models import EmulationRun

        engine = get_engine(get_settings().database_url)
        with make_session(engine) as session:
            runs = session.scalars(
                select(EmulationRun)
                .where(EmulationRun.iid == iid)
                .order_by(EmulationRun.id.desc())
            ).all()
        if not runs:
            return "该实例没有已记录的仿真运行"
        lines = [f"共 {len(runs)} 次运行,最近一次:"]
        for run in runs[:3]:
            outcome = "成功" if run.result else (
                run.result_kind or "失败(无归因)"
            )
            duration = f"{run.time_web}s" if run.time_web is not None else "无耗时记录"
            lines.append(f"- 运行 {run.id}: {outcome},{duration}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001 - one missing field never fails a diagnosis
        return "运行历史不可读"


def _rootfs_summary(iid: int, scratch: Path) -> str | None:
    """A short structural summary of the extracted rootfs, if it is still there.

    The init scripts and the web binaries are what a plugin draft's ``detect``
    and ``actions`` have to name, and a draft naming paths that do not exist is
    the most likely way a model-authored rule does nothing.
    """
    base = scratch / f"emulate-{iid}"
    rootfs = None
    if base.is_dir():
        for entry in sorted(base.iterdir()):
            if entry.is_dir() and entry.name.endswith("-rootfs"):
                rootfs = entry
                break
    if rootfs is None:
        return None

    lines = [f"rootfs 目录：{rootfs.name}"]
    etc = rootfs / "etc"
    if etc.is_dir():
        inits = sorted(
            p.name for p in etc.iterdir()
            if p.is_file() and ("init" in p.name.lower() or "rc" in p.name.lower())
        )
        lines.append("etc/ 下 init/rc 脚本：" + (", ".join(inits) if inits else "无"))
    web_bins: list[str] = []
    for sub in ("bin", "sbin", "usr/bin", "usr/sbin"):
        directory = rootfs / sub
        if not directory.is_dir():
            continue
        for name in ("goahead", "boa", "lighttpd", "uhttpd", "thttpd", "httpd"):
            if (directory / name).is_file():
                web_bins.append(f"{sub}/{name}")
    lines.append("web 服务二进制：" + (", ".join(web_bins) if web_bins else "未找到"))
    return "\n".join(lines)


def _plugins_summary() -> str:
    """What the engine would already apply, so a draft does not duplicate it."""
    try:
        from iris.api.plugins import list_plugins

        found = list_plugins()
        if not found:
            return "（无已加载的规则插件）"
        lines = [f"共 {len(found)} 条,各自的 id 与用途:"]
        for item in found:
            rule = item["rule"]
            lines.append(f"- {rule.id}({item['origin']}): {rule.description[:120]}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001 - a summary never fails a diagnosis
        logger.warning(f"could not summarise the plugin library: {exc}")
        return "规则插件库不可读"


def _client_from_settings(settings) -> LLMClient | None:
    """The settings' LLM block as a client, or None when not configured."""
    if not settings.llm_enabled:
        return None
    return LLMClient(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        timeout_sec=settings.llm_timeout_sec,
        max_retries=settings.llm_max_retries,
    )