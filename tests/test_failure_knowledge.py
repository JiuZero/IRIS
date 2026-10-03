"""The failure history, read back.

Two things are being guarded here, and one of them is a bug this file was written
after making.

The first is the missing writer. ``repair_action`` shipped with six columns and no
code path that ever wrote to it, so L3 rules fired on every emulation and nothing
recorded that they had. A ledger with no writer is a claim about a ledger.

The second is ``network-fallback-ok``. That kind means the injected network fallback
ran *as designed* -- the guest's network worked. Ranked as a root cause it would
have come first on this corpus (36 occurrences) and inverted its own meaning, so
it is excluded through the same closed set ``db.runs`` uses for the histogram.

The third is what the ranking is allowed to claim. ``recovered`` is 0 for every
card, because no successful run has ever carried a failure row. An earlier draft
gated the work list on "never recovered", which therefore ranked all seven kinds
and told a maintainer nothing. The gate is liveness instead, and the test that
matters most is the one that says a kind dropping out of the recent window is what
"the fix landed" looks like.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iris.cli import app as cli_app
from iris.db.knowledge import (
    RootCauseCard,
    promote_candidates,
    record_repairs,
    root_cause_cards,
)
from iris.db.models import Brand, EmulationRun, FailureProfile, Image, RepairAction


def naive(hour: int) -> datetime:
    """An hour on 2026-10-03, the way the column stores a timestamp.

    Naive, like ``db.runs.record_run`` writes: the column is a naive ``DateTime``
    and mixing an aware value in would compare wrong against the real corpus.
    """
    return datetime(2026, 10, 3, hour, 0)  # noqa: DTZ001


@pytest.fixture
def session(tmp_path: Path):
    from iris.db.engine import get_engine, init_db, make_session

    engine = get_engine(f"sqlite:///{(tmp_path / 'k.db').as_posix()}")
    init_db(engine)
    with make_session(engine) as s:
        s.add(Brand(id=1, name="test"))
        s.flush()
        yield s


def add_image(session, filename: str = "fw.bin") -> int:
    image = Image(filename=filename, arch="armel")
    session.add(image)
    session.flush()
    return image.id


def add_run(session, iid: int, *, web: bool, when: datetime, image_id: int | None = None,
            arch: str = "armel") -> EmulationRun:
    run = EmulationRun(
        iid=iid, arch=arch, image_id=image_id, web_reachable=web, result=web,
        started_at=when, result_kind="" if web else "no-guest-ip",
    )
    session.add(run)
    session.flush()
    return run


def add_finding(session, run: EmulationRun, kind: str, stage: str = "network",
                sample: str = "a finger, not a kind") -> None:
    session.add(FailureProfile(
        run_id=run.id, stage=stage, signal=kind, log_fingerprint=sample,
    ))
    session.flush()


class TestRecordRepairs:
    def test_a_rule_that_fired_is_recorded_against_its_run(self, session):
        run = add_run(session, 1, web=False, when=naive(1))
        assert record_repairs(session, run_id=run.id, applied_rule_ids=("netfix-telnetd",)) == 1
        row = session.query(RepairAction).one()
        assert (row.run_id, row.rule_id, row.applied, row.source) == (
            run.id, "netfix-telnetd", True, "rule",
        )

    def test_a_run_that_fired_nothing_writes_nothing(self, session):
        run = add_run(session, 1, web=True, when=naive(1))
        assert record_repairs(session, run_id=run.id) == 0
        assert session.query(RepairAction).count() == 0

    def test_blank_rule_ids_are_dropped(self, session):
        run = add_run(session, 1, web=False, when=naive(1))
        assert record_repairs(session, run_id=run.id, applied_rule_ids=("", "  ")) == 0

    def test_the_same_rule_fired_twice_is_one_row(self, session):
        """The engine reports a rule once per run; a duplicate would inflate the
        count a card reads as "repairs tried"."""
        run = add_run(session, 1, web=False, when=naive(1))
        assert record_repairs(session, run_id=run.id,
                              applied_rule_ids=("a", "a", "b", "a")) == 2

    def test_a_repair_that_did_not_happen_is_not_in_the_ledger(self, session):
        """Only matched rules are recorded. Offered-and-declined is knowledge, not
        an action, and mixing the two makes "repairs applied" unreadable."""
        run = add_run(session, 1, web=False, when=naive(1))
        record_repairs(session, run_id=run.id, applied_rule_ids=())
        assert session.query(RepairAction).count() == 0


class TestCards:
    def test_an_empty_database_has_no_cards(self, session):
        assert root_cause_cards(session) == []

    def test_a_kind_becomes_one_card(self, session):
        image = add_image(session)
        for iid, hour in enumerate((1, 2, 3), start=1):
            run = add_run(session, iid, web=False, when=naive(hour), image_id=image)
            add_finding(session, run, "no-guest-ip")
        (card,) = root_cause_cards(session)
        assert card.kind == "no-guest-ip"
        assert card.stage == "network"
        assert card.runs == 3
        assert card.images == 1

    def test_three_stacked_causes_make_three_cards(self, session):
        """The reason failure_profile has a row per finding: collapsing them would
        hide which one to attack first."""
        run = add_run(session, 1, web=False, when=naive(1))
        for kind, stage in (("no-guest-ip", "network"), ("web-wrong-port", "service"),
                            ("boot-hooks-missing", "boot")):
            add_finding(session, run, kind, stage)
        assert {c.kind for c in root_cause_cards(session)} == {
            "no-guest-ip", "web-wrong-port", "boot-hooks-missing",
        }

    def test_an_informational_kind_is_not_a_root_cause(self, session):
        """`network-fallback-ok` means the fallback *worked*. On the real corpus it
        would otherwise rank first with 36 occurrences and mean the opposite."""
        run = add_run(session, 1, web=False, when=naive(1))
        add_finding(session, run, "network-fallback-ok", "network")
        add_finding(session, run, "no-guest-ip", "network")
        assert [c.kind for c in root_cause_cards(session)] == ["no-guest-ip"]

    def test_a_finding_with_no_signal_is_skipped(self, session):
        run = add_run(session, 1, web=False, when=naive(1))
        add_finding(session, run, "", "network")
        add_finding(session, run, "   ", "network")
        assert root_cause_cards(session) == []

    def test_cards_are_ordered_by_frequency(self, session):
        image = add_image(session)
        for iid in range(1, 4):
            run = add_run(session, iid, web=False, when=naive(iid), image_id=image)
            add_finding(session, run, "common")
        rare = add_run(session, 9, web=False, when=naive(9), image_id=image)
        add_finding(session, rare, "rare")
        assert [c.kind for c in root_cause_cards(session)] == ["common", "rare"]

    def test_the_window_spans_first_to_last_occurrence(self, session):
        for iid, hour in ((1, 1), (2, 9)):
            run = add_run(session, iid, web=False, when=naive(hour))
            add_finding(session, run, "no-guest-ip")
        (card,) = root_cause_cards(session)
        assert card.first_seen == "2026-10-03 01:00:00"
        assert card.last_seen == "2026-10-03 09:00:00"

    def test_an_undated_run_is_counted_rather_than_dropped(self, session):
        """`ensure_columns` adds `started_at` to old databases with no default, so
        NULL is real. Skipping such a run would quietly shrink the count -- but it must
        not be placed on the timeline either, or "" becomes a timestamp the recency
        window cuts on."""
        run = add_run(session, 1, web=False, when=naive(1))
        run.started_at = None
        session.flush()
        add_finding(session, run, "no-guest-ip")
        (card,) = root_cause_cards(session)
        assert card.runs == 1
        assert card.seen_at == ()
        assert card.last_seen == ""

    def test_archs_and_repairs_are_gathered_across_runs(self, session):
        first = add_run(session, 1, web=False, when=naive(1), arch="armel")
        add_finding(session, first, "no-guest-ip")
        record_repairs(session, run_id=first.id, applied_rule_ids=("rule-a",))
        second = add_run(session, 2, web=False, when=naive(2), arch="mipsel")
        add_finding(session, second, "no-guest-ip")
        record_repairs(session, run_id=second.id, applied_rule_ids=("rule-b",))
        (card,) = root_cause_cards(session)
        assert card.archs == ("armel", "mipsel")
        assert card.repairs == ("rule-a", "rule-b")

    def test_a_run_without_an_image_does_not_invent_one(self, session):
        run = add_run(session, 1, web=False, when=naive(1))
        add_finding(session, run, "no-guest-ip")
        assert root_cause_cards(session)[0].images == 0

    def test_a_kind_seen_on_a_working_run_counts_as_recovered(self, session):
        run = add_run(session, 1, web=True, when=naive(1))
        add_finding(session, run, "web-wrong-port", "service")
        (card,) = root_cause_cards(session)
        assert card.recovered == 1
        assert card.recovery_rate == 1.0

    def test_a_run_that_never_probed_the_web_is_not_a_recovery(self, session):
        """`web_reachable` is NULL when the run ended before the probe. Python treats
        NULL as false, which here is the convenient direction: "we never found out"
        must not be counted as "it came up fine"."""
        run = add_run(session, 1, web=False, when=naive(1))
        run.web_reachable = None
        session.flush()
        add_finding(session, run, "web-wrong-port", "service")
        (card,) = root_cause_cards(session)
        assert (card.runs, card.recovered, card.recovery_rate) == (1, 0, 0.0)

    def test_an_empty_card_reports_zero_not_a_full_recovery(self, session):
        assert RootCauseCard(kind="x", stage="network").recovery_rate == 0.0

    def test_the_sample_fingerprint_is_carried_through(self, session):
        run = add_run(session, 1, web=False, when=naive(1))
        add_finding(session, run, "no-guest-ip", sample="no-guest-ip: nothing assigned")
        assert root_cause_cards(session)[0].sample == "no-guest-ip: nothing assigned"

    def test_the_card_is_serialisable(self, session):
        run = add_run(session, 1, web=False, when=naive(1))
        add_finding(session, run, "no-guest-ip")
        data = root_cause_cards(session)[0].to_dict()
        assert data["kind"] == "no-guest-ip"
        assert data["runs"] == 1
        assert data["last_seen"] == "2026-10-03 01:00:00"


class TestPromoteCandidates:
    @staticmethod
    def card(kind: str, runs: int, last_day: int) -> RootCauseCard:
        return RootCauseCard(
            kind=kind, stage="network", runs=runs,
            seen_at=(f"2026-10-{last_day:02d} 00:00:00",),
        )

    def test_a_kind_that_stopped_is_not_on_the_work_list(self, session):
        """This is what a fix landing looks like: the kind drops out of the recent
        window without anyone editing a document."""
        for iid, hour in enumerate(range(1, 10), start=1):
            run = add_run(session, iid, web=False, when=naive(hour))
            add_finding(session, run, "old-wrong-conclusion")
        for iid, hour in enumerate(range(10, 15), start=100):
            run = add_run(session, iid, web=False, when=naive(hour))
            add_finding(session, run, "still-broken")
        cards = root_cause_cards(session)
        live = promote_candidates(cards, recent=3)
        assert "still-broken" in [c.kind for c in live]
        assert "old-wrong-conclusion" not in [c.kind for c in live]

    def test_the_window_counts_runs_not_kinds(self, session):
        """A kind seen on every recent run stays live even though two kinds have been
        seen more recently. Ranking by kinds instead would drop it after a single
        newer sighting, and on this corpus several kinds share a last-seen minute --
        so the two definitions disagree in practice, not just in theory."""
        for iid, hour in enumerate((10, 11, 12, 13, 14), start=100):
            run = add_run(session, iid, web=False, when=naive(hour))
            add_finding(session, run, "chronic")
        for iid, hour in enumerate((20, 21), start=200):
            run = add_run(session, iid, web=False, when=naive(hour))
            add_finding(session, run, "newcomer")
        cards = root_cause_cards(session)
        assert {c.kind for c in promote_candidates(cards, recent=3)} == {"chronic", "newcomer"}

    def test_one_timestamp_recorded_twice_does_not_widen_the_window(self, session):
        """`failure_profile` carries a row per finding, so the same kind can land twice
        on one run. The duplicate would take a second slot of the window and push the
        genuine sighting before it out of the range."""
        chronic = add_run(session, 1, web=False, when=naive(10))
        add_finding(session, chronic, "chronic")
        add_finding(session, chronic, "chronic")
        newcomer = add_run(session, 2, web=False, when=naive(1))
        add_finding(session, newcomer, "newcomer", stage="service")
        cards = root_cause_cards(session)
        (card,) = [c for c in cards if c.kind == "chronic"]
        assert (card.runs, len(card.seen_at)) == (1, 1)
        assert [c.kind for c in promote_candidates(cards, recent=2)] == ["chronic", "newcomer"]

    def test_everything_is_live_when_all_kinds_are_recent(self):
        cards = [self.card("a", 5, 9), self.card("b", 3, 8)]
        assert len(promote_candidates(cards, recent=10)) == 2

    def test_the_live_ones_are_ranked_by_frequency(self):
        cards = [self.card("rare", 1, 9), self.card("common", 9, 8)]
        assert [c.kind for c in promote_candidates(cards, recent=10)] == ["common", "rare"]

    def test_a_card_with_no_timestamp_is_not_ranked_as_current(self):
        """An undated run cannot be placed in the window, and claiming it is current
        would put unplaceable history at the top of the work list."""
        cards = [RootCauseCard(kind="undated", stage="network", runs=5), self.card("dated", 1, 9)]
        assert [c.kind for c in promote_candidates(cards, recent=10)] == ["dated"]

    def test_no_cards_means_no_work_list(self):
        assert promote_candidates([], recent=10) == []

    def test_a_zero_or_negative_window_is_rejected(self):
        with pytest.raises(ValueError, match="at least 1"):
            promote_candidates([self.card("a", 1, 1)], recent=0)

    def test_recovery_is_not_used_as_the_gate(self, session):
        """No successful run has ever carried a failure row, so "has this recovered?"
        answers no for every kind and would put all of them on the list."""
        run = add_run(session, 1, web=False, when=naive(1))
        add_finding(session, run, "no-guest-ip")
        card = root_cause_cards(session)[0]
        assert card.recovered == 0


class TestTheCommand:
    @staticmethod
    def run(*args: str):
        return CliRunner().invoke(cli_app, ["db", "cards", *args])

    def test_an_empty_database_says_so_instead_of_printing_an_empty_table(self, session, monkeypatch):
        monkeypatch.setattr("iris.cli.get_settings", lambda: _settings(session))
        result = self.run()
        assert result.exit_code == 0, result.output
        assert "no failure profiles recorded yet" in result.output

    def test_the_command_reports_the_recent_window(self, session, monkeypatch):
        run = add_run(session, 1, web=False, when=naive(1))
        add_finding(session, run, "no-guest-ip")
        session.commit()  # the command opens its own connection
        monkeypatch.setattr("iris.cli.get_settings", lambda: _settings(session))
        result = self.run("--promote-only")
        assert result.exit_code == 0, result.output
        assert "no-guest-ip" in result.output
        assert "live within the last" in result.output

    def test_the_command_shows_the_last_seen_time_readably(self, session, monkeypatch):
        run = add_run(session, 1, web=False, when=naive(1))
        add_finding(session, run, "no-guest-ip")
        session.commit()  # the command opens its own connection
        monkeypatch.setattr("iris.cli.get_settings", lambda: _settings(session))
        result = self.run()
        assert "2026-10-03 01:00:00" in result.output
        assert "last=" in result.output

    def test_the_command_names_what_dropped_out_of_the_window(self, session, monkeypatch):
        """The half that answers "did the last fix land?" is printed alongside the
        ranked list; a command that only shows the survivors cannot answer it."""
        for iid, hour in enumerate(range(1, 4), start=1):
            run = add_run(session, iid, web=False, when=naive(hour))
            add_finding(session, run, "fixed-yesterday")
        for iid, hour in enumerate((10, 11, 12, 13), start=100):
            run = add_run(session, iid, web=False, when=naive(hour))
            add_finding(session, run, "still-broken")
        session.commit()  # the command opens its own connection
        monkeypatch.setattr("iris.cli.get_settings", lambda: _settings(session))
        result = self.run("--recent", "3")
        assert "no longer seen recently" in result.output
        assert "fixed-yesterday" in result.output


def _settings(session):
    """Point the command at *this* fixture's database.

    ``conftest`` redirects ``get_settings()`` at a session-wide temp file, which is
    not the database this fixture filled. Left alone, the command would read an
    empty corpus and every assertion below would pass or fail for the wrong reason
    -- the two that check for a kind would report it missing.
    """
    from iris.config import get_settings

    return get_settings().model_copy(
        update={"database_url": str(session.get_bind().url)}
    )