"""Persist what an emulation run actually did, so the numbers can be counted.

``emulation_run`` and ``failure_profile`` existed in the schema with zero write
paths: ``grep -r EmulationRun src/`` matched the model and nothing else. Every
statistic in ``docs/eval-log.md`` was therefore maintained by hand, which is how a
table can claim "4/5 booted" next to five rows that all say otherwise.

Two things made the gap structural rather than an oversight:

* ``emulation_run.iid`` was declared as a foreign key to ``image.id``, but the
  ``iid`` every caller passes is a *scratch run* number -- ``md5(rootfs) % 10000``
  by default -- which almost never corresponds to a registered firmware. The
  column now holds the run number, and ``image_id`` carries the (optional) link
  to the corpus entry. Writing the run number into a column typed as a corpus id
  would have been a lie the schema could not express.
* The orchestrator is the only place every entry point passes through, so it is
  the only place a row can be written without one of them being forgotten.
  Recording is best-effort: a database that cannot be reached costs a log line,
  never the emulation result the caller is waiting for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from iris.db.models import EmulationRun, FailureProfile, Image, RepairAction
from iris.failures import Failure, FailureKind, kind_of, stage_of
from iris.log import get_logger

logger = get_logger(__name__)

__all__ = [
    "RunStats",
    "attribute_to_image",
    "clear_runs",
    "delete_run",
    "failure_histogram",
    "record_run",
    "run_stats",
    "web_reach_rate",
]


@dataclass
class RunStats:
    """What the recorded runs add up to. An empty table is a legitimate answer."""

    total: int = 0
    web_ok: int = 0
    #: arch -> (runs, web_ok)
    by_arch: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: kind -> count, informational kinds excluded
    failures: dict[str, int] = field(default_factory=dict)
    #: kind -> stage, resolved by the same rule that fills ``failures``
    failure_stages: dict[str, str] = field(default_factory=dict)
    #: stage -> count
    stages: dict[str, int] = field(default_factory=dict)

    @property
    def web_rate(self) -> float:
        return self.web_ok / self.total if self.total else 0.0


def attribute_to_image(session: Session, rootfs_dir: Path) -> int | None:
    """The corpus image a run belongs to, or None when it cannot be established.

    Extraction names its output ``<firmware stem>-rootfs`` under the scratch
    directory, and ``iris db add`` registers the firmware under that same stem, so
    the stem is the only link available. It is matched as a *prefix* because the
    registered name is sometimes truncated relative to the file name
    (``...en_plus_cn_`` vs ``...en_plus_cn_TD``).

    A run that cannot be attributed is stored with ``image_id`` NULL rather than
    attached to the closest candidate: a wrong attribution silently corrupts every
    per-firmware statistic, while a missing one is visible.
    """
    stem = rootfs_dir.name.removesuffix("-rootfs")
    if not stem:
        return None
    best: tuple[int, int] | None = None
    for image_id, filename in session.execute(select(Image.id, Image.filename)):
        if not filename:
            continue
        if stem.startswith(filename) or filename.startswith(stem):
            score = min(len(stem), len(filename))
            # Ties break on the lower id so the answer never depends on row order.
            if best is None or (score, -image_id) > (best[0], -best[1]):
                best = (score, image_id)
    return best[1] if best else None


def record_run(
    session: Session,
    *,
    iid: int,
    arch: str,
    rootfs_dir: Path,
    success: bool,
    web_ok: bool,
    findings: tuple[Failure, ...] = (),
    duration_sec: float = 0.0,
    started_at: datetime | None = None,
    ping_reachable: bool | None = None,
    ip: str | None = None,
) -> int:
    """Write one ``emulation_run`` row plus a ``failure_profile`` row per finding.

    ``result_kind`` on the run holds the primary failure -- the one that names the
    cause -- while every finding gets its own row, because one guest that fails to
    come up usually has several causes stacked (no NIC, no address, no web server)
    and collapsing them into one bucket would hide which one to fix first.

    ``ping_reachable`` and ``ip`` come from the layered link probe and are both
    nullable on purpose. "Never measured" and "measured, no reply" are different
    rows, and a NOT NULL column would force the first to be recorded as the second
    -- which is how a device that was never probed ends up in the failure count.
    """
    if not findings and not success:
        findings = (Failure(FailureKind.WEB_UNREACHABLE,
                            "the run failed without a diagnosis"),)

    primary = next((f for f in findings if f.is_failure), None)
    # The columns are naive DateTime, so the timestamp is stored naive too;
    # mixing aware values in would compare wrong against rows written elsewhere.
    finished = datetime.now(UTC).replace(tzinfo=None)
    run = EmulationRun(
        iid=iid,
        arch=arch,
        image_id=attribute_to_image(session, rootfs_dir),
        mode="run",
        web_reachable=web_ok,
        result=success,
        result_kind=primary.kind.value if primary else "",
        started_at=started_at or finished,
        finished_at=finished,
        time_web=int(duration_sec) if web_ok else None,
        ping_reachable=ping_reachable,
        ip=ip or None,
    )
    session.add(run)
    session.flush()

    for finding in findings:
        session.add(FailureProfile(
            run_id=run.id,
            stage=finding.stage.value,
            signal=finding.kind.value,
            log_fingerprint=finding.message[:200],
            detail={"hint": finding.hint, **dict(finding.evidence)},
        ))
    session.commit()
    return run.id


def delete_run(session: Session, run_id: int) -> bool:
    """Drop one recorded run and everything filed under it. ``False`` if absent.

    The children go first on purpose. Both ``failure_profile`` and
    ``repair_action`` declare ``ondelete="CASCADE"``, but SQLite does not enforce
    foreign keys unless ``PRAGMA foreign_keys=ON`` is issued on every connection --
    this engine issues none. So the CASCADE is a declaration, not a guarantee, and
    relying on it would leave rows that no longer reference anything.

    That is not cosmetic: :func:`run_stats` counts *every* ``failure_profile`` row
    to build the failure histogram, so an orphan would keep inflating the failure
    counts on the dashboard long after its run was gone.
    """
    run = session.get(EmulationRun, run_id)
    if run is None:
        return False
    session.execute(delete(FailureProfile).where(FailureProfile.run_id == run_id))
    session.execute(delete(RepairAction).where(RepairAction.run_id == run_id))
    session.delete(run)
    session.commit()
    return True


def clear_runs(session: Session) -> int:
    """Drop every recorded run, returning how many were removed.

    Children first, for the same reason as :func:`delete_run`. An empty table is a
    legitimate outcome and returns ``0`` -- a reader has to be able to tell "there
    was nothing to delete" from "the delete failed", which are different states
    that would otherwise both look like a no-op.
    """
    removed = session.scalar(select(func.count()).select_from(EmulationRun)) or 0
    session.execute(delete(FailureProfile))
    session.execute(delete(RepairAction))
    session.execute(delete(EmulationRun))
    session.commit()
    return int(removed)


def run_stats(session: Session) -> RunStats:
    """Aggregate the recorded runs. Reads only what is in the table."""
    stats = RunStats()
    # scalars(), not execute(): execute() yields Rows keyed by the entity, so
    # `run.web_reachable` raises instead of reading the column.
    for run in session.scalars(select(EmulationRun)):
        stats.total += 1
        if run.web_reachable:
            stats.web_ok += 1
        arch = run.arch or "?"
        seen, ok = stats.by_arch.get(arch, (0, 0))
        stats.by_arch[arch] = (seen + 1, ok + (1 if run.web_reachable else 0))

    for profile in session.scalars(select(FailureProfile)):
        if not profile.signal or profile.signal in _informational():
            # A working signal has no stage to attribute: counting it would pad
            # whichever stage it sits in with runs that did not fail there.
            continue
        stats.failures[profile.signal] = stats.failures.get(profile.signal, 0) + 1
        stage = _stage_of(profile)
        if stage:
            stats.failure_stages[profile.signal] = stage
            stats.stages[stage] = stats.stages.get(stage, 0) + 1
    return stats


def failure_histogram(session: Session) -> list[tuple[str, str, int]]:
    """``(stage, kind, count)`` ordered by stage, then most frequent first.

    The stage comes from :func:`run_stats`, which already resolved it while
    counting. Re-deriving it here would need a second, independently-invented
    fallback for kinds the taxonomy does not know -- and two fallbacks disagreeing
    is how one failure ends up filed under ``infra`` in the histogram and under
    ``boot`` in the per-stage totals.
    """
    stats = run_stats(session)
    rows = [(stats.failure_stages.get(kind, ""), kind, count)
            for kind, count in stats.failures.items()]
    rows.sort(key=lambda row: (row[0], -row[2], row[1]))
    return rows


def web_reach_rate(session: Session) -> float:
    """Share of recorded runs whose web plane answered, over *every* recorded run.

    The denominator is all runs, not the ones that produced a diagnosis: counting
    only diagnosed runs would quietly drop the firmware that never left evidence,
    which is exactly the population the metric exists to expose.
    """
    return run_stats(session).web_rate


def _informational() -> frozenset[str]:
    from iris.failures import INFORMATIONAL_KINDS

    return frozenset(k.value for k in INFORMATIONAL_KINDS)


def _stage_of(profile: FailureProfile) -> str:
    """The stage, preferring the taxonomy over whatever string is in the row.

    A row written before this taxonomy existed (or by hand) has a stage that no
    kind maps to; falling back to the stored value keeps those rows visible
    instead of dropping them from the totals.
    """
    resolved = kind_of(f"{profile.signal}:") if profile.signal else None
    return stage_of(resolved).value if resolved else (profile.stage or "")