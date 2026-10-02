"""What a real run leaves in the database, and what `iris db stats` says about it.

The tables existed with no write path, so every number in the evaluation log was
maintained by hand. These tests drive `emulate_firmware` with docker stubbed out --
the recording is the part under test, and it must not depend on a guest booting.

`record=False` is available on the orchestrator precisely so that the tests which
are about *emulation* do not also assert things about the database; the ones here
are about the database, so they leave it on.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from iris.db.engine import get_engine, init_db, make_session
from iris.db.models import Brand, EmulationRun, FailureProfile, Image
from iris.db.runs import (
    attribute_to_image,
    failure_histogram,
    record_run,
    run_stats,
    web_reach_rate,
)
from iris.emulate import orchestrator
from iris.failures import Failure, FailureKind

NVRAM = (
    "firmadyne: sys_reboot\n"
    "nvram partition is destory\n"
    "IRIS-NETFIX: no interface has an IP, assigning fallback 192.168.1.1 to eth0\n"
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A throwaway database, independent of whatever conftest points at."""
    url = f"sqlite:///{(tmp_path / 'rec.db').as_posix()}"
    engine = get_engine(url)
    init_db(engine)
    return engine


@pytest.fixture()
def session(db):
    with make_session(db) as s:
        yield s


def _register(session, filename: str) -> int:
    session.add(Brand(name="Tenda"))
    session.flush()
    image = Image(filename=filename, hash=f"h-{filename}", brand_id=1)
    session.add(image)
    session.commit()
    return image.id


class TestRecordRun:
    def test_a_successful_run_writes_one_row_and_no_profiles(self, session, tmp_path):
        run_id = record_run(session, iid=7, arch="arm64",
                            rootfs_dir=tmp_path / "fw-rootfs",
                            success=True, web_ok=True, duration_sec=81.2)
        run = session.get(EmulationRun, run_id)
        assert (run.iid, run.arch, run.web_reachable, run.result) == (7, "arm64", True, True)
        assert run.time_web == 81
        assert list(session.execute(select(FailureProfile))) == []

    def test_a_failed_run_records_the_primary_kind_on_the_run(self, session, tmp_path):
        run_id = record_run(session, iid=7, arch="mipsel", rootfs_dir=tmp_path / "fw-rootfs",
                            success=False, web_ok=False,
                            findings=(Failure(FailureKind.REBOOT_LOOP, "rebooted 28 times"),
                                      Failure(FailureKind.NO_GUEST_IP, "no address")))
        run = session.get(EmulationRun, run_id)
        assert run.result_kind == "reboot-loop"
        assert run.web_reachable is False and run.time_web is None

    def test_every_finding_gets_its_own_profile_row(self, session, tmp_path):
        """One guest usually has several stacked causes; collapsing them hides
        which one to fix first."""
        run_id = record_run(session, iid=1, arch="mipsel", rootfs_dir=tmp_path / "fw-rootfs",
                            success=False, web_ok=False,
                            findings=(Failure(FailureKind.REBOOT_LOOP, "loop"),
                                      Failure(FailureKind.NVRAM_UNREADABLE, "flash"),
                                      Failure(FailureKind.NO_GUEST_IP, "no address")))
        rows = list(session.scalars(
            select(FailureProfile).where(FailureProfile.run_id == run_id)))
        assert {r.signal for r in rows} == {"reboot-loop", "nvram-unreadable", "no-guest-ip"}
        assert {r.stage for r in rows} == {"boot", "nvram", "network"}

    def test_the_hint_is_stored_with_the_row(self, session, tmp_path):
        """The point of the histogram is that the next action is derivable from it."""
        run_id = record_run(session, iid=1, arch="armel", rootfs_dir=tmp_path / "fw-rootfs",
                            success=False, web_ok=False,
                            findings=(Failure(FailureKind.ARCH_MISMATCH, "x"),))
        row = session.scalars(
            select(FailureProfile).where(FailureProfile.run_id == run_id)).one()
        assert row.detail["hint"]
        assert row.log_fingerprint.startswith("arch-mismatch: ")

    def test_an_informational_finding_never_becomes_the_result_kind(self, session, tmp_path):
        run_id = record_run(session, iid=1, arch="armel", rootfs_dir=tmp_path / "fw-rootfs",
                            success=False, web_ok=False,
                            findings=(Failure(FailureKind.NETWORK_FALLBACK_OK, "ran"),
                                      Failure(FailureKind.WEB_NOT_STARTED, "no web")))
        assert session.get(EmulationRun, run_id).result_kind == "web-not-started"

    def test_a_failure_with_no_diagnosis_is_still_recorded(self, session, tmp_path):
        """An unexplained failure is exactly the population a hand-kept table
        drops; it must not vanish for want of a cause."""
        run_id = record_run(session, iid=1, arch="armel", rootfs_dir=tmp_path / "fw-rootfs",
                            success=False, web_ok=False)
        run = session.get(EmulationRun, run_id)
        assert run.result is False
        assert run.result_kind == "web-unreachable"


class TestAttribution:
    def test_a_matching_stem_links_the_corpus_image(self, session, tmp_path):
        image_id = _register(session, "US_TES7002V1.0re_v1.0.0.86_en_plus_cn_TD")
        rootfs = tmp_path / "US_TES7002V1.0re_v1.0.0.86_en_plus_cn_TD-rootfs"
        assert attribute_to_image(session, rootfs) == image_id

    def test_a_truncated_registered_name_still_matches(self, session, tmp_path):
        """`iris db add` stores a truncated filename for long names; the rootfs
        dir keeps the full stem. A strict equality match would attribute nothing."""
        image_id = _register(session, "US_TES7002V1.0re_v1.0.0.86_en_plus_cn_")
        rootfs = tmp_path / "US_TES7002V1.0re_v1.0.0.86_en_plus_cn_TD-rootfs"
        assert attribute_to_image(session, rootfs) == image_id

    def test_the_longest_overlap_wins(self, session, tmp_path):
        session.add(Brand(name="Tenda"))
        session.flush()
        session.add(Image(filename="fw", hash="h1", brand_id=1))
        session.add(Image(filename="fw-rev2", hash="h2", brand_id=1))
        session.commit()
        assert attribute_to_image(session, tmp_path / "fw-rev2-rootfs") == 2

    def test_an_unrelated_rootfs_is_left_unattributed(self, session, tmp_path):
        """A wrong attribution silently corrupts every per-firmware statistic,
        while a missing one is visible in the NULL."""
        _register(session, "G1V31si.bin")
        assert attribute_to_image(session, tmp_path / "something-else-rootfs") is None

    def test_the_image_id_is_stored_on_the_run(self, session, tmp_path):
        image_id = _register(session, "G1V31si.bin")
        run_id = record_run(session, iid=1, arch="mipsel",
                            rootfs_dir=tmp_path / "G1V31si.bin-rootfs",
                            success=True, web_ok=True)
        assert session.get(EmulationRun, run_id).image_id == image_id


class TestAggregates:
    def _seed(self, session, tmp_path):
        record_run(session, iid=1, arch="mipsel", rootfs_dir=tmp_path / "a-rootfs",
                   success=True, web_ok=True, duration_sec=47)
        record_run(session, iid=2, arch="mipsel", rootfs_dir=tmp_path / "b-rootfs",
                   success=False, web_ok=False,
                   findings=(Failure(FailureKind.REBOOT_LOOP, "loop"),
                             Failure(FailureKind.NVRAM_UNREADABLE, "flash")))
        record_run(session, iid=3, arch="armel", rootfs_dir=tmp_path / "c-rootfs",
                   success=True, web_ok=True, duration_sec=12)

    def test_counts_and_reach_rate(self, session, tmp_path):
        self._seed(session, tmp_path)
        stats = run_stats(session)
        assert stats.total == 3
        assert stats.web_ok == 2
        assert web_reach_rate(session) == pytest.approx(2 / 3)

    def test_reach_rate_is_broken_down_by_arch(self, session, tmp_path):
        self._seed(session, tmp_path)
        assert run_stats(session).by_arch == {"mipsel": (2, 1), "armel": (1, 1)}

    def test_the_histogram_counts_kinds_not_runs(self, session, tmp_path):
        """A run with three findings contributes to three buckets; that is the
        point of keeping them apart."""
        self._seed(session, tmp_path)
        histogram = {kind: n for _stage, kind, n in failure_histogram(session)}
        assert histogram == {"reboot-loop": 1, "nvram-unreadable": 1}

    def test_the_histogram_is_grouped_by_stage(self, session, tmp_path):
        self._seed(session, tmp_path)
        stages = {kind: stage for stage, kind, _n in failure_histogram(session)}
        assert stages == {"reboot-loop": "boot", "nvram-unreadable": "nvram"}

    def test_both_views_of_a_failure_agree_on_its_stage(self, session, tmp_path):
        """The histogram and the per-stage totals answer the same question about
        the same row, so they cannot get their stage from two different rules --
        that is how one failure ends up filed under `infra` in one view and
        `boot` in the other."""
        self._seed(session, tmp_path)
        # A row written before the taxonomy existed: a signal no kind maps to,
        # carrying the stage it was stored with.
        run_id = record_run(session, iid=4, arch="mipsel",
                            rootfs_dir=tmp_path / "d-rootfs", success=False, web_ok=False)
        session.add(FailureProfile(run_id=run_id, stage="wizard", signal="hand-typed",
                                    log_fingerprint="a finger, not a kind"))
        session.commit()

        stats = run_stats(session)
        histogram = {kind: stage for stage, kind, _n in failure_histogram(session)}

        for kind, stage in histogram.items():
            assert stats.failure_stages.get(kind) == stage, (
                f"{kind}: histogram says {stage!r}, stats says "
                f"{stats.failure_stages.get(kind)!r}"
            )
        assert histogram["hand-typed"] == "wizard", (
            "a signal the taxonomy does not know keeps the stage it was stored with"
        )

    def test_an_informational_finding_is_absent_from_the_histogram(self, session, tmp_path):
        record_run(session, iid=1, arch="armel", rootfs_dir=tmp_path / "a-rootfs",
                   success=False, web_ok=False,
                   findings=(Failure(FailureKind.NETWORK_FALLBACK_OK, "ran"),
                             Failure(FailureKind.WEB_NOT_STARTED, "no web")))
        assert [kind for _s, kind, _n in failure_histogram(session)] == ["web-not-started"]

    def test_an_empty_database_reports_nothing_rather_than_zero_percent(self, session):
        assert run_stats(session).total == 0
        assert web_reach_rate(session) == 0.0


class TestOrchestratorRecordsEveryOutcome:
    """The early returns matter most: they are the runs that never had a
    container, and they are exactly the ones a hand-kept table forgets."""

    @pytest.fixture(autouse=True)
    def _record_into_this_test_s_database(self, db, monkeypatch):
        """The orchestrator reads the URL from settings; point it at this test's
        own file so the assertions can read back what it wrote."""
        from iris import config

        monkeypatch.setattr(config.get_settings(), "database_url",
                            f"sqlite:///{(Path(db.url.database)).as_posix()}")

    def test_an_unsupported_arch_is_recorded(self, session, tmp_path):
        (tmp_path / "fw-rootfs").mkdir()
        result = orchestrator.emulate_firmware(
            tmp_path / "fw-rootfs", "ppc", 1, tmp_path / "scratch")
        assert result.success is False
        assert result.failure.kind is FailureKind.UNSUPPORTED_ARCH
        runs = list(session.scalars(select(EmulationRun)))
        assert len(runs) == 1
        assert runs[0].result_kind == "unsupported-arch"

    def test_an_image_build_failure_is_recorded(self, session, tmp_path, monkeypatch):
        """Every branch of the pipeline must reach the table, not just the happy one."""
        (tmp_path / "fw-rootfs").mkdir()

        def fake_create(*_a, **_k):
            (tmp_path / "scratch" / "emulate-1").mkdir(parents=True, exist_ok=True)
            (tmp_path / "scratch" / "emulate-1" / "1.tar.gz").write_bytes(b"tar")

        monkeypatch.setattr(orchestrator, "_create_tarball", fake_create)
        monkeypatch.setattr(orchestrator, "safe_stat_size", lambda _p: 3)
        monkeypatch.setattr(orchestrator, "_tarball_is_stale", lambda *a, **k: True)
        monkeypatch.setattr(orchestrator, "_build_baked_image", lambda: "img")

        def fake_exec(cmd, timeout=60):
            # `cmd` is an argv list, so an in-test substring test on it never
            # matches -- it silently returned rc=0 for every call and the run
            # then sat through the real 120s boot wait before reaching the
            # timeout branch, i.e. the case under test never ran at all.
            if any("make_image.sh" in str(part) for part in cmd):
                return subprocess.CompletedProcess(cmd, 1, "", "mkfs failed")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(orchestrator, "_run", fake_exec)
        result = orchestrator.emulate_firmware(tmp_path / "fw-rootfs", "mipsel", 1,
                                                tmp_path / "scratch")
        assert result.failure.kind is FailureKind.IMAGE_BUILD_FAILED
        assert run_stats(session).failures == {"image-build-failed": 1}

    def test_a_broken_database_never_breaks_the_run(self, tmp_path, monkeypatch):
        """A metric write that can fail an emulation makes the measurement part of
        the system under test. The caller must still get its verdict."""
        from iris import config

        (tmp_path / "fw-rootfs").mkdir()
        # A directory where the database file should be: get_engine only creates
        # parents, so opening it fails inside _record_outcome.
        monkeypatch.setattr(config.get_settings(), "database_url",
                            f"sqlite:///{(tmp_path / 'not-a-dir').as_posix()}")
        (tmp_path / "not-a-dir").mkdir()
        result = orchestrator.emulate_firmware(tmp_path / "fw-rootfs", "ppc", 1,
                                                tmp_path / "scratch")
        assert result.failure.kind is FailureKind.UNSUPPORTED_ARCH
        assert result.error

    def test_record_false_leaves_no_trace(self, tmp_path, monkeypatch):
        (tmp_path / "fw-rootfs").mkdir()
        monkeypatch.setattr(orchestrator, "_record_outcome",
                            lambda *a, **k: pytest.fail("recorded despite record=False"))
        result = orchestrator.emulate_firmware(tmp_path / "fw-rootfs", "ppc", 1,
                                                tmp_path / "scratch", record=False)
        assert result.failure is not None


class TestSchemaUpgrade:
    def test_an_old_emulation_run_gains_the_new_columns(self, tmp_path):
        """`create_all` only creates missing tables, so a database created before
        these columns existed would fail every insert that names them -- which
        reads as "recording is broken", not as "this database predates it"."""
        from sqlalchemy import inspect, text

        engine = get_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE emulation_run (id INTEGER PRIMARY KEY, iid INTEGER NOT NULL)"))
            conn.execute(text("INSERT INTO emulation_run (iid) VALUES (42)"))

        init_db(engine)  # must not raise, must not lose the existing row
        columns = {c["name"] for c in inspect(engine).get_columns("emulation_run")}
        assert {"image_id", "arch"} <= columns

        row = engine.connect().execute(text("SELECT iid, image_id FROM emulation_run")).one()
        assert row.iid == 42 and row.image_id is None

    def test_running_init_twice_is_a_no_op(self, tmp_path):
        engine = get_engine(f"sqlite:///{(tmp_path / 'twice.db').as_posix()}")
        init_db(engine)
        init_db(engine)  # the ALTER branch must find nothing left to add


class TestCorpusIsolation:
    """The guard on the guard: the real corpus database must stay untouched."""

    def test_the_suite_does_not_write_to_iris_home(self, session):
        from iris import config

        assert "iris-home" not in config.get_settings().database_url, (
            "tests are pointed at the corpus database; a fake run would become "
            "a fake firmware in the evaluated numbers"
        )
        assert str(Path(session.get_bind().url.database)) != "iris.db"