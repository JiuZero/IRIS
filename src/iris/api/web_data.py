"""工作台的只读视图:把库和磁盘上已有的东西聚合成页面直接能用的形状。

三条约束贯穿本模块:

* **形状在这里定,不在前端。** 前端拿到的 ``web_ok`` 是布尔而不是 ``"302"``、
  ``kind`` 是 ``FailureKind`` 的值而不是一句中文,这些都在这里做完。理由是同一份
  数据会被 CSV 导出、主页卡片和链路图共用,让三处各自推断同一个含义,就会在三处
  不一致的时候没人发现。
* **没有数据与数值为零是两件事。** 库里没有记录时统计卡返回 ``None`` 而不是 ``0``:
  0 断言的是"测了,结果是零",而 ``None`` 说的是"没测"。把前者显示成后者,会让
  刚跑完第一步的用户看到一张全部为零的仪表盘,读起来像"全都不行"。
* **口径写在返回值里。** 评测集的分母、能力徽章的判定依据都随数据一起返回,
  因为它们是数据的一部分,不是可以只存在于本页 docstring 里的注释。
"""

from __future__ import annotations

import csv
import io
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from iris import __version__
from iris.api.server import _session
from iris.config import get_settings
from iris.db.active import list_owned
from iris.db.knowledge import promote_candidates, root_cause_cards
from iris.db.models import EmulationRun, FailureProfile, RepairAction
from iris.db.runs import (
    clear_runs,
    delete_run,
    failure_histogram,
    run_stats,
    web_reach_rate,
)
from iris.emulate.container_stats import container_stats
from iris.emulate.linkprobe import LAYER_ORDER
from iris.emulate.orchestrator import serial_port_of
from iris.log import get_logger

logger = get_logger(__name__)

#: `docker stats` costs a round trip to the daemon, so a panel refreshing every few
#: seconds would otherwise spawn one per panel. The window is short enough that a
#: reader never notices the sample is not literally instantaneous, and long enough
#: that several panels showing the same instance agree with each other.
_STATS_TTL_SEC = 2.0
_stats_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}

#: Which failure kinds mean "the network never came up" as opposed to "the firmware
#: never offered a service". The dashboard gives these two very different advice, so
#: they cannot share one bucket -- but the literals come from ``iris.failures`` and
#: are not re-spelled here.
_NETWORK_KINDS = ("link-no-arp", "link-no-route", "link-no-icmp")

#: 落库的链路探测摘要只有三个键（见 `iris.emulate.linkprobe.LinkProfile.as_failure`
#: ）,所以详情页能还原的是"哪一层断了"而不是"每层当时看到了什么"。页面必须知道
#: 这个区别,否则渲染出的空文案会被读成"探测没发现异常"。
_LINK_DETAIL_NOTE = (
    "链路表来自 failure_profile 里落库的探测摘要:四层状态与首个断点完整,"
    "每层的原始探测文案与探测不可用原因未落库,因此这些字段为空；"
    "只有四层中出现阻断的运行才有这份摘要 -- 全通的运行不探测,探测不可用的运行不记证据"
)


def _link_from_profiles(
        profiles: list[FailureProfile], guest_ip: str) -> dict[str, Any] | None:
    """把落库的探测摘要还原成链路视图,没有摘要就返回 ``None``。

    还原而不是重新探测:重新探测要再起一个容器,而这里要回答的是"那次运行当时
    测到了什么",只有当时记下的那三键能回答。缺失的键按 ``unknown`` 补齐而不是
    省略,是因为四层里少一层会让读者以为是链路本身少了一层。
    """
    for profile in profiles:
        detail = profile.detail if isinstance(profile.detail, dict) else {}
        table = detail.get("table")
        if detail.get("probe") != "link" or not isinstance(table, dict):
            continue
        states = {str(layer): str(state) for layer, state in table.items()}
        first_break = str(detail.get("first_break") or "")
        return {
            "guest_ip": guest_ip,
            "first_break": first_break,
            # `unavailable` 从不落库:`as_failure` 在该字段非空时直接返回 None,
            # 所以只要有摘要,这一行就必然不存在。留空串而不是编一句说明。
            "unavailable": "",
            "layers": [
                {"layer": layer.value, "state": states.get(layer.value, "unknown"), "detail": ""}
                for layer in LAYER_ORDER
            ],
            "note": _LINK_DETAIL_NOTE,
        }
    return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def serial_console_available() -> bool:
    """Whether this checkout's ``run_qemu.sh`` publishes a writable serial console.

    Read from the script rather than declared as a constant, so the capability
    panel cannot claim an interactive console for a build that has not had the
    chardev change applied -- which is exactly the state a stale baked image is in.
    """
    script = _repo_root() / "scripts" / "emulate" / "run_qemu.sh"
    try:
        return "-serial chardev:iris_serial" in script.read_text(encoding="utf-8")
    except OSError:
        return False


def capabilities() -> dict[str, Any]:
    """The six capability rows the dashboard shows, with why each reads as it does.

    Every row carries the evidence it was derived from. A capability badge is a
    promise about the build; without the evidence next to it, the only way to tell a
    true one from a stale one is to go read the source.
    """
    console = serial_console_available()
    return {
        "version": __version__,
        "items": [
            {"id": "l1-extract", "name": "L1 固件提取", "state": "available",
             "detail": "UBI / squashfs / JFFS2 / FIT 均已实跑",
             "evidence": "iris.extract.rootfs_extract"},
            {"id": "l2-emulate", "name": "L2 容器仿真", "state": "available",
             "detail": "mips / armel / arm64 三种架构在隔离容器内启动 QEMU",
             "evidence": "iris.emulate.orchestrator"},
            {"id": "l2-probe", "name": "L2 链路诊断", "state": "available",
             "detail": "路由 / ARP / ICMP / 服务四层，逐层给出证据",
             "evidence": "iris.emulate.linkprobe"},
            {"id": "l3-rules", "name": "L3 规则引擎", "state": "available",
             "detail": "启动前修复规则，命中与否都记账",
             "evidence": "iris.rules.engine"},
            {"id": "l4-console", "name": "L4 交互式终端", "state": "available" if console else "planned",
             "detail": ("QEMU 串口为可写 chardev，可回车执行命令"
                        if console else
                        "run_qemu.sh 仍是单向 file: 落盘，交互需先应用串口改造"),
             "evidence": "scripts/emulate/run_qemu.sh"},
            {"id": "ai-guardian", "name": "AI 值守", "state": "unavailable",
             "detail": "规则 + 状态机的确定性自愈器，不含任何模型调用",
             "evidence": "iris.monitor.ai_guardian"},
        ],
    }


def stats(caller: str) -> dict[str, Any]:
    """The four dashboard cards, plus the numbers they are derived from.

    ``total`` is ``None`` rather than ``0`` when the table is empty, for the reason
    in this module's docstring.
    """
    try:
        with _session() as session:
            aggregate = run_stats(session)
            histogram = failure_histogram(session)
            rate = web_reach_rate(session)
            active = list_owned(session, caller)
    except Exception as exc:  # noqa: BLE001 - a dashboard must not 500 on a locked db
        logger.warning(f"could not read statistics: {exc}")
        return {"available": False, "detail": "metadata database unavailable",
                "total": None, "web_ok": None, "environment_failures": None,
                "web_reach_rate": None, "running": None, "by_arch": {},
                "failures": [], "active": []}

    network = sum(count for _stage, kind, count in histogram if kind in _NETWORK_KINDS)
    return {
        "available": True,
        "total": aggregate.total,
        "web_ok": aggregate.web_ok,
        "environment_failures": network,
        "web_reach_rate": rate,
        "running": len(active),
        "by_arch": {arch: {"seen": seen, "web_ok": ok}
                    for arch, (seen, ok) in sorted(aggregate.by_arch.items())},
        "failures": [{"stage": stage, "kind": kind, "count": count}
                     for stage, kind, count in histogram],
        "stages": dict(sorted(aggregate.stages.items())),
        "active": [vars(record) for record in active],
    }


def runs_page(*, limit: int = 50, offset: int = 0, arch: str = "",
              result_kind: str = "", query: str = "") -> dict[str, Any]:
    """Newest-first page of recorded runs, with the filters the list view offers.

    Filters live here rather than in the browser because the alternative -- fetching
    everything and filtering client-side -- would quietly change the meaning of the
    page count as soon as the table grew past a screen or two.
    """
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    with _session() as session:
        stmt = select(EmulationRun)
        if arch:
            stmt = stmt.where(EmulationRun.arch == arch)
        if result_kind:
            stmt = stmt.where(EmulationRun.result_kind == result_kind)
        needle = query.strip()
        if needle:
            stmt = stmt.where(EmulationRun.iid == int(needle)) if needle.isdigit() \
                else stmt.where(EmulationRun.ip.like(f"%{needle}%"))
        total = len(session.scalars(stmt).all())
        rows = list(session.scalars(
            stmt.order_by(EmulationRun.started_at.desc(), EmulationRun.id.desc())
            .offset(offset).limit(limit)
        ))
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": [
                {
                    "id": run.id,
                    "iid": run.iid,
                    "image_id": run.image_id,
                    "arch": run.arch or "",
                    "web_ok": run.web_reachable,
                    "ping_ok": run.ping_reachable,
                    "ip": run.ip or "",
                    "time_web": run.time_web,
                    "time_ping": run.time_ping,
                    "result": run.result,
                    "result_kind": run.result_kind or "",
                    "started_at": run.started_at.isoformat() if run.started_at else "",
                    "finished_at": run.finished_at.isoformat() if run.finished_at else "",
                }
                for run in rows
            ],
        }


def clear_run_records() -> dict[str, Any]:
    """Remove every recorded run. Returns how many rows were deleted.

    ``runs`` is a count, not a boolean, and it is ``0`` on an already-empty
    library -- "there was nothing to remove" is a different state from "the delete
    failed", and a caller that cannot tell them apart will render a successful
    cleanup that removed nothing.

    Note what this does *not* touch: ``active_emulation`` (what the service is
    hosting right now) and the corpus rows (``image``, ``object``). Clearing history
    is not "forget this firmware ever existed"; a running instance whose history row
    disappears is still running and still occupies a container.
    """
    with _session() as session:
        removed = clear_runs(session)
    return {"removed": removed}


def delete_run_record(run_id: int) -> dict[str, Any] | bool:
    """Remove one recorded run. ``False`` when no such run exists."""
    with _session() as session:
        return delete_run(session, run_id)


def run_detail(run_id: int) -> dict[str, Any] | None:
    """One run with everything that was learned about why it went the way it did.

    ``link`` is reconstructed from the evidence stored with the run, not measured
    again -- see :func:`_link_from_profiles` for what that can and cannot answer.
    """
    with _session() as session:
        run = session.get(EmulationRun, run_id)
        if run is None:
            return None
        profiles = list(session.scalars(
            select(FailureProfile).where(FailureProfile.run_id == run_id)))
        repairs = list(session.scalars(
            select(RepairAction).where(RepairAction.run_id == run_id)))
        return {
            "id": run.id,
            "iid": run.iid,
            "image_id": run.image_id,
            "arch": run.arch or "",
            "web_ok": run.web_reachable,
            "ping_ok": run.ping_reachable,
            "ip": run.ip or "",
            "time_web": run.time_web,
            "time_ping": run.time_ping,
            "result": run.result,
            "result_kind": run.result_kind or "",
            "started_at": run.started_at.isoformat() if run.started_at else "",
            "finished_at": run.finished_at.isoformat() if run.finished_at else "",
            "link": _link_from_profiles(profiles, run.ip or ""),
            "failures": [
                {"stage": p.stage, "signal": p.signal or "", "detail": p.detail or {},
                 "log_fingerprint": p.log_fingerprint or ""}
                for p in profiles
            ],
            "repairs": [
                {"source": r.source, "rule_id": r.rule_id or "",
                 "evidence": r.evidence or "", "applied": r.applied,
                 "promoted": r.promoted}
                for r in repairs
            ],
        }


def eval_set() -> dict[str, Any]:
    """The measured-run table, with the denominator spelled out.

    Counted from :func:`iris.db.runs.run_stats`, the same function the dashboard
    cards use, rather than from a page of rows. Counting the page would have made
    the rate silently wrong past 500 runs -- the denominator would keep growing
    while the numerator stopped at the page size -- and it would have made this
    table disagree with the cards above it for reasons nobody could see.

    The denominator is every recorded run. Firmware whose extraction failed never
    gets that far, so including them would quietly lower the rate for a stage of
    the pipeline this table does not measure -- and the number a dashboard shows
    has to mean the same thing on every machine that computes it, which a
    hand-maintained denominator does not.
    """
    with _session() as session:
        aggregate = run_stats(session)
    page = runs_page(limit=500)
    return {
        "release": __version__,
        "scope": "全库累计：所有已记录的仿真运行，同一固件的多次运行按次计入",
        "denominator": aggregate.total,
        "web_ok": aggregate.web_ok,
        "web_reach_rate": aggregate.web_rate,
        "by_arch": {arch: {"seen": seen, "web_ok": ok}
                    for arch, (seen, ok) in sorted(aggregate.by_arch.items())},
        "denominator_note": (
            "分母为全部已记录的仿真运行，分子为 web 面有响应的运行；"
            "提取阶段失败的固件不计入，因为本表度量的是仿真阶段而不是提取阶段；"
            "与主页统计卡同源（iris.db.runs.run_stats），两处数字必然一致"
        ),
        "items": page["items"],
        "items_note": (
            f"明细{'仅列出最近' if len(page['items']) < page['total'] else '列出全部'}"
            f" {len(page['items'])} 条，分母与分子不受此截断影响"
        ),
    }


def serial_log(iid: int, *, start_line: int = 0, max_lines: int = 2000) -> dict[str, Any]:
    """Lines of the guest console since ``start_line``.

    Read from the host-side snapshot the orchestrator copies back, not from inside
    the container: this is the same file the failure diagnosis reads, so what the
    log tab shows and what the verdict was based on cannot drift apart. Note that
    the copy happens when the run ends -- for a run still in progress there is
    nothing here yet, and the live stream arrives over the terminal socket.
    """
    path = get_settings().scratch_dir / f"emulate-{iid}" / "qemu.serial.log"
    if not path.is_file():
        return {"available": False, "reason": "no console snapshot for this instance",
                "lines": [], "next_line": start_line, "path": str(path)}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"available": False, "reason": f"console snapshot unreadable: {exc}",
                "lines": [], "next_line": start_line, "path": str(path)}
    lines = text.splitlines()
    start = max(0, start_line)
    chunk = lines[start:start + max_lines]
    return {
        "available": True,
        "path": str(path),
        "lines": chunk,
        "next_line": start + len(chunk),
        "total_lines": len(lines),
        "complete": start + len(chunk) >= len(lines),
    }


def export_csv(*, limit: int = 5000) -> str:
    """Every recorded run as CSV.

    Streamed into a string rather than a temp file: it is bounded by ``limit``, and
    a file would need cleaning up on every path that leaves early.
    """
    page = runs_page(limit=limit)
    buffer = io.StringIO()
    fields = ["id", "iid", "image_id", "arch", "web_ok", "ping_ok", "ip",
              "time_web", "time_ping", "result", "result_kind", "started_at", "finished_at"]
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for run in page["items"]:
        writer.writerow(run)
    return buffer.getvalue()


def instance_stats(iid: int) -> dict[str, Any]:
    """Live resource use for one running instance, with the serial port attached.

    ``sampled`` is separate from the values because a container that has just
    started has no reading yet, and a card showing ``0%`` for a container that is
    merely unmeasured is a card lying.
    """
    container = f"iris-qemu-{iid}"
    cached = _stats_cache.get(container)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _STATS_TTL_SEC:
        payload = cached[1]
    else:
        sample = container_stats(container)
        payload = None if sample is None else sample.to_dict()
        _stats_cache[container] = (now, payload)
    port = serial_port_of(iid)
    return {
        "iid": iid,
        "container": container,
        "sampled": payload is not None,
        "cpu_pct": None if payload is None else payload["cpu_pct"],
        "mem_mb": None if payload is None else payload["mem_mb"],
        "mem_limit_mb": None if payload is None else payload["mem_limit_mb"],
        "serial_port": port,
        "console_available": port is not None,
    }


def rule_plugins() -> dict[str, Any]:
    """The rule plugins the engine would load -- IRIS's own plus installed ones.

    A plugin centre that lists made-up extensions would be the one panel on this
    workbench whose contents mean nothing, so this reads the actual rule documents
    through the engine's own loader rather than keeping a parallel inventory: a rule
    added to ``rules/`` appears here, and one deleted disappears. It also goes
    through ``plugins.list_plugins``, so a document uploaded through the workbench
    shows up without a second code path -- and ``origin`` says which directory it
    came from, because "can I uninstall this" is only answerable for an external one.

    The tallies come from ``repair_action``, which is a ledger of repairs that were
    *attempted and recorded*. That is not the same as "the rule matched": the
    engine's own match report is per-application and is not retained. So the fields
    are named ``applied``/``promoted`` rather than "hits", and the page says so.
    """
    rules = _load_rule_plugins()
    tallies: dict[str, tuple[int, int, int]] = {}
    with _session() as session:
        for action in session.scalars(select(RepairAction)):
            key = action.rule_id or ""
            applied, promoted, recorded = tallies.get(key, (0, 0, 0))
            tallies[key] = (
                applied + 1 if action.applied else applied,
                promoted + 1 if action.promoted else promoted,
                recorded + 1,
            )
    return {
        "dirs": [str(directory) for directory in get_settings().effective_rules_dirs],
        "items": [
            {
                "id": item["rule"].id,
                "description": item["rule"].description.strip(),
                "stage": item["rule"].stage,
                "origin": item["origin"],
                "source_file": item["source_file"],
                "detect": _summarise_conditions(item["rule"].detect),
                "actions": _summarise_actions(item["rule"].actions),
                "verify": sorted(item["rule"].post_action_verify),
                "warnings": item["rule"].warnings,
                "applied": tallies.get(item["rule"].id, (0, 0, 0))[0],
                "promoted": tallies.get(item["rule"].id, (0, 0, 0))[1],
                "recorded": tallies.get(item["rule"].id, (0, 0, 0))[2],
            }
            for item in rules
        ],
    }


def _load_rule_plugins() -> list[dict[str, Any]]:
    """Every rule document with its origin, or an empty list when unreadable.

    Absent rather than raising: a wheel install has no ``rules/`` beside it, and a
    plugin centre that 500s is worse than one that honestly shows nothing. The
    empty case is distinguishable by the caller's own rendering, which reports the
    directories it could not read.
    """
    from iris.api.plugins import list_plugins

    try:
        return list_plugins()
    except OSError as exc:
        logger.warning(f"could not read rule plugins: {exc}")
        return []


def _summarise_conditions(detect: list[dict]) -> list[str]:
    """The detection keys a rule declares, flattened out of its AND/OR nesting.

    Only the *shape* is reported, never the patterns themselves: the fingerprints
    are long regexes and path globs whose readable form is the vendor's file
    layout, and a wall of those is not what a reader of a plugin list needs. The
    full document stays on disk in the directory the rule came from.
    """
    keys: set[str] = set()

    def walk(entries: object) -> None:
        if isinstance(entries, dict):
            for key, value in entries.items():
                if key in {"all", "any"} and isinstance(value, list):
                    for item in value:
                        walk(item)
                elif key == "within":
                    continue
                else:
                    keys.add(str(key))
        elif isinstance(entries, list):
            for item in entries:
                walk(item)

    walk(detect)
    return sorted(keys)


def _summarise_actions(actions: list[dict]) -> list[str]:
    """Which action kinds a rule declares, one entry per action."""
    kinds = [str(key) for entry in actions if isinstance(entry, dict) for key in entry]
    return kinds


def root_causes(*, recent: int = 10) -> dict[str, Any]:
    """Failure clusters worth looking at, with how many of them recovered."""
    with _session() as session:
        cards = root_cause_cards(session)
    promoted = promote_candidates(cards, recent=recent)
    names = {id(card) for card in promoted}
    return {
        "cards": [
            {
                "kind": card.kind,
                "stage": card.stage,
                "runs": card.runs,
                "recovered": card.recovered,
                "images": card.images,
                "archs": list(card.archs),
                "repairs": list(card.repairs),
                "sample": card.sample,
                "candidate": id(card) in names,
            }
            for card in cards
        ],
    }