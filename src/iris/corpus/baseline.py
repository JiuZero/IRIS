"""A corpus you can hold IRIS to.

``docs/eval-log.md`` is a Markdown table a person maintains. It was accurate the
day it was written and stayed accurate by nobody touching it: while it claimed
DIR-868L was unreachable, the failure table next to it had already been
superseded, and nothing compared the document to the recorded runs. "3/5" and
"5/5" could both be true depending on which file a reader opened.

This module makes the claim checkable. It takes the corpus manifest plus what a
run actually produced, and answers three separate questions that have been
conflated into one number:

* :attr:`EvalReport.web_rate` -- how often a user gets a working device.
  Environment failures stay in the denominator, because the user still got
  nothing.
* :attr:`EvalReport.capability_rate` -- how much IRIS can do when the host is
  healthy. Environment failures leave the denominator.
* :attr:`EvalReport.regressions_against` -- which specific device got worse,
  which a rate difference cannot tell you (3/5 -> 4/5 can hide one regression
  and one fix at the same time).

Entries with no declared expectation (``expect_web is None``) and entries that
were never measured are reported as ``skipped`` and excluded from every
denominator. An unmeasured device and a broken one are different facts, and a
rate that mixes them is not a measurement.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy.orm import Session

from iris.corpus.manifest import FirmwareEntry
from iris.db.models import EmulationRun
from iris.log import get_logger

logger = get_logger(__name__)

__all__ = [
    "EvalEntryResult",
    "EvalReport",
    "Verdict",
    "evaluate",
    "observations_from_db",
    "render_markdown",
    "report_from_json",
    "report_to_json",
]


class Verdict(StrEnum):
    #: The entry declares a web expectation and IRIS met it.
    MET = "met"
    #: The entry declares a web expectation and IRIS did not meet it.
    MISSED = "missed"
    #: No declared expectation, or never measured. Excluded from every rate.
    SKIPPED = "skipped"
    #: Missed for an environmental reason the host cannot recover from. Stays in
    #: :attr:`EvalReport.web_rate`, leaves :attr:`EvalReport.capability_rate`.
    ENV_BROKEN = "env-broken"


@dataclass(frozen=True)
class EvalEntryResult:
    name: str
    arch: str
    verdict: Verdict
    web_ok: bool | None = None
    duration_sec: float = 0.0
    failure_kind: str = ""
    detail: str = ""
    note: str = ""

    @property
    def counted(self) -> bool:
        """Whether this row may enter a denominator.

        Tied to the verdict rather than to a separate flag: a row that was not
        measured has nothing to contribute to a rate, and an exclusion with no
        recorded reason is indistinguishable from cherry-picking.
        """
        return self.verdict is not Verdict.SKIPPED

    @property
    def is_env_broken(self) -> bool:
        return self.verdict is Verdict.ENV_BROKEN


@dataclass
class EvalReport:
    results: list[EvalEntryResult] = field(default_factory=list)

    @property
    def counted(self) -> list[EvalEntryResult]:
        return [r for r in self.results if r.counted]

    @property
    def met(self) -> int:
        return sum(1 for r in self.counted if r.verdict is Verdict.MET)

    @property
    def env_broken(self) -> int:
        return sum(1 for r in self.counted if r.is_env_broken)

    @property
    def total(self) -> int:
        return len(self.counted)

    @property
    def web_rate(self) -> float:
        """Met / participants, environment failures included. 0.0 for an empty set."""
        return self.met / self.total if self.total else 0.0

    @property
    def capability_rate(self) -> float:
        """Met / participants excluding environment failures.

        Only honest when read next to :attr:`web_rate`. Reporting this alone
        flatters the platform; reporting that alone convicts the host.
        """
        blocked = self.total - self.env_broken
        return self.met / blocked if blocked else 0.0

    def regressions_against(self, previous: EvalReport) -> list[EvalEntryResult]:
        """Entries that were met before and are not met now."""
        was_met = {r.name for r in previous.counted if r.verdict is Verdict.MET}
        return [r for r in self.counted if r.name in was_met and r.verdict is not Verdict.MET]


def evaluate(
    entries: Iterable[FirmwareEntry],
    observations: dict[str, dict[str, object]] | None = None,
    *,
    env_broken: Iterable[str] = (),
) -> EvalReport:
    """Turn per-entry observations into a report.

    ``observations`` maps entry name to what a run produced: ``web_ok`` (bool),
    ``duration_sec``, ``failure_kind``, ``detail``. An entry missing from it is
    ``skipped``, not failed.

    ``env_broken`` names entries whose miss had an environmental cause. It is a
    parameter and not a rule derived from the failure kind on purpose: the two
    devices ``docs/eval-log.md`` classifies that way were judged by reading a
    serial log, and this project has already been misled twice by trusting a log
    line over a measurement. The judgement belongs to whoever read the evidence.
    """
    observed = observations or {}
    broken = set(env_broken)
    report = EvalReport()
    for entry in entries:
        record = observed.get(entry.name)
        raw_web = record.get("web_ok") if record else None
        web_ok = raw_web if isinstance(raw_web, bool) else None

        if web_ok is None or entry.expect_web is None:
            verdict = Verdict.SKIPPED
        elif web_ok:
            verdict = Verdict.MET
        elif entry.name in broken:
            verdict = Verdict.ENV_BROKEN
        else:
            verdict = Verdict.MISSED

        report.results.append(
            EvalEntryResult(
                name=entry.name,
                arch=entry.arch_hint or "",
                verdict=verdict,
                web_ok=web_ok,
                duration_sec=float(record.get("duration_sec") or 0.0) if record else 0.0,
                failure_kind=str(record.get("failure_kind") or "") if record else "",
                detail=str(record.get("detail") or "") if record else "",
                note=entry.expectation_note,
            )
        )
    return report


def render_markdown(report: EvalReport, *, title: str = "评测基线") -> str:
    """A report in the shape of ``docs/eval-log.md``, so it can replace it."""
    lines = [
        f"## {title}",
        "",
        f"- 计入条目 / participants: **{report.total}**",
        f"- 达标 / met: **{report.met}**",
        f"- Web 可达率 / web rate: **{report.web_rate:.0%}**（分母含环境失败条目）",
        f"- 环境不可用 / env-broken: **{report.env_broken}**",
        f"- 能力口径 / capability rate: **{report.capability_rate:.0%}**（分母剔除环境失败条目）",
        "",
        "| 条目 | arch | 判定 | Web | 耗时 | 失败画像 | 说明 |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in report.results:
        web = "-" if result.web_ok is None else ("✅" if result.web_ok else "❌")
        duration = f"{result.duration_sec:.1f}s" if result.duration_sec else "-"
        lines.append(
            f"| {result.name} | {result.arch or '-'} | {result.verdict} | {web} | "
            f"{duration} | {result.failure_kind or '-'} | {result.note or '-'} |"
        )
    skipped = [r for r in report.results if r.verdict is Verdict.SKIPPED]
    if skipped:
        lines += [
            "",
            f"> 未实测或不参与统计 {len(skipped)} 条（不计入分母）："
            + "、".join(r.name for r in skipped),
        ]
    return "\n".join(lines) + "\n"


def report_to_json(report: EvalReport) -> dict[str, object]:
    """Machine-readable form, for saving a baseline to diff against later.

    Structure rather than Markdown on purpose: the rendered table is for humans
    and its column set will change, whereas this is the thing a regression
    comparison reads back.
    """
    return {
        "results": [
            {
                "name": r.name,
                "arch": r.arch,
                "verdict": r.verdict.value,
                "web_ok": r.web_ok,
                "duration_sec": r.duration_sec,
                "failure_kind": r.failure_kind,
            }
            for r in report.results
        ]
    }


def report_from_json(payload: dict[str, object]) -> EvalReport:
    """The inverse of :func:`report_to_json`.

    An unknown verdict string raises rather than defaulting to ``SKIPPED``: a
    baseline written by a newer version should fail loudly when read by an older
    one, not quietly report every device as unmeasured.
    """
    rows = payload.get("results") or []
    return EvalReport(
        results=[
            EvalEntryResult(
                name=str(row["name"]),
                arch=str(row.get("arch") or ""),
                verdict=Verdict(str(row["verdict"])),
                web_ok=row.get("web_ok") if isinstance(row.get("web_ok"), bool) else None,
                duration_sec=float(row.get("duration_sec") or 0.0),
                failure_kind=str(row.get("failure_kind") or ""),
            )
            for row in rows
            if isinstance(row, dict)
        ]
    )


def observations_from_db(
    entries: Iterable[FirmwareEntry],
    session: Session,
) -> dict[str, dict[str, object]]:
    """The latest recorded run per entry, pulled from `emulation_run`.

    "Latest" is by ``started_at`` with ``id`` as the tie-break, because two runs
    started in the same second must still order deterministically -- otherwise the
    report changes between two invocations that saw the same rows.

    Only entries that declared ``db_match`` are considered, and only exact
    substring hits count. An entry whose match string hits nothing -- or hits
    images that have no run, or only runs that never recorded a web verdict -- is
    reported as unmeasured rather than dropped, so a renamed file shows up as a
    gap in the report instead of quietly shrinking the denominator.
    """
    from sqlalchemy import select

    from iris.db.models import Image

    filenames = {
        image_id: (filename or "")
        for image_id, filename in session.execute(select(Image.id, Image.filename))
    }
    latest: dict[int, EmulationRun] = {}
    for run in session.scalars(select(EmulationRun)):
        if run.image_id is None:
            continue
        current = latest.get(run.image_id)
        if current is None or _run_order(run) >= _run_order(current):
            latest[run.image_id] = run

    observations: dict[str, dict[str, object]] = {}
    for entry in entries:
        if not entry.db_match:
            continue
        # A match string can hit more than one stored filename (a re-download
        # under a new name, a .zip and its extracted .bin). Take the newest run
        # across every hit rather than the first hit's run: stopping at the
        # first match would report an older attempt for a device whose newest
        # attempt is under the other filename, and "3/5" would drift with the
        # order rows happen to come back in.
        runs = [
            latest[image_id]
            for image_id, filename in filenames.items()
            if entry.db_match in filename and image_id in latest
        ]
        if not runs:
            continue
        run = max(runs, key=_run_order)
        if run.web_reachable is None:
            # Recorded without a verdict. Counting it as False would report a
            # failure for a run that never checked -- the run was aborted or
            # timed out before the web probe, which is not the same claim.
            continue
        observations[entry.name] = {
            "web_ok": bool(run.web_reachable),
            "duration_sec": float(run.time_web or 0),
            "failure_kind": run.result_kind or "",
            "detail": f"iid={run.iid} recorded {run.started_at}",
        }
    return observations


def _run_order(run: EmulationRun) -> tuple[float, int]:
    """Sort key for "most recent run", total over both fields.

    ``started_at`` can be NULL on a row written by an older code path, and
    comparing None with a datetime raises -- so a missing timestamp loses rather
    than taking the whole report down with it.
    """
    stamp = run.started_at
    return (stamp.timestamp() if stamp is not None else -1.0, run.id or 0)
