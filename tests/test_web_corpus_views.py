"""Three corpus views, and the promises each of them makes about what they count.

``/api/v1/stats/corpus``, ``/latency`` and ``/failure-matrix`` exist because the
dashboard could answer "how well" but not "which firmware", "how long" or "where".
Adding three panels that all read the same library is exactly how two of them end
up telling different stories about it, so most of what is pinned here is not the
shape of a response but *which* counting each one promises:

* every total is derived from :func:`iris.db.runs.run_stats`, the same function the
  stat cards read, so a panel cannot become a rival opinion about one run;
* informational kinds stay out of the failure table -- the network fallback working
  is not a fault, and counting it paints a working corpus as broken;
* percentiles are nearest-rank, so a median is a time some run actually took;
* an empty library and a truncated sample both say so rather than reading as zero.

The frontend contract is pinned by :class:`TestTheFrontendTypesMatchTheServer`,
because ``web/src/lib/types.ts`` is written by hand with no generator behind it --
the only thing standing between a renamed field and a silently blank cell.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_type_hints

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from iris.api import web_app, web_data
from iris.api.server import Caller
from iris.db.models import EmulationRun, FailureProfile, Image
from iris.db.runs import corpus_profile, failure_cross, latency_profile, run_stats
from iris.failures import Failure, FailureKind

TOKEN = "workbench-token"
PROJECT = Path(__file__).resolve().parents[1]

CORPUS_URL = "/api/v1/stats/corpus"
LATENCY_URL = "/api/v1/stats/latency"
MATRIX_URL = "/api/v1/stats/failure-matrix"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """An isolated schema, wired into both ``web_app`` and ``web_data``.

    A token is configured so the 401 guards have something to guard: with none set
    the server is in local mode and ``require_client`` hands every request the
    loopback identity without comparing anything, which would make those guards
    pass for the wrong reason.
    """
    from iris.api import auth as auth_module
    from iris.config import Settings
    from iris.db.engine import get_engine, init_db, make_session

    engine = get_engine(f"sqlite:///{(tmp_path / 'corpus-views.db').as_posix()}")
    init_db(engine)
    monkeypatch.setattr(web_app, "_session", lambda: make_session(engine))
    monkeypatch.setattr(web_data, "_session", lambda: make_session(engine))
    monkeypatch.setattr(auth_module, "get_settings",
                        lambda: Settings(api_token=TOKEN))
    return engine


@pytest.fixture
def client(db) -> TestClient:
    """No lifespan: nothing here starts or stops an instance.

    ``install`` is called without the shutdown assembly on purpose -- these views
    only read, and a test that ran the shutdown path would be testing that the
    docker call it stubs does nothing.
    """
    return TestClient(web_app.install(FastAPI()))


@pytest.fixture
def auth(client) -> TestClient:
    client.headers.update({"X-IRIS-Token": TOKEN})
    return client


def _register(engine, filename: str, *, arch: str | None = None,
              target_type: str | None = None) -> int:
    with _writer(engine) as session:
        image = Image(filename=filename, arch=arch, target_type=target_type,
                      brand_id=1)
        session.add(image)
        session.flush()
        registered = image.id
        # Committed, not just flushed: leaving the session to close would roll the
        # firmware back, and every run below would then be unattributed -- the
        # views would agree with each other about a library nobody registered.
        session.commit()
        return registered


def _record(engine, *, iid: int, arch: str, rootfs: str, success: bool, web_ok: bool,
            findings: tuple[Failure, ...] = (), duration_sec: float = 0.0,
            ping_ok: bool | None = None) -> int:
    """Write a run through the real path, so what is counted is what production writes."""
    from iris.db.runs import record_run

    with _writer(engine) as session:
        return record_run(session, iid=iid, arch=arch,
                          rootfs_dir=Path(rootfs), success=success, web_ok=web_ok,
                          findings=findings, duration_sec=duration_sec,
                          ping_reachable=ping_ok)


def _writer(engine):
    from iris.db.engine import make_session

    return make_session(engine)


def _profile(engine, run_id: int, stage: str, signal: str | None) -> None:
    """A row written outside ``record_run``, for signals a run would not produce.

    ``record_run`` refuses to store a run with no findings and no success, so the
    empty-signal case is only reachable from an older row or a hand-edited database.
    Both are exactly what this table has to survive, so they are staged directly.
    """
    with _writer(engine) as session:
        session.add(FailureProfile(run_id=run_id, stage=stage, signal=signal,
                                   log_fingerprint="", detail=None))
        session.commit()


class TestTheCorpusViewGroupsByFirmware:
    def test_one_row_per_firmware(self, db, auth):
        """Two runs of one firmware are one row, not two."""
        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=47.0)
        _record(db, iid=2, arch="armel", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=81.0)

        body = auth.get(CORPUS_URL).json()

        assert len(body["firmwares"]) == 1, body
        row = body["firmwares"][0]
        assert (row["label"], row["runs"], row["web_ok"]) == ("US_AC15.bin", 2, 2)

    def test_a_second_firmware_is_a_second_row(self, db, auth):
        _register(db, "US_AC15.bin")
        _register(db, "US_TES7002.bin")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=40.0)
        _record(db, iid=2, arch="mipsel", rootfs="US_TES7002-rootfs",
                success=False, web_ok=False)

        labels = [row["label"] for row in auth.get(CORPUS_URL).json()["firmwares"]]

        assert labels == ["US_AC15.bin", "US_TES7002.bin"], labels

    def test_the_most_exercised_firmware_comes_first(self, db, auth):
        _register(db, "rare.bin")
        _register(db, "busy.bin")
        _record(db, iid=1, arch="armel", rootfs="rare-rootfs",
                success=True, web_ok=True, duration_sec=10.0)
        for iid in (2, 3):
            _record(db, iid=iid, arch="armel", rootfs="busy-rootfs",
                    success=True, web_ok=True, duration_sec=10.0)

        labels = [row["label"] for row in auth.get(CORPUS_URL).json()["firmwares"]]

        assert labels == ["busy.bin", "rare.bin"], labels

    def test_the_newest_run_is_the_one_reported(self, db, auth):
        """A reader asking "what happened to this firmware" wants the latest."""
        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.WEB_UNREACHABLE, "first"),))
        _record(db, iid=2, arch="armel", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=30.0)

        row = auth.get(CORPUS_URL).json()["firmwares"][0]

        assert row["last_result_kind"] == "", row
        assert row["run_ids"][0] == 2, "run_ids are newest first"

    def test_a_failed_run_keeps_its_reason_in_the_newest_row(self, db, auth):
        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=30.0)
        _record(db, iid=2, arch="armel", rootfs="US_AC15-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.WEB_UNREACHABLE, "second"),))

        row = auth.get(CORPUS_URL).json()["firmwares"][0]

        assert row["last_result_kind"] == "web-unreachable", row


class TestUnattributedRunsAreNotSpreadOverRealFirmware:
    def test_a_run_with_no_registered_firmware_gets_its_own_row(self, db, auth):
        """``attribute_to_image`` stores NULL on purpose; folding these in would
        corrupt every per-firmware number in the table."""
        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="armel", rootfs="never-registered-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.WEB_UNREACHABLE, "orphan"),))

        body = auth.get(CORPUS_URL).json()

        assert body["firmwares"] == [], body
        assert body["unattributed"] is not None
        assert body["unattributed"]["image_id"] is None
        assert body["unattributed"]["runs"] == 1

    def test_the_two_groups_do_not_mix(self, db, auth):
        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=40.0)
        _record(db, iid=2, arch="mipsel", rootfs="never-registered-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.WEB_UNREACHABLE, "orphan"),))

        body = auth.get(CORPUS_URL).json()

        assert body["firmwares"][0]["runs"] == 1, "the orphan leaked into the firmware row"
        assert body["unattributed"]["runs"] == 1

    def test_no_orphan_means_no_second_group(self, db, auth):
        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=40.0)

        assert auth.get(CORPUS_URL).json()["unattributed"] is None


class TestTheCorpusViewAgreesWithTheStatCards:
    """The totals come from ``run_stats``, not from summing the rows above them.

    A total derived from the rows would agree with them by construction and so would
    prove nothing; these two guards are what make the panel a view rather than a
    second, independent count.
    """

    def test_the_run_total_is_the_one_run_stats_counts(self, db, auth):
        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=40.0)
        _record(db, iid=2, arch="armel", rootfs="never-registered-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.WEB_UNREACHABLE, "orphan"),))

        with _writer(db) as session:
            aggregate = run_stats(session)

        assert auth.get(CORPUS_URL).json()["totals"]["runs"] == aggregate.total

    def test_the_reachable_total_matches_too(self, db, auth):
        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=40.0)
        _record(db, iid=2, arch="armel", rootfs="US_AC15-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.WEB_UNREACHABLE, "later"),))

        with _writer(db) as session:
            aggregate = run_stats(session)

        assert auth.get(CORPUS_URL).json()["totals"]["web_ok"] == aggregate.web_ok

    def test_a_firmware_row_sums_to_the_same_web_ok(self, db, auth):
        """Belt and braces: the rows and the total are the same number."""
        _register(db, "a.bin")
        _register(db, "b.bin")
        _record(db, iid=1, arch="armel", rootfs="a-rootfs",
                success=True, web_ok=True, duration_sec=40.0)
        _record(db, iid=2, arch="armel", rootfs="b-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.WEB_UNREACHABLE, "b"),))

        body = auth.get(CORPUS_URL).json()

        assert sum(row["web_ok"] for row in body["firmwares"]) == body["totals"]["web_ok"]


class TestTheArchitectureIsNotInvented:
    def test_a_firmware_measured_as_two_architectures_says_mixed(self, db, auth):
        """Picking the most common one would report an agreement the runs disagree
        about, and the stat card counting per run would then disagree with it."""
        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=40.0)
        _record(db, iid=2, arch="mipsel", rootfs="US_AC15-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.WEB_UNREACHABLE, "other"),))

        assert auth.get(CORPUS_URL).json()["firmwares"][0]["arch"] == "mixed"

    def test_a_run_with_no_architecture_reports_a_question_mark(self, db, auth):
        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=40.0)

        assert auth.get(CORPUS_URL).json()["firmwares"][0]["arch"] == "?"

    def test_the_registered_type_is_reported_when_there_is_one(self, db, auth):
        _register(db, "US_AC15.bin", arch="armel", target_type="router")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=40.0)

        row = auth.get(CORPUS_URL).json()["firmwares"][0]

        assert row["target_type"] == "router", row


class TestTheLatencyViewDescribesWhatItMeasured:
    def test_only_successful_runs_contribute_a_sample(self, db, auth):
        """``record_run`` writes ``time_web`` only when the web plane answered, so a
        failed run contributes nothing rather than a slow reading."""
        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=40.0)
        _record(db, iid=2, arch="armel", rootfs="US_AC15-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.WEB_UNREACHABLE, "never came up"),))

        body = auth.get(LATENCY_URL).json()

        assert [row["arch"] for row in body["by_arch"]] == ["armel"], body
        assert body["by_arch"][0]["samples"] == 1, body
        assert body["unmeasured"] == 1, body

    def test_the_median_is_a_time_a_run_actually_took(self, db, auth):
        """Nearest rank, not an average: interpolating would report a median no run
        ever hit, and on a corpus where one run takes minutes that is the number
        that misleads."""
        _register(db, "US_AC15.bin")
        for iid, seconds in enumerate((1, 2, 100), start=1):
            _record(db, iid=iid, arch="armel", rootfs="US_AC15-rootfs",
                    success=True, web_ok=True, duration_sec=float(seconds))

        row = auth.get(LATENCY_URL).json()["by_arch"][0]

        assert row["values"] == [1, 2, 100], row
        assert row["median_sec"] == 2, "an average of 1, 2 and 100 is 34: no run took 34s"
        assert row["p90_sec"] == 100, row
        assert (row["min_sec"], row["max_sec"]) == (1, 100), row

    def test_a_single_sample_is_its_own_median(self, db, auth):
        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=47.0)

        row = auth.get(LATENCY_URL).json()["by_arch"][0]

        assert (row["median_sec"], row["p90_sec"], row["max_sec"]) == (47, 47, 47), row

    def test_each_architecture_is_measured_separately(self, db, auth):
        _register(db, "a.bin")
        _register(db, "b.bin")
        _record(db, iid=1, arch="armel", rootfs="a-rootfs",
                success=True, web_ok=True, duration_sec=40.0)
        _record(db, iid=2, arch="mipsel", rootfs="b-rootfs",
                success=True, web_ok=True, duration_sec=90.0)

        rows = {row["arch"]: row for row in auth.get(LATENCY_URL).json()["by_arch"]}

        assert rows["armel"]["median_sec"] == 40, rows
        assert rows["mipsel"]["median_sec"] == 90, rows

    def test_a_truncated_sample_says_so_and_keeps_the_real_count(self, db, auth, monkeypatch):
        """A library re-run thousands of times must not inflate the response in
        silence -- and the count beside it stays exact."""
        from iris.db import runs as runs_module

        monkeypatch.setattr(runs_module, "_LATENCY_VALUE_LIMIT", 2)
        _register(db, "US_AC15.bin")
        for iid, seconds in enumerate((10, 20, 30, 40), start=1):
            _record(db, iid=iid, arch="armel", rootfs="US_AC15-rootfs",
                    success=True, web_ok=True, duration_sec=float(seconds))

        row = auth.get(LATENCY_URL).json()["by_arch"][0]

        assert row["samples"] == 4, row
        assert row["values"] == [10, 20], row
        assert row["truncated"] is True, row
        assert row["max_sec"] == 40, "the extremes stay exact even when the list is cut"

    def test_an_untruncated_sample_does_not_claim_to_be_one(self, db, auth):
        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=47.0)

        assert auth.get(LATENCY_URL).json()["by_arch"][0]["truncated"] is False

    def test_the_note_says_what_the_number_is_not(self, auth):
        """A reader shown "median 47s" would conclude the boots that never finished
        were merely slower, and that the figure is the web probe's answer."""
        note = auth.get(LATENCY_URL).json()["note"]

        assert "墙钟" in note, note
        assert "只在 web 面有响应时才写入" in note, note


class TestTheFailureMatrixCountsTheWayTheDashboardCounts:
    def test_the_stage_is_the_one_run_stats_resolved(self, db, auth):
        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.WEB_UNREACHABLE, "no :80"),))

        with _writer(db) as session:
            expected = run_stats(session).failure_stages["web-unreachable"]

        body = auth.get(MATRIX_URL).json()
        stages = {row["stage"]: row for row in body["stages"]}

        assert "web-unreachable" in stages[expected]["cells"][0]["kind"], body

    def test_the_worked_network_fallback_is_not_a_failure(self, db, auth):
        """``network-fallback-ok`` is the fallback succeeding. Counting it would paint
        a working corpus as broken -- the exact inversion of what the panel is for."""
        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.NETWORK_FALLBACK_OK, "fallback ran"),
                          Failure(FailureKind.WEB_UNREACHABLE, "no :80")))

        body = auth.get(MATRIX_URL).json()

        kinds = {cell["kind"] for row in body["stages"] for cell in row["cells"]}
        assert "network-fallback-ok" not in kinds, body
        assert body["unclassified"] == 0, body

    def test_a_library_with_only_informational_signals_has_no_rows(self, db, auth):
        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.NETWORK_FALLBACK_OK, "fallback ran"),))

        body = auth.get(MATRIX_URL).json()

        assert body["stages"] == [], body
        assert body["kind_totals"] == {}, body

    def test_the_architecture_comes_from_the_run_not_the_profile(self, db, auth):
        """``failure_profile`` stores no architecture, so the cross joins back to the
        run -- and a profile whose run is gone still has to be counted somewhere."""
        _register(db, "a.bin")
        _record(db, iid=1, arch="armel", rootfs="a-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.WEB_UNREACHABLE, "armel"),))
        _register(db, "b.bin")
        _record(db, iid=2, arch="mipsel", rootfs="b-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.WEB_UNREACHABLE, "mipsel"),))

        body = auth.get(MATRIX_URL).json()
        cell = next(c for row in body["stages"] for c in row["cells"]
                    if c["kind"] == "web-unreachable")

        assert cell["per_arch"] == {"armel": 1, "mipsel": 1}, body
        assert cell["count"] == 2, body

    def test_two_architectures_show_up_as_two_columns(self, db, auth):
        _register(db, "a.bin")
        _record(db, iid=1, arch="armel", rootfs="a-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.WEB_UNREACHABLE, "armel"),))
        _register(db, "b.bin")
        _record(db, iid=2, arch="arm64", rootfs="b-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.WEB_UNREACHABLE, "arm64"),))

        assert auth.get(MATRIX_URL).json()["archs"] == ["arm64", "armel"]

    def test_a_kind_the_taxonomy_never_defined_keeps_the_stage_it_was_written_with(
            self, db, auth):
        """``_stage_of`` falls back to the stored stage for a signal the taxonomy
        does not know, so a row from an older run stays counted instead of vanishing
        out of the totals."""
        _register(db, "US_AC15.bin")
        run_id = _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                         success=True, web_ok=True, duration_sec=40.0)
        _profile(db, run_id, "boot", "a-kind-this-taxonomy-never-defined")

        body = auth.get(MATRIX_URL).json()

        assert [row["stage"] for row in body["stages"]] == ["boot"], body
        assert body["unclassified"] == 0, body

    def test_a_profile_whose_stage_resolves_to_nothing_is_reported_not_dropped(self, db, auth):
        """The last case is a failing signal with no stage anywhere: neither the
        taxonomy nor the row. Dropping it would leave a total that does not add up
        looking like a complete picture."""
        _register(db, "US_AC15.bin")
        run_id = _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                         success=True, web_ok=True, duration_sec=40.0)
        _profile(db, run_id, "", "a-kind-this-taxonomy-never-defined")

        body = auth.get(MATRIX_URL).json()

        assert body["unclassified"] == 1, body
        assert body["stages"] == [], "there is no stage to file it under"
        assert "unclassified" in body["note"], body["note"]

    def test_a_profile_with_no_signal_at_all_is_left_alone(self, db, auth):
        """``signal`` is nullable; a row with nothing in it names no cause and must
        not be filed under the stage it happens to carry."""
        _register(db, "US_AC15.bin")
        run_id = _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                         success=True, web_ok=True, duration_sec=40.0)
        _profile(db, run_id, "service", None)

        body = auth.get(MATRIX_URL).json()

        assert body["stages"] == [], body
        assert body["unclassified"] == 0, body

    def test_two_rows_of_one_kind_do_not_split_into_two_stages(self, db, auth):
        """One kind gets one stage, resolved once by ``run_stats``.

        Deriving it per row instead -- which is what ``_stage_of`` on each row would
        do for a signal the taxonomy does not know, where the row's own stage is the
        only thing to go on -- files each row under whatever that row happens to
        carry. The same failure then appears under two stages on this board while the
        histogram above it lists it once, and neither panel can be checked against
        the other.
        """
        _register(db, "US_AC15.bin")
        run_id = _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                         success=True, web_ok=True, duration_sec=40.0)
        unknown = "a-kind-this-taxonomy-never-defined"
        _profile(db, run_id, "boot", unknown)
        _profile(db, run_id, "service", unknown)

        with _writer(db) as session:
            resolved = run_stats(session).failure_stages[unknown]

        body = auth.get(MATRIX_URL).json()

        assert [row["stage"] for row in body["stages"]] == [resolved], body
        assert body["stages"][0]["total"] == 2, body

    def test_the_stage_rows_add_up_to_the_reported_total(self, db, auth):
        _register(db, "a.bin")
        _record(db, iid=1, arch="armel", rootfs="a-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.WEB_UNREACHABLE, "no :80"),
                          Failure(FailureKind.NETWORK_FALLBACK_OK, "fallback ran")))
        _register(db, "b.bin")
        _record(db, iid=2, arch="mipsel", rootfs="b-rootfs",
                success=False, web_ok=False,
                findings=(Failure(FailureKind.NO_GUEST_IP, "no address"),))

        body = auth.get(MATRIX_URL).json()

        assert sum(row["total"] for row in body["stages"]) == \
            sum(body["kind_totals"].values()), body


class TestAnEmptyLibraryReadsAsEmpty:
    def test_all_three_answer_without_raising(self, auth):
        for url in (CORPUS_URL, LATENCY_URL, MATRIX_URL):
            assert auth.get(url).status_code == 200, url

    def test_nothing_is_invented(self, auth):
        corpus = auth.get(CORPUS_URL).json()
        latency = auth.get(LATENCY_URL).json()
        matrix = auth.get(MATRIX_URL).json()

        assert corpus["firmwares"] == [], corpus
        assert corpus["unattributed"] is None, corpus
        assert corpus["totals"] == {"firmwares": 0, "runs": 0, "web_ok": 0,
                                    "web_reach_rate": 0.0}, corpus
        assert latency["by_arch"] == [], latency
        assert latency["unmeasured"] == 0, latency
        assert matrix["stages"] == [], matrix

    def test_the_denominators_stay_carrying_their_note(self, auth):
        """An empty table is a legitimate answer, but only if it says so."""
        for url in (CORPUS_URL, LATENCY_URL, MATRIX_URL):
            assert auth.get(url).json()["note"].strip(), url


class TestTheRoutesRequireAToken:
    @pytest.mark.parametrize("url", [CORPUS_URL, LATENCY_URL, MATRIX_URL])
    def test_no_token_is_refused(self, client, url):
        """Reading the corpus is reading which firmware was tried and how it failed,
        which is what the token exists to withhold. ``/api/v1/health`` is the only
        open route and none of these is it."""
        assert client.get(url).status_code == 401, url

    @pytest.mark.parametrize("url", [CORPUS_URL, LATENCY_URL, MATRIX_URL])
    def test_a_wrong_token_is_refused(self, client, url):
        client.headers.update({"X-IRIS-Token": "not-the-token"})
        assert client.get(url).status_code == 401, url

    @pytest.mark.parametrize("url", [CORPUS_URL, LATENCY_URL, MATRIX_URL])
    def test_the_caller_is_written_into_the_signature(self, db, url):
        """``caller: Caller`` *is* the guard -- removing the parameter is what opens a
        route, and it is the kind of edit that leaves the handler looking tidier.

        Asserted on the annotation rather than only on a 401 because a wrong token
        still 401s in local mode: with no token configured, ``require_client`` returns
        the loopback identity without comparing anything, so the status-code guards
        above stay green while the route has quietly stopped needing a token.

        Read through ``get_type_hints`` because ``web_app`` runs on postponed
        annotations: what actually sits on the handler is the *string*
        ``"Caller"``, and comparing that to the alias would always miss.
        """
        app = web_app.install(FastAPI())
        route = next(r for r in app.routes if getattr(r, "path", None) == url)

        assert Caller in get_type_hints(route.endpoint, include_extras=True).values(), \
            f"{url} does not name Caller in its signature"


class TestTheResponseModelsRefuseExtraFields:
    """``extra="forbid"`` so a renamed field fails here instead of arriving blank.

    Without it pydantic drops the unknown key and the panel renders an empty cell,
    which is how a contract drift reaches a user instead of a test.
    """

    @pytest.mark.parametrize("model_name,extra", [
        ("FirmwareRow", {"image_id": 1, "label": "x", "arch": "armel", "target_type": "",
                         "runs": 1, "web_ok": 1, "last_result_kind": "", "run_ids": [],
                         "run_ids_total": 1, "run_ids_truncated": False, "extra": 1}),
        ("ArchLatencyRow", {"arch": "armel", "samples": 1, "min_sec": 1, "median_sec": 1,
                            "p90_sec": 1, "max_sec": 1, "values": [1], "truncated": False,
                            "extra": 1}),
        ("FailureStageRow", {"stage": "boot", "total": 1, "cells": [], "extra": 1}),
    ])
    def test_an_unknown_field_is_a_validation_error(self, model_name, extra):
        from iris.api import web_app as module

        with pytest.raises(ValidationError):
            getattr(module, model_name)(**extra)

    def test_a_firmware_row_rejects_an_architecture_outside_the_vocabulary(self):
        from iris.api.web_app import FirmwareRow

        with pytest.raises(ValidationError):
            FirmwareRow(image_id=1, label="x", arch="riscv64", target_type="", runs=1,
                        web_ok=1, last_result_kind="", run_ids=[], run_ids_total=1,
                        run_ids_truncated=False)

    def test_the_three_views_validate_what_their_builders_return(self, db, auth):
        """The models are only worth anything if the payloads fit them, and that is
        a separate fact from the endpoints returning 200."""
        from iris.api.web_app import CorpusViewResponse, FailureMatrixResponse, LatencyViewResponse

        _register(db, "US_AC15.bin")
        _record(db, iid=1, arch="armel", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=47.0)

        CorpusViewResponse(**web_data.corpus_view())
        LatencyViewResponse(**web_data.latency_view())
        FailureMatrixResponse(**web_data.failure_matrix_view())


class TestTheFrontendTypesMatchTheServer:
    """``types.ts`` is hand-written with no generator behind it.

    Nothing else would notice a field renamed on one side only: the payload arrives,
    the response is 200, and the panel shows a blank cell. These compare the two
    declarations key by key, in both directions.
    """

    @staticmethod
    def _frontend_interface(name: str) -> set[str]:
        source = (PROJECT / "web/src/lib/types.ts").read_text(encoding="utf-8")
        body = re.search(rf"export interface {name}\b[^{{]*\{{(.*?)\n\}}", source, re.DOTALL)
        assert body, f"{name} is not declared in types.ts"
        return set(re.findall(r"^\s*(\w+)\??:", body.group(1), re.MULTILINE))

    @staticmethod
    def _server_fields(model) -> set[str]:
        return set(model.model_fields)

    @pytest.mark.parametrize("name,model_name", [
        ("CorpusView", "CorpusViewResponse"),
        ("FirmwareRow", "FirmwareRow"),
        ("CorpusTotals", "CorpusTotals"),
        ("LatencyView", "LatencyViewResponse"),
        ("ArchLatencyRow", "ArchLatencyRow"),
        ("FailureMatrix", "FailureMatrixResponse"),
        ("FailureStageRow", "FailureStageRow"),
        ("FailureCell", "FailureCell"),
    ])
    def test_the_declared_fields_are_the_same_on_both_sides(self, name, model_name):
        from iris.api import web_app as module

        frontend = self._frontend_interface(name)
        server = self._server_fields(getattr(module, model_name))

        assert frontend == server, (
            f"{name} has drifted from {model_name}: "
            f"only in types.ts {sorted(frontend - server)}; "
            f"only on the server {sorted(server - frontend)}"
        )

    def test_the_failure_matrix_hook_exists_for_each_view(self):
        """An endpoint nothing fetches is an endpoint nobody can see."""
        source = (PROJECT / "web/src/lib/api.ts").read_text(encoding="utf-8")
        for path in ("/api/v1/stats/corpus", "/api/v1/stats/latency",
                     "/api/v1/stats/failure-matrix"):
            assert path in source, f"{path} is never requested"

    def test_the_notes_reach_the_dom(self):
        """A note nobody renders is a caveat nobody reads."""
        source = (PROJECT / "web/src/pages/Dashboard.tsx").read_text(encoding="utf-8")
        assert source.count("note") >= 3, "each of the three views renders its own note"


class TestThePanelsStayInsideTheDesignTokens:
    @pytest.mark.parametrize("page", ["CorpusPanel", "LatencyPanel", "FailureMatrixPanel"])
    def test_no_hardcoded_colour_reaches_the_new_panels(self, page):
        source = (PROJECT / "web/src/pages/Dashboard.tsx").read_text(encoding="utf-8")
        body = re.search(rf"function {page}\b.*?(?=\nfunction |\Z)", source, re.DOTALL)
        assert body, f"{page} is not defined on the dashboard"
        text = body.group(0)
        for banned in ("bg-[#", "text-[#", "border-[#", "rgba(", "hsl(", "white", "black"):
            assert banned not in text, f"{page} hardcodes {banned}"

    def test_the_three_panels_are_actually_on_the_dashboard(self):
        source = (PROJECT / "web/src/pages/Dashboard.tsx").read_text(encoding="utf-8")
        for page in ("CorpusPanel", "LatencyPanel", "FailureMatrixPanel"):
            assert f"<{page}" in source, f"{page} is defined but never rendered"

    def test_the_shared_ratio_bar_has_one_implementation(self):
        """Two copies of the same bar drift, and the dashboard and the corpus panel
        would end up showing different ratios for the same runs."""
        dashboard = (PROJECT / "web/src/pages/Dashboard.tsx").read_text(encoding="utf-8")
        ui = (PROJECT / "web/src/components/ui.tsx").read_text(encoding="utf-8")
        assert "function ArchBar" not in dashboard, \
            "ArchBar belongs in ui.tsx now that the corpus panel uses it too"
        assert "function ArchBar" in ui, "ui.tsx has to keep it for both callers"
        assert "from '../components/ui'" in dashboard


class TestTheDbLayerIsUsableOnItsOwn:
    """The three views are built on these, so their behaviour is pinned here too --
    the endpoints above only prove the shape survived the trip."""

    def test_corpus_profile_is_empty_for_an_empty_library(self, db):
        with _writer(db) as session:
            assert corpus_profile(session) == []

    def test_latency_profile_is_empty_for_an_empty_library(self, db):
        with _writer(db) as session:
            assert latency_profile(session) == []

    def test_failure_cross_is_empty_for_an_empty_library(self, db):
        with _writer(db) as session:
            cross = failure_cross(session)
        assert (cross.stages, cross.archs, cross.kind_totals) == ([], [], {})

    def test_a_run_row_is_not_left_half_written(self, db):
        """Every run in these tables must carry an architecture or say it has none;
        a NULL there would otherwise become an arch column with a blank in it."""
        _record(db, iid=1, arch="", rootfs="US_AC15-rootfs",
                success=True, web_ok=True, duration_sec=40.0)

        with _writer(db) as session:
            run = session.scalars(select(EmulationRun)).one()

        assert run.arch == ""

    def test_the_registered_firmware_is_read_from_the_image_table(self, db):
        """The row's name is a join, not the scratch directory: the directory is
        named after the extraction and does not always match what was registered."""
        _register(db, "US_AC15V1.0BR_V15.03.05.18_multi_TD01.bin")
        _record(db, iid=1, arch="armel",
                rootfs="US_AC15V1.0BR_V15.03.05.18_multi_TD-rootfs",
                success=True, web_ok=True, duration_sec=40.0)

        with _writer(db) as session:
            rows = corpus_profile(session)

        assert rows[0].label == "US_AC15V1.0BR_V15.03.05.18_multi_TD01.bin", rows