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

import math
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
    "ArchLatency",
    "FailureCellCounts",
    "FailureCross",
    "FailureStageCounts",
    "FirmwareRuns",
    "RunStats",
    "attribute_to_image",
    "clear_runs",
    "corpus_profile",
    "delete_run",
    "failure_cross",
    "failure_histogram",
    "latency_profile",
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


@dataclass
class FirmwareRuns:
    """Every recorded run of one corpus firmware, as a single row.

    ``image_id`` is ``None`` for the runs that could not be attributed. Those form a
    group of their own rather than disappearing: :func:`attribute_to_image` stores a
    NULL instead of the closest candidate on purpose, because a wrong attribution
    silently corrupts every per-firmware statistic while a missing one is visible.
    """

    image_id: int | None
    label: str
    arch: str
    target_type: str | None
    runs: int
    web_ok: int
    last_result_kind: str
    run_ids: list[int]
    run_ids_total: int
    run_ids_truncated: bool


@dataclass
class ArchLatency:
    """How long the successful runs of one architecture took, in seconds."""

    arch: str
    samples: int
    min_sec: int | None
    median_sec: int | None
    p90_sec: int | None
    max_sec: int | None
    values: list[int]
    truncated: bool


@dataclass
class FailureCellCounts:
    kind: str
    count: int
    per_arch: dict[str, int]


@dataclass
class FailureStageCounts:
    stage: str
    total: int
    cells: list[FailureCellCounts]


@dataclass
class FailureCross:
    """Failures crossed with architecture, counted the way the dashboard counts them."""

    stages: list[FailureStageCounts]
    archs: list[str]
    kind_totals: dict[str, int]
    #: Failing signals whose stage could not be resolved at all. Reported rather
    #: than dropped: a total that does not add up looks like a complete picture.
    unclassified: int


#: The label for the runs that no registered firmware owns. Not a firmware.
_UNATTRIBUTED_LABEL = "未归属固件"

#: Reported per-arch values are capped so a corpus re-run thousands of times cannot
#: inflate the response without saying so; the counts beside them stay exact.
_LATENCY_VALUE_LIMIT = 200

#: Per-firmware run links are a convenience, not the table: only the newest are
#: linked and the total is reported next to them.
_RUN_ID_LIMIT = 50


def corpus_profile(session: Session) -> list[FirmwareRuns]:
    """Group every recorded run by the firmware it belongs to.

    Two shapes of the same question get two answers on purpose. ``arch`` is the
    architecture each *run* was measured as, collapsed to a single value when the
    firmware's runs agree -- :func:`run_stats` counts by that same per-run value, and
    a firmware reported under a different architecture than the stat card would make
    the two views disagree about one run. Runs that were measured as more than one
    architecture say ``mixed`` instead of picking the most common one, because
    picking would invent an agreement the data does not contain.

    Ordering is by run count descending, then by label, so the corpus's most
    exercised firmware is the first row and the order never depends on row order.
    """
    runs = list(session.scalars(select(EmulationRun).order_by(EmulationRun.id)))
    images = {image.id: image for image in session.scalars(select(Image))}

    grouped: dict[int | None, list[EmulationRun]] = {}
    for run in runs:
        grouped.setdefault(run.image_id, []).append(run)

    rows = [
        _firmware_row(image_id, group, images.get(image_id), run_id_limit=_RUN_ID_LIMIT)
        for image_id, group in grouped.items()
    ]
    rows.sort(key=lambda row: (-row.runs, row.label))
    return rows


def latency_profile(session: Session) -> list[ArchLatency]:
    """Per architecture, how long a run took to reach a working web plane.

    Only runs that got there are counted, and that is a property of the data rather
    than a filter chosen here: ``record_run`` writes ``time_web`` as the whole
    simulation's wall clock and only when the web plane answered, so a run that
    never came up has no duration recorded at all. Reading the column as "how long
    did the boot take" would be wrong twice over -- it includes container start,
    image build and QEMU boot, and it says nothing about the runs that failed.

    Percentiles are the *nearest rank* of the sorted sample, not an interpolated
    value: a run that took 400s is 400s long, and a median that reports 200s
    because it averaged it against a 1s run describes no run that happened.
    """
    by_arch: dict[str, list[int]] = {}
    for run in session.scalars(select(EmulationRun)):
        if run.time_web is None:
            continue
        by_arch.setdefault(run.arch or "?", []).append(run.time_web)

    rows = []
    for arch, values in by_arch.items():
        ordered = sorted(values)
        shown = ordered[:_LATENCY_VALUE_LIMIT]
        rows.append(ArchLatency(
            arch=arch,
            samples=len(ordered),
            min_sec=ordered[0],
            median_sec=_nearest_rank(ordered, 0.5),
            p90_sec=_nearest_rank(ordered, 0.9),
            max_sec=ordered[-1],
            values=shown,
            truncated=len(shown) < len(ordered),
        ))
    rows.sort(key=lambda row: row.arch)
    return rows


def failure_cross(session: Session) -> FailureCross:
    """Failures crossed with architecture, using the stages the dashboard resolved.

    The stage comes from :func:`run_stats` for the same reason
    :func:`failure_histogram` refuses to re-derive it: a second fallback would file
    one failure under two different stages across two views of the same library, and
    nobody would find out from the numbers. Informational kinds are excluded on the
    same grounds -- ``network-fallback-ok`` counted as a failure would paint the
    corpus as broken when it is the opposite.
    """
    stats = run_stats(session)
    arch_of_run = {
        run.id: (run.arch or "?")
        for run in session.scalars(select(EmulationRun))
    }

    #: kind -> stage -> arch -> count
    cells: dict[str, dict[str, dict[str, int]]] = {}
    kind_totals: dict[str, int] = {}
    unclassified = 0

    for profile in session.scalars(select(FailureProfile)):
        if not profile.signal or profile.signal in _informational():
            continue
        stage = stats.failure_stages.get(profile.signal, "")
        if not stage:
            unclassified += 1
            continue
        arch = arch_of_run.get(profile.run_id, "?")
        per_arch = cells.setdefault(profile.signal, {}).setdefault(stage, {})
        per_arch[arch] = per_arch.get(arch, 0) + 1
        kind_totals[profile.signal] = kind_totals.get(profile.signal, 0) + 1

    stage_rows: list[FailureStageCounts] = []
    archs: set[str] = set()
    for stage in sorted({s for by_stage in cells.values() for s in by_stage}):
        stage_cells = []
        stage_total = 0
        # Most frequent first so the reason to act is the first thing read.
        for kind, per_arch in sorted(
                cells.items(), key=lambda kv: -sum(kv[1].get(stage, {}).values())):
            arch_counts = per_arch.get(stage)
            if not arch_counts:
                continue
            stage_total += sum(arch_counts.values())
            archs.update(arch_counts)
            stage_cells.append(FailureCellCounts(
                kind=kind, count=sum(arch_counts.values()), per_arch=dict(sorted(arch_counts.items()))))
        stage_rows.append(FailureStageCounts(stage=stage, total=stage_total, cells=stage_cells))

    return FailureCross(
        stages=stage_rows,
        archs=sorted(archs),
        kind_totals=dict(sorted(kind_totals.items(), key=lambda kv: (-kv[1], kv[0]))),
        unclassified=unclassified,
    )


def _firmware_row(image_id: int | None, group: list[EmulationRun],
                  image: Image | None, *, run_id_limit: int) -> FirmwareRuns:

    archs = {run.arch for run in group if run.arch}
    return FirmwareRuns(
        image_id=image_id,
        label=image.filename if image is not None else _UNATTRIBUTED_LABEL,
        arch=next(iter(archs)) if len(archs) == 1 else ("mixed" if archs else "?"),
        target_type=image.target_type if image is not None else None,
        runs=len(group),
        web_ok=sum(1 for run in group if run.web_reachable),
        # The last row is the newest one: ordered by id, which only ever grows.
        last_result_kind=group[-1].result_kind or "",
        run_ids=[run.id for run in reversed(group)][:run_id_limit],
        run_ids_total=len(group),
        run_ids_truncated=len(group) > run_id_limit,
    )


def _nearest_rank(ordered: list[int], fraction: float) -> int:
    """The smallest sample at or above *fraction* of the way through the sorted list.

    Nearest rank, so the answer is always a value that was actually observed.
    Interpolating would report a median that no run ever hit, which on a corpus
    where a handful of runs take minutes and the rest take seconds is precisely
    the number that misleads.
    """
    rank = math.ceil(fraction * len(ordered))
    return ordered[min(max(rank, 1), len(ordered)) - 1]


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