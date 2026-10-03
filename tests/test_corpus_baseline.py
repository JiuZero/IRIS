"""The corpus has to be able to fail.

``docs/eval-log.md`` said DIR-868L was unreachable while the run table beside it
had already recorded HTTP 200, and nothing noticed for a release. Every test
here guards one of the ways a rate can go wrong without anybody deciding to
lie: a denominator that quietly counts untried devices, an environment failure
that gets blamed on the platform, a regression hidden behind an unchanged
percentage, and a report that reads its own history back as "unmeasured".
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from iris.corpus.baseline import (
    EvalReport,
    Verdict,
    evaluate,
    observations_from_db,
    render_markdown,
    report_from_json,
    report_to_json,
)
from iris.corpus.manifest import FirmwareEntry, load_manifest
from iris.db.models import Brand

ROOT = Path(__file__).parent.parent / "iris-home" / "corpus"


def entry(name: str, *, expect_web: bool | None = True, **kw: object) -> FirmwareEntry:
    return FirmwareEntry(
        name=name, brand="B", url=f"https://example.com/{name}.bin", expect_web=expect_web, **kw
    )


def naive(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """A timestamp the way the column actually stores one.

    ``emulation_run.started_at`` is a naive ``DateTime`` and ``record_run``
    deliberately strips tzinfo before writing, so an aware value here would not
    round-trip through SQLite and would test a shape the database never holds.
    """
    return datetime(year, month, day, hour, minute)  # noqa: DTZ001


def verdicts(report: EvalReport) -> dict[str, Verdict]:
    return {r.name: r.verdict for r in report.results}


class TestDenominator:
    def test_an_entry_without_an_expectation_stays_unmeasured_even_when_observed(self):
        """A device nobody declared an expectation for cannot enter the rate by
        being handed an observation later -- that is how a denominator grows."""
        report = evaluate(
            [entry("a"), entry("b", expect_web=None)],
            {"a": {"web_ok": True}, "b": {"web_ok": True}},
        )
        assert verdicts(report) == {"a": Verdict.MET, "b": Verdict.SKIPPED}
        assert report.total == 1

    def test_an_unmeasured_entry_is_not_a_failure(self):
        """The single most load-bearing rule: absence of evidence is not evidence."""
        report = evaluate([entry("a"), entry("b")], {"a": {"web_ok": True}})
        assert verdicts(report) == {"a": Verdict.MET, "b": Verdict.SKIPPED}
        assert report.total == 1
        assert report.web_rate == 1.0

    def test_a_non_boolean_verdict_is_not_a_measurement(self):
        report = evaluate([entry("a")], {"a": {"web_ok": "yes"}})
        assert verdicts(report) == {"a": Verdict.SKIPPED}
        assert report.total == 0

    def test_an_empty_report_is_zero_over_zero_not_zero_percent(self):
        report = evaluate([], {})
        assert (report.total, report.met, report.web_rate) == (0, 0, 0.0)

    def test_skipped_rows_still_appear_in_the_table(self):
        report = evaluate([entry("a"), entry("b", expect_web=None)], {"a": {"web_ok": True}})
        markdown = render_markdown(report)
        assert "b" in markdown
        assert "skipped" in markdown

    def test_skipped_rows_are_named_in_the_exclusion_note(self):
        report = evaluate([entry("a"), entry("b"), entry("c")], {"a": {"web_ok": True}})
        assert "b、c" in render_markdown(report)


class TestEnvironmentFailures:
    def test_env_broken_stays_in_the_user_facing_rate(self):
        """The user still got no device, so it must not vanish from the headline."""
        report = evaluate(
            [entry("a"), entry("b"), entry("c")],
            {"a": {"web_ok": True}, "b": {"web_ok": False}, "c": {"web_ok": False}},
            env_broken=["b"],
        )
        assert verdicts(report)["b"] is Verdict.ENV_BROKEN
        assert report.total == 3
        assert report.web_rate == pytest.approx(1 / 3)

    def test_env_broken_leaves_the_capability_rate(self):
        report = evaluate(
            [entry("a"), entry("b"), entry("c")],
            {"a": {"web_ok": True}, "b": {"web_ok": False}, "c": {"web_ok": False}},
            env_broken=["b"],
        )
        assert report.env_broken == 1
        assert report.capability_rate == pytest.approx(0.5)

    def test_env_broken_cannot_make_a_met_entry_fail(self):
        report = evaluate([entry("a")], {"a": {"web_ok": True}}, env_broken=["a"])
        assert verdicts(report) == {"a": Verdict.MET}

    def test_env_broken_is_a_judgement_not_an_inference(self):
        """Naming an unmeasured device must not invent a verdict for it."""
        report = evaluate([entry("a")], {}, env_broken=["a"])
        assert verdicts(report) == {"a": Verdict.SKIPPED}
        assert report.env_broken == 0

    def test_capability_rate_of_only_env_failures_is_zero_not_a_crash(self):
        report = evaluate([entry("a")], {"a": {"web_ok": False}}, env_broken=["a"])
        assert report.web_rate == 0.0
        assert report.capability_rate == 0.0


class TestRegressions:
    def test_an_unchanged_rate_can_still_hide_a_regression(self):
        """3/5 -> 3/5 with a fix and a break in it is exactly what a rate hides."""
        before = evaluate(
            [entry("keep"), entry("lose"), entry("hold")],
            {
                "keep": {"web_ok": True},
                "lose": {"web_ok": True},
                "hold": {"web_ok": False},
            },
        )
        after = evaluate(
            [entry("keep"), entry("lose"), entry("hold")],
            {
                "keep": {"web_ok": True},
                "lose": {"web_ok": False},
                "hold": {"web_ok": True},
            },
        )
        assert before.web_rate == after.web_rate == pytest.approx(2 / 3)
        assert [r.name for r in after.regressions_against(before)] == ["lose"]
        assert [r.name for r in before.regressions_against(after)] == ["hold"]

    def test_the_failure_kind_travels_with_the_regression(self):
        before = evaluate([entry("a")], {"a": {"web_ok": True}})
        after = evaluate([entry("a")], {"a": {"web_ok": False, "failure_kind": "no-guest-ip"}})
        (regressed,) = after.regressions_against(before)
        assert regressed.failure_kind == "no-guest-ip"

    def test_a_device_that_was_never_met_is_not_a_regression(self):
        before = evaluate([entry("a")], {})
        after = evaluate([entry("a")], {"a": {"web_ok": False}})
        assert after.regressions_against(before) == []

    def test_env_broken_counts_as_a_regression(self):
        before = evaluate([entry("a")], {"a": {"web_ok": True}})
        after = evaluate([entry("a")], {"a": {"web_ok": False}}, env_broken=["a"])
        assert [r.name for r in after.regressions_against(before)] == ["a"]


class TestJsonRoundTrip:
    def test_a_written_report_reads_back_identical(self):
        report = evaluate(
            [entry("a"), entry("b"), entry("c")],
            {
                "a": {"web_ok": True, "duration_sec": 53.0},
                "b": {"web_ok": False, "failure_kind": "no-guest-ip"},
            },
            env_broken=["b"],
        )
        restored = report_from_json(json.loads(json.dumps(report_to_json(report))))
        assert verdicts(restored) == verdicts(report)
        assert (restored.total, restored.met, restored.env_broken) == (
            report.total,
            report.met,
            report.env_broken,
        )
        assert restored.web_rate == report.web_rate
        assert restored.capability_rate == report.capability_rate
        assert {r.name: r.duration_sec for r in restored.counted} == {
            r.name: r.duration_sec for r in report.counted
        }

    def test_a_baseline_from_a_newer_iris_fails_loudly(self):
        """Silent downgrade would report every device as unmeasured -- a fake pass."""
        payload = {"results": [{"name": "a", "verdict": "quantum-superposition"}]}
        with pytest.raises(ValueError):
            report_from_json(payload)

    def test_a_truncated_payload_is_not_a_reason_to_report_zero_percent(self):
        assert report_from_json({}).total == 0
        assert report_from_json({"results": "nonsense"}).total == 0

    def test_missing_web_ok_stays_unmeasured_across_the_round_trip(self):
        payload = {"results": [{"name": "a", "verdict": "skipped", "web_ok": "true"}]}
        assert report_from_json(payload).results[0].web_ok is None


class TestObservationsFromDb:
    @pytest.fixture
    def db(self, tmp_path: Path):
        from iris.db.engine import get_engine, init_db, make_session

        engine = get_engine(f"sqlite:///{(tmp_path / 'b.db').as_posix()}")
        init_db(engine)
        with make_session(engine) as session:
            session.add(Brand(id=1, name="test"))
            session.flush()
            yield session

    @staticmethod
    def add_image(session, filename: str) -> int:
        from iris.db.models import Image

        image = Image(filename=filename, arch="mipseb")
        session.add(image)
        session.flush()
        return image.id

    @staticmethod
    def add_run(session, *, image_id: int | None, web: bool | None, started: datetime | None, iid: int):
        from iris.db.models import EmulationRun

        run = EmulationRun(
            iid=iid,
            image_id=image_id,
            arch="mipseb",
            web_reachable=web,
            result=web,
            result_kind="" if web else "no-guest-ip",
            time_web=53 if web else None,
            started_at=started,
        )
        session.add(run)
        session.flush()
        return run

    def test_the_newest_run_wins(self, db):
        image_id = self.add_image(db, "archer-c7.bin")
        self.add_run(db, image_id=image_id, web=False, started=naive(2026, 10, 2, 9, 0), iid=1)
        self.add_run(db, image_id=image_id, web=True, started=naive(2026, 10, 3, 9, 0), iid=2)
        observed = observations_from_db([entry("c7", db_match="archer-c7")], db)
        assert observed["c7"]["web_ok"] is True
        assert observed["c7"]["duration_sec"] == 53.0

    def test_two_runs_in_the_same_second_still_order_deterministically(self, db):
        """Otherwise the same rows produce a different report on every invocation."""
        image_id = self.add_image(db, "archer-c7.bin")
        stamp = naive(2026, 10, 3, 9, 0)
        self.add_run(db, image_id=image_id, web=False, started=stamp, iid=1)
        self.add_run(db, image_id=image_id, web=True, started=stamp, iid=2)
        first = observations_from_db([entry("c7", db_match="archer-c7")], db)
        second = observations_from_db([entry("c7", db_match="archer-c7")], db)
        assert first == second
        assert first["c7"]["web_ok"] is True

    def test_a_run_without_a_timestamp_loses_to_one_with(self, db):
        """`ensure_columns` adds `started_at` to old databases with no default,
        so NULL timestamps exist in the wild -- and comparing None to a datetime
        raises, which would take the whole report down over one legacy row."""
        from sqlalchemy import text

        image_id = self.add_image(db, "archer-c7.bin")
        undated = self.add_run(db, image_id=image_id, web=True, started=naive(2026, 10, 3), iid=1)
        # The column carries ``default=datetime.utcnow``, so passing None would be
        # filled in rather than stored as NULL; only a raw write reproduces a
        # row that predates the column.
        db.execute(text("UPDATE emulation_run SET started_at = NULL WHERE id = :i"), {"i": undated.id})
        db.expire_all()
        self.add_run(db, image_id=image_id, web=False, started=naive(2026, 1, 1), iid=2)
        observed = observations_from_db([entry("c7", db_match="archer-c7")], db)
        assert observed["c7"]["web_ok"] is False

    def test_several_undated_runs_still_resolve_to_one_row(self, db):
        from sqlalchemy import text

        image_id = self.add_image(db, "archer-c7.bin")
        first = self.add_run(db, image_id=image_id, web=False, started=naive(2026, 10, 3), iid=1)
        second = self.add_run(db, image_id=image_id, web=True, started=naive(2026, 10, 3), iid=2)
        db.execute(
            text("UPDATE emulation_run SET started_at = NULL WHERE id IN (:a, :b)"),
            {"a": first.id, "b": second.id},
        )
        db.expire_all()
        observed = observations_from_db([entry("c7", db_match="archer-c7")], db)
        assert observed["c7"]["web_ok"] is True
        assert observed["c7"]["detail"].startswith("iid=2")

    def test_the_newest_run_across_every_matching_filename_wins(self, db):
        """A re-download stores a second row; the first match is the older run."""
        old_id = self.add_image(db, "archer-c7.bin")
        new_id = self.add_image(db, "archer-c7-factory.bin")
        self.add_run(db, image_id=old_id, web=True, started=naive(2026, 10, 2), iid=1)
        self.add_run(db, image_id=new_id, web=False, started=naive(2026, 10, 3), iid=2)
        observed = observations_from_db([entry("c7", db_match="archer-c7")], db)
        assert observed["c7"]["web_ok"] is False

    def test_a_match_string_hitting_nothing_is_unmeasured(self, db):
        self.add_image(db, "something-else.bin")
        self.add_run(db, image_id=1, web=True, started=naive(2026, 10, 3), iid=1)
        assert observations_from_db([entry("c7", db_match="archer-c7")], db) == {}

    def test_an_image_with_no_run_is_unmeasured(self, db):
        self.add_image(db, "archer-c7.bin")
        assert observations_from_db([entry("c7", db_match="archer-c7")], db) == {}

    def test_a_run_that_never_reached_the_web_probe_is_not_a_miss(self, db):
        """`web_reachable IS NULL` means undecided; scoring it False invents a failure."""
        image_id = self.add_image(db, "archer-c7.bin")
        self.add_run(db, image_id=image_id, web=None, started=naive(2026, 10, 3), iid=1)
        assert observations_from_db([entry("c7", db_match="archer-c7")], db) == {}

    def test_an_entry_without_a_match_string_is_never_guessed_at(self, db):
        self.add_image(db, "archer-c7.bin")
        self.add_run(db, image_id=1, web=True, started=naive(2026, 10, 3), iid=1)
        assert observations_from_db([entry("c7")], db) == {}

    def test_runs_with_no_image_link_are_invisible_rather_than_misattributed(self, db):
        self.add_image(db, "archer-c7.bin")
        self.add_run(db, image_id=None, web=True, started=naive(2026, 10, 3), iid=1)
        assert observations_from_db([entry("c7", db_match="archer-c7")], db) == {}

    def test_distinct_match_strings_never_cross_fill(self, db):
        """One device's result attached to another is worse than no number at all."""
        c7 = self.add_image(db, "openwrt-ath79-tplink_archer-c7-v2-squashfs-factory.bin")
        d2 = self.add_image(db, "openwrt-ramips-mt7621-d-team_newifi-d2-squashfs-sysupgrade.bin")
        self.add_run(db, image_id=c7, web=True, started=naive(2026, 10, 3), iid=1)
        self.add_run(db, image_id=d2, web=False, started=naive(2026, 10, 3), iid=2)
        observed = observations_from_db(
            [
                entry("c7", db_match="tplink_archer-c7-v2"),
                entry("d2", db_match="d-team_newifi-d2"),
            ],
            db,
        )
        assert observed["c7"]["web_ok"] is True
        assert observed["d2"]["web_ok"] is False
        assert observed["c7"]["detail"].startswith("iid=1")
        assert observed["d2"]["detail"].startswith("iid=2")


class TestTheShippedManifest:
    """Guards on ``iris-home/corpus/m0-baseline.toml`` itself."""

    @pytest.fixture
    def manifest(self):
        return load_manifest(ROOT / "m0-baseline.toml")

    def test_every_scored_entry_says_which_run_it_means(self, manifest):
        scored = [e for e in manifest.entries if e.expect_web is not None]
        assert scored, "the corpus declares no web expectations at all"
        assert [e.name for e in scored if not e.db_match] == []

    def test_an_entry_that_never_gets_a_verdict_must_say_so(self, manifest):
        silent = [e for e in manifest.entries if e.expect_web is None and not e.expectation_note]
        assert silent == [], f"unscored entries without a reason: {[e.name for e in silent]}"

    def test_match_strings_stay_unique(self, manifest):
        """Two entries sharing a match string would swap results on a rename."""
        declared = [e.db_match for e in manifest.entries if e.db_match]
        assert len(declared) == len(set(declared))

    def test_match_strings_are_distinctive_enough_to_bind_one_row(self, manifest):
        """A match string is a substring, so a shared stem would double-bind."""
        matches = [e.db_match for e in manifest.entries if e.db_match]
        for i, left in enumerate(matches):
            for right in matches[i + 1 :]:
                assert not (left in right or right in left), (left, right)

    def test_the_measured_entries_have_a_recorded_verdict_to_point_at(self, manifest):
        measured = [e for e in manifest.entries if e.expect_web is not None]
        unrecorded = [e.name for e in measured if "实测" not in e.expectation_note and "环境" not in e.expectation_note]
        assert unrecorded == [], f"measured entries with no provenance: {unrecorded}"

class TestTheCommand:
    """``iris corpus eval`` as an operator runs it."""

    @staticmethod
    def manifest(tmp_path: Path, *specs: tuple[str, bool | None]) -> Path:
        blocks = ['name = "t"', 'description = "test corpus"']
        for name, expect in specs:
            expect_line = "" if expect is None else f"expect_web = {'true' if expect else 'false'}\n"
            blocks.append(
                f'\n[[entries]]\nname = "{name}"\nbrand = "B"\n'
                f'url = "https://example.com/{name}.bin"\n{expect_line}'
            )
        path = tmp_path / "corpus.toml"
        path.write_text("\n".join(blocks) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def observations(tmp_path: Path, payload: dict[str, dict[str, object]]) -> Path:
        path = tmp_path / "obs.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    @staticmethod
    def run(*args: str):
        from typer.testing import CliRunner

        from iris.cli import app as cli_app

        return CliRunner().invoke(cli_app, ["corpus", "eval", *args])

    def test_an_unmeasured_corpus_does_not_report_zero_percent(self, tmp_path: Path):
        result = self.run(str(self.manifest(tmp_path, ("a", True))))
        assert result.exit_code == 0, result.output
        assert "NOT a 0% success rate" in result.output

    def test_observations_score_the_corpus(self, tmp_path: Path):
        obs = self.observations(tmp_path, {"a": {"web_ok": True}, "b": {"web_ok": False}})
        result = self.run(
            str(self.manifest(tmp_path, ("a", True), ("b", True))), "--observations", str(obs)
        )
        assert result.exit_code == 0, result.output
        assert "participants=2 met=1" in result.output
        assert "web_rate=50%" in result.output

    def test_env_failures_move_the_capability_rate_only(self, tmp_path: Path):
        obs = self.observations(tmp_path, {"a": {"web_ok": True}, "b": {"web_ok": False}})
        result = self.run(
            str(self.manifest(tmp_path, ("a", True), ("b", True))),
            "--observations", str(obs),
            "--env-broken", "b",
        )
        assert "web_rate=50%" in result.output
        assert "env_broken=1" in result.output
        assert "capability_rate=100%" in result.output

    def test_the_markdown_report_really_lands_on_disk(self, tmp_path: Path):
        obs = self.observations(tmp_path, {"a": {"web_ok": True, "duration_sec": 53.0}})
        out = tmp_path / "nested" / "report.md"
        result = self.run(
            str(self.manifest(tmp_path, ("a", True))),
            "--observations", str(obs),
            "--write", str(out),
        )
        assert result.exit_code == 0, result.output
        body = out.read_text(encoding="utf-8")
        assert "| a |" in body
        assert "53.0s" in body

    def test_the_json_report_reads_back_as_a_baseline(self, tmp_path: Path):
        obs = self.observations(tmp_path, {"a": {"web_ok": True}})
        first = self.run(
            str(self.manifest(tmp_path, ("a", True))),
            "--observations", str(obs),
            "--write-json", str(tmp_path / "base.json"),
        )
        assert first.exit_code == 0, first.output
        broken = self.observations(tmp_path, {"a": {"web_ok": False, "failure_kind": "no-guest-ip"}})
        second = self.run(
            str(self.manifest(tmp_path, ("a", True))),
            "--observations", str(broken),
            "--baseline", str(tmp_path / "base.json"),
        )
        assert "REGRESSION" in second.output
        assert "no-guest-ip" in second.output

    def test_an_unchanged_corpus_reports_no_regressions(self, tmp_path: Path):
        obs = self.observations(tmp_path, {"a": {"web_ok": True}})
        self.run(
            str(self.manifest(tmp_path, ("a", True))),
            "--observations", str(obs),
            "--write-json", str(tmp_path / "base.json"),
        )
        again = self.run(
            str(self.manifest(tmp_path, ("a", True))),
            "--observations", str(obs),
            "--baseline", str(tmp_path / "base.json"),
        )
        assert "no regressions" in again.output

    def test_a_missing_baseline_file_is_not_silently_a_clean_bill_of_health(self, tmp_path: Path):
        """No regression line printed after asking for a comparison reads as
        "nothing got worse", which is the one conclusion this command exists to
        prevent."""
        obs = self.observations(tmp_path, {"a": {"web_ok": True}})
        result = self.run(
            str(self.manifest(tmp_path, ("a", True))),
            "--observations", str(obs),
            "--baseline", str(tmp_path / "absent.json"),
        )
        assert result.exit_code == 0
        assert "no regressions" not in result.output
        assert "NOT a clean bill of health" in result.output