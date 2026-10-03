"""What IRIS learned last time, read back before the next run.

The gap this closes is stated in the README and was true: ``failure_profile`` was
written on every run and never read by anything. 73 recorded runs produced no
compound interest, because a run that failed told the next run nothing.

Two halves, and the write half was the more urgent. ``repair_action`` had a table,
six columns and **no writer at all** -- L3 rules fired on every emulation and
nothing recorded that they had. So even a human reading the database could not
answer the only question the ledger exists for: *did applying that rule change
the outcome?* A table nobody writes is a claim, not a record.

So the loop is closed in the order that makes it honest:

1. :func:`record_repairs` writes one ``repair_action`` row per rule that actually
   fired, on the run it fired on. Rules that were offered and declined are not
   actions and are not written -- a repair that did not happen has no business in
   a ledger of repairs.
2. :func:`root_cause_cards` reads ``failure_profile`` back, joined to the run's
   outcome and to whatever repairs were applied, and groups by failure kind. One
   card per kind: how often, on how much firmware, under which architecture, with
   which repairs applied, and how many of those runs ever reached the web plane.
3. :func:`promote_candidates` picks the kinds still turning up in the most recent
   runs. Those are the ones deserving a deterministic rule that does not exist yet
   -- the list a maintainer works from, ordered by how much it would buy.

Deliberately not automated. Nothing here applies a repair on the strength of a
historical row, because the only evidence that a repair works is
``Rule.post_action_verify`` on a live run, and a remembered success is not that.
The output is a ranked list of what to write a rule for; a human still writes the
rule, and the next run still proves or disproves it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from iris.db.models import EmulationRun, FailureProfile, RepairAction
from iris.log import get_logger

logger = get_logger(__name__)

__all__ = [
    "RootCauseCard",
    "promote_candidates",
    "record_repairs",
    "root_cause_cards",
]


def record_repairs(
    session: Session,
    *,
    run_id: int,
    applied_rule_ids: tuple[str, ...] | list[str] = (),
) -> int:
    """Write one ``repair_action`` row per rule that fired on ``run_id``.

    ``source`` is ``rule`` because that is the only writer in the project: there is
    no LLM integration and no manual path wired to this table, and claiming
    otherwise in a column would be a fabricated provenance.

    ``evidence`` names the run the rule fired on, nothing more. The rule engine's
    ``RuleReport`` does carry ``touched_files``, and persisting a count of them
    would make the difference between a surgical repair and a shotgun visible --
    but the reports do not survive ``prepare_from_firmware``, which keeps only the
    matched ids, so the number is not available at this layer. That gap is
    recorded in the README rather than papered over with a placeholder.

    Returns the number of rows written, so a caller can assert "this run fired
    nothing" without re-querying.
    """
    ids = [rid for rid in dict.fromkeys(r.strip() for r in applied_rule_ids) if rid]
    if not ids:
        return 0
    for rule_id in ids:
        session.add(RepairAction(
            run_id=run_id,
            source="rule",
            rule_id=rule_id,
            applied=True,
            evidence=f"matched on run {run_id}",
        ))
    session.flush()
    logger.info(f"recorded {len(ids)} repair(s) on run {run_id}: {', '.join(ids)}")
    return len(ids)


@dataclass(frozen=True)
class RootCauseCard:
    """One failure kind, accumulated across every run that hit it.

    Read as a question rather than a verdict. ``runs`` says how often it was seen,
    ``last_seen`` says whether it is still happening, and ``repairs`` says what was
    already tried on those runs.

    ``recovered`` counts runs where this kind appeared *and* the web plane came up
    anyway -- the difference between a cause and a symptom riding along with
    healthy runs. It reads 0 across the whole corpus today, and that is a fact
    about the data rather than a finding: no successful run has ever carried a
    failure row. It is kept because the link probe can now produce one, and a
    column that appears the moment it becomes meaningful beats a column invented
    for the occasion.

    ``seen_at`` is every dated occurrence, ascending, one entry per run -- the
    timeline the recency gate in :func:`promote_candidates` reads. It can be
    shorter than ``runs``, because ``ensure_columns`` adds ``started_at`` to older
    databases with no default and an undated run is counted but cannot be placed.
    """
    kind: str
    stage: str
    runs: int = 0
    recovered: int = 0
    images: int = 0
    archs: tuple[str, ...] = ()
    repairs: tuple[str, ...] = ()
    seen_at: tuple[str, ...] = ()
    sample: str = ""

    @property
    def first_seen(self) -> str:
        return self.seen_at[0] if self.seen_at else ""

    @property
    def last_seen(self) -> str:
        return self.seen_at[-1] if self.seen_at else ""

    @property
    def recovery_rate(self) -> float:
        """0.0 for a set with nothing in it, so an unused card never reads as 100%."""
        return self.recovered / self.runs if self.runs else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "stage": self.stage,
            "runs": self.runs,
            "recovered": self.recovered,
            "recovery_rate": round(self.recovery_rate, 4),
            "images": self.images,
            "archs": list(self.archs),
            "repairs": list(self.repairs),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "seen_at": list(self.seen_at),
            "sample": self.sample,
        }


def root_cause_cards(session: Session) -> list[RootCauseCard]:
    """One card per failure kind, most frequent first.

    A run that hit three stacked causes contributes to three cards on purpose --
    collapsing them would hide which one to attack first, which is the whole
    reason ``failure_profile`` has a row per finding rather than one per run.

    Informational kinds are excluded, reusing the same closed set ``db.runs``
    already uses for the histogram. ``network-fallback-ok`` means the injected
    fallback ran as designed -- the guest's network *worked*. Ranking it beside
    ``no-guest-ip`` as a root cause inverts its meaning, and on this corpus it
    would have ranked first.

    Rows with no ``signal`` are skipped: they carry a stage and free text but name
    no kind, and a card keyed on nothing would gather every unnamed failure into
    one bucket that looks like a cause.
    """
    from iris.db.runs import _informational

    informational = _informational()
    runs = {
        run.id: run
        for run in session.scalars(select(EmulationRun))
    }
    if not runs:
        return []

    repairs_by_run: dict[int, list[str]] = {}
    for action in session.scalars(select(RepairAction)):
        if action.run_id in runs and action.rule_id:
            repairs_by_run.setdefault(action.run_id, []).append(action.rule_id)

    grouped: dict[str, dict[str, object]] = {}
    for profile in session.scalars(select(FailureProfile)):
        kind = (profile.signal or "").strip()
        if not kind or kind in informational:
            continue
        run = runs.get(profile.run_id)
        if run is None:
            continue
        bucket = grouped.setdefault(kind, {
            "stage": profile.stage,
            "run_ids": set(),
            "stamps": {},
            "recovered": 0,
            "images": set(),
            "archs": set(),
            "repairs": set(),
            "sample": profile.log_fingerprint or "",
        })
        bucket["run_ids"].add(run.id)
        if run.web_reachable:
            bucket["recovered"] += 1
        if run.image_id is not None:
            bucket["images"].add(run.image_id)
        if run.arch:
            bucket["archs"].add(run.arch)
        bucket["repairs"].update(repairs_by_run.get(run.id, []))
        if run.started_at is not None:
            # Keyed by run, not appended: one kind can be recorded twice on the same
            # run, and a duplicated timestamp would widen its share of the recency
            # window that ``promote_candidates`` cuts from these.
            bucket["stamps"][run.id] = _text(run.started_at)

    cards = [
        RootCauseCard(
            kind=kind,
            stage=str(bucket["stage"]),
            runs=len(bucket["run_ids"]),
            recovered=int(bucket["recovered"]),
            images=len(bucket["images"]),
            archs=tuple(sorted(bucket["archs"])),
            repairs=tuple(sorted(bucket["repairs"])),
            seen_at=tuple(sorted(bucket["stamps"].values())),
            sample=str(bucket["sample"]),
        )
        for kind, bucket in grouped.items()
    ]
    cards.sort(key=lambda c: (-c.runs, c.kind))
    return cards


def promote_candidates(cards: list[RootCauseCard], *, recent: int = 10) -> list[RootCauseCard]:
    """The kinds most recently seen, ranked by how often -- the work list.

    Liveness, not severity, is the gate, and deliberately so. The obvious
    alternative -- "has this kind ever recovered?" -- cannot discriminate on this
    corpus: no successful run carries a failure row, so the answer is no for every
    kind and the ranking would be frequency wearing a disguise. Recurrence is
    something the data actually answers, and it is the question a maintainer has:
    *is this still biting?*

    Liveness is "seen in one of the ``recent`` most recent failing runs", so the
    cutoff is the ``recent``-th newest timestamp pooled from every card's
    ``seen_at``. Runs are the unit rather than days because the corpus's runs are
    minutes to hours apart: a day window would call the entire two-day history
    live or dead as a block, and the point of the gate is to notice a kind going
    quiet. Counting kinds instead of runs was the earlier attempt and it collapsed
    on ties -- several kinds here share a last-seen minute, so "the third newest
    kind" and "the third newest run" are different sets, and only the second
    answers the question.

    It also gives the loop something to close: a kind that used to appear on every
    run and has dropped out of the recent window has stopped, which is how a fix
    is seen to have landed without anyone editing a document.
    """
    if recent < 1:
        raise ValueError(f"recent must be at least 1, got {recent}")
    stamps = sorted((stamp for c in cards for stamp in c.seen_at), reverse=True)
    if not stamps:
        return []
    cutoff = stamps[min(recent, len(stamps)) - 1]
    return sorted(
        (c for c in cards if c.last_seen >= cutoff),
        key=lambda c: (-c.runs, c.kind),
    )


def _text(value: object) -> str:
    """Render a timestamp for a human.

    Readable on purpose: this string is the whole reason a card says *when* a kind
    was last seen, and an epoch float in a terminal answers no question anyone
    asked. Second resolution, because the corpus's runs are minutes apart and the
    microseconds are noise.
    """
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    return isoformat(sep=" ", timespec="seconds") if callable(isoformat) else str(value)