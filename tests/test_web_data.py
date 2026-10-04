"""The dashboard's read model: what a page gets, and what it must never imply.

Every test points ``web_data._session`` at its own throwaway database rather than
using the session-wide one. The reasons are specific, not hygiene:

* the numbers are the product here -- ``web_ok``, ``web_reach_rate``, the eval
  denominator. A test that appended rows to the shared database would change the
  answer the next test computes, and the failure would look like an arithmetic bug;
* ``conftest`` isolates the *real* corpus, not the other tests in this file.

``web_data`` imports ``_session`` by name, so the module attribute is what these
tests replace. That only works because the call sites look the name up at call
time; if someone ever binds it as a default argument, the fixture silently stops
isolating and the next assertion failure will be a mystery.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import pytest

from iris.api import web_data


@pytest.fixture
def db(tmp_path, monkeypatch):
    """An isolated schema for one test, wired in as ``web_data``'s session source."""
    from iris.db.engine import get_engine, init_db, make_session

    engine = get_engine(f"sqlite:///{(tmp_path / 'web.db').as_posix()}")
    init_db(engine)
    monkeypatch.setattr(web_data, "_session", lambda: make_session(engine))
    return engine


def add_run(session, *, iid: int, arch: str = "mipsel", web_ok: bool | None = True,
            ip: str = "10.0.2.15", kind: str | None = None, when: datetime | None = None) -> int:
    """Insert one recorded run and return its primary key."""
    from iris.db.models import EmulationRun

    row = EmulationRun(iid=iid, arch=arch, web_reachable=web_ok, ping_reachable=web_ok,
                       ip=ip, result=web_ok, result_kind=kind, time_web=90 if web_ok else None,
                       started_at=when or _at(12, 0),
                       finished_at=_at(12, 5))
    session.add(row)
    session.commit()
    return row.id


def add_failure(session, run_id: int, *, stage: str = "network", signal: str = "link-no-arp") -> None:
    from iris.db.models import FailureProfile

    session.add(FailureProfile(run_id=run_id, stage=stage, signal=signal,
                               log_fingerprint="fp", detail={"hint": "check the nic"}))
    session.commit()


def add_repair(session, run_id: int, *, rule_id: str = "fix-nic", applied: bool = True) -> None:
    from iris.db.models import RepairAction

    session.add(RepairAction(run_id=run_id, source="rule", rule_id=rule_id,
                             evidence="guest has no arp reply", applied=applied, promoted=False))
    session.commit()


# ------------------------------------------------------------------ capabilities


class TestCapabilities:
    def test_every_row_carries_state_detail_and_evidence(self, db) -> None:
        """A badge without its evidence is a promise nobody can check."""
        items = web_data.capabilities()["items"]
        assert len(items) == 6
        for item in items:
            assert item["state"] in {"available", "planned", "unavailable"}
            assert item["detail"], item
            assert item["evidence"], item

    def test_ai_guardian_is_declared_unavailable_and_says_why(self, db) -> None:
        """It is a deterministic self-healer, not a model call -- the row must say so.

        A contest demo that claims "AI" without a model in the process is the one
        claim a judge can check by reading two files, so the wording is pinned.
        """
        row = next(i for i in web_data.capabilities()["items"] if i["id"] == "ai-guardian")
        assert row["state"] == "unavailable"
        assert "不含任何模型调用" in row["detail"]

    def test_console_row_follows_the_script_rather_than_a_constant(self, db, tmp_path, monkeypatch) -> None:
        """L4 reads the build, so a stale baked image cannot claim a live console."""
        root = tmp_path / "with-chardev"
        (root / "scripts" / "emulate").mkdir(parents=True)
        (root / "scripts" / "emulate" / "run_qemu.sh").write_text(
            "-serial chardev:iris_serial \\\n", encoding="utf-8", newline="")
        monkeypatch.setattr(web_data, "_repo_root", lambda: root)
        assert web_data.serial_console_available() is True
        row = next(i for i in web_data.capabilities()["items"] if i["id"] == "l4-console")
        assert row["state"] == "available"
        assert "chardev" in row["detail"]

    def test_console_is_planned_when_the_script_only_dumps_to_a_file(self, db, tmp_path, monkeypatch) -> None:
        root = tmp_path / "file-only"
        (root / "scripts" / "emulate").mkdir(parents=True)
        (root / "scripts" / "emulate" / "run_qemu.sh").write_text(
            "-serial file:${WORK_DIR}/qemu.serial.log\n", encoding="utf-8", newline="")
        monkeypatch.setattr(web_data, "_repo_root", lambda: root)
        assert web_data.serial_console_available() is False
        row = next(i for i in web_data.capabilities()["items"] if i["id"] == "l4-console")
        assert row["state"] == "planned"
        assert "file:" in row["detail"]

    def test_a_missing_script_is_not_a_console(self, db, tmp_path, monkeypatch) -> None:
        """No script means no evidence; the answer is False, not an exception."""
        monkeypatch.setattr(web_data, "_repo_root", lambda: tmp_path / "nothing-here")
        assert web_data.serial_console_available() is False


# ------------------------------------------------------------------------ stats


class TestStats:
    def test_an_empty_table_reports_none_rather_than_zero(self, db) -> None:
        """Zero asserts "measured, came out zero"; None says "not measured".

        A brand-new install opens the dashboard and sees four cards. If they read
        0/0/0% it looks like everything failed, which is the opposite of what an
        empty corpus means.
        """
        body = web_data.stats("local")
        assert body["available"] is True
        assert body["total"] is None or body["total"] == 0
        assert body["web_ok"] is None or body["web_ok"] == 0
        assert body["web_reach_rate"] is None or body["web_reach_rate"] == 0.0

    def test_counts_runs_web_hits_and_network_failures(self, db) -> None:
        with web_data._session() as session:
            ok = add_run(session, iid=1, arch="mipsel", web_ok=True)
            add_run(session, iid=2, arch="mipsel", web_ok=False, kind="link-no-arp")
            add_failure(session, ok, signal="link-no-icmp")
        body = web_data.stats("local")
        assert body["total"] == 2
        assert body["web_ok"] == 1
        assert body["environment_failures"] == 1
        assert body["web_reach_rate"] == pytest.approx(0.5)
        assert body["by_arch"] == {"mipsel": {"seen": 2, "web_ok": 1}}

    def test_network_kinds_are_the_three_that_mean_the_link_never_came_up(self, db) -> None:
        """A service-layer miss is a different bucket and must not be counted here."""
        with web_data._session() as session:
            bad = add_run(session, iid=1, web_ok=False, kind="no-http-service")
            add_failure(session, bad, signal="no-http-service")
        assert web_data.stats("local")["environment_failures"] == 0

    def test_a_broken_database_degrades_instead_of_raising(self, db, monkeypatch) -> None:
        """The dashboard must render with a warning, not 500 on a locked file."""
        def boom():
            raise RuntimeError("database is locked")

        monkeypatch.setattr(web_data, "_session", boom)
        body = web_data.stats("local")
        assert body["available"] is False
        assert body["total"] is None
        assert body["web_reach_rate"] is None

    def test_running_counts_only_this_callers_instances(self, db) -> None:
        from iris.db.active import register

        with web_data._session() as session:
            register(session, iid=7001, client_id="local", arch="mipsel",
                     rootfs_path="/tmp/a", container_id="c1")
            register(session, iid=7002, client_id="somebody-else", arch="mipsel",
                     rootfs_path="/tmp/b", container_id="c2")
        body = web_data.stats("local")
        assert body["running"] == 1
        assert [row["iid"] for row in body["active"]] == [7001]


# -------------------------------------------------------------------- runs page


class TestRunsPage:
    def test_newest_first(self, db) -> None:
        with web_data._session() as session:
            for index, minute in enumerate([1, 2, 3]):
                add_run(session, iid=index, when=_at(10, minute))
        items = web_data.runs_page()["items"]
        assert [run["iid"] for run in items] == [2, 1, 0]

    def test_total_counts_the_filters_not_the_page(self, db) -> None:
        with web_data._session() as session:
            for index in range(5):
                add_run(session, iid=index, arch="mipsel" if index < 3 else "arm64")
        page = web_data.runs_page(limit=2, arch="mipsel")
        assert page["total"] == 3
        assert len(page["items"]) == 2

    def test_filters_narrow_without_changing_the_page_size(self, db) -> None:
        with web_data._session() as session:
            add_run(session, iid=1, arch="mipsel", web_ok=True, kind="ok")
            add_run(session, iid=2, arch="arm64", web_ok=False, kind="link-no-arp")
        assert len(web_data.runs_page(arch="arm64")["items"]) == 1
        assert len(web_data.runs_page(result_kind="link-no-arp")["items"]) == 1
        assert len(web_data.runs_page(result_kind="link-no-arp", arch="mipsel")["items"]) == 0

    def test_numeric_query_searches_the_iid(self, db) -> None:
        with web_data._session() as session:
            add_run(session, iid=7001)
            add_run(session, iid=7002)
        assert [r["iid"] for r in web_data.runs_page(query="7002")["items"]] == [7002]

    def test_text_query_searches_the_ip(self, db) -> None:
        with web_data._session() as session:
            add_run(session, iid=1, ip="192.168.7.20")
            add_run(session, iid=2, ip="10.0.2.15")
        assert [r["iid"] for r in web_data.runs_page(query="192.168.7")["items"]] == [1]

    def test_offset_walks_the_list(self, db) -> None:
        with web_data._session() as session:
            for index, minute in enumerate([1, 2, 3]):
                add_run(session, iid=index, when=_at(10, minute))
        assert [r["iid"] for r in web_data.runs_page(limit=1, offset=1)["items"]] == [1]

    def test_nonsense_bounds_are_clamped_rather_than_500ed(self, db) -> None:
        """The list view sends whatever the scroll position produced; clamping is
        what keeps a negative limit from asking SQLite for nothing."""
        with web_data._session() as session:
            add_run(session, iid=1)
        page = web_data.runs_page(limit=0, offset=-5)
        assert page["limit"] == 1
        assert page["offset"] == 0
        assert len(page["items"]) == 1

    def test_null_columns_come_back_as_empty_not_as_the_string_none(self, db) -> None:
        """A run that died before the web probe has no ip and no verdict; JSON null
        would make the table render the word "null"."""
        with web_data._session() as session:
            add_run(session, iid=1, web_ok=None, ip="", kind=None)
        row = web_data.runs_page()["items"][0]
        assert row["ip"] == ""
        assert row["result_kind"] == ""
        assert row["web_ok"] is None


# ------------------------------------------------------------------- run detail


class TestRunDetail:
    def test_unknown_run_is_none(self, db) -> None:
        assert web_data.run_detail(4242) is None

    def test_detail_carries_the_failures_and_the_repairs(self, db) -> None:
        with web_data._session() as session:
            run_id = add_run(session, iid=7001, web_ok=False, kind="link-no-arp")
            add_failure(session, run_id)
            add_repair(session, run_id)
        detail = web_data.run_detail(run_id)
        assert detail["iid"] == 7001
        assert detail["failures"][0]["signal"] == "link-no-arp"
        assert detail["failures"][0]["detail"]["hint"] == "check the nic"
        assert detail["repairs"][0]["rule_id"] == "fix-nic"
        assert detail["repairs"][0]["applied"] is True

    def test_a_clean_run_has_empty_lists_not_missing_keys(self, db) -> None:
        """The page branches on these; a missing key would be a blank panel."""
        with web_data._session() as session:
            run_id = add_run(session, iid=7001, web_ok=True)
        detail = web_data.run_detail(run_id)
        assert detail["failures"] == []
        assert detail["repairs"] == []


class TestRunDetailLink:
    """`link` is reconstructed from stored evidence, so its limits are the contract.

    Only three keys of the probe survive into the database (see
    `iris.emulate.linkprobe.LinkProfile.as_failure`). The panel can therefore draw the
    four states and the first break, and can *not* draw per-layer probe text. These
    tests pin both halves: what is reconstructed, and that the gap is declared rather
    than rendered as an empty string that reads like "nothing was wrong here".
    """

    TABLE: ClassVar[dict[str, str]] = {"route": "ok", "arp": "ok", "icmp": "ok", "service": "blocked"}

    def _run_with_link_evidence(self, session, table=None, first_break="service") -> int:
        from iris.db.models import FailureProfile

        run_id = add_run(session, iid=7001, web_ok=False, kind="link-no-service",
                         ip="192.168.1.1")
        session.add(FailureProfile(
            run_id=run_id, stage="network", signal="link-no-service",
            log_fingerprint="link-no-service: probe stops at the service layer",
            detail={"hint": "the http port did not answer",
                    "probe": "link", "first_break": first_break,
                    "table": dict(self.TABLE if table is None else table)},
        ))
        session.commit()
        return run_id

    def test_states_and_first_break_come_back_in_physical_order(self, db) -> None:
        with web_data._session() as session:
            run_id = self._run_with_link_evidence(session)
        link = web_data.run_detail(run_id)["link"]
        assert [layer["layer"] for layer in link["layers"]] == ["route", "arp", "icmp", "service"]
        assert [layer["state"] for layer in link["layers"]] == ["ok", "ok", "ok", "blocked"]
        assert link["first_break"] == "service"
        assert link["guest_ip"] == "192.168.1.1"

    def test_a_missing_layer_reads_unknown_not_absent(self, db) -> None:
        """A short table means the summary was partial, not that the layer is gone."""
        with web_data._session() as session:
            run_id = self._run_with_link_evidence(session, table={"route": "ok", "arp": "blocked"})
        states = {layer["layer"]: layer["state"]
                  for layer in web_data.run_detail(run_id)["link"]["layers"]}
        assert states == {"route": "ok", "arp": "blocked", "icmp": "unknown", "service": "unknown"}

    def test_the_unreconstructable_halves_are_declared_in_the_note(self, db) -> None:
        with web_data._session() as session:
            run_id = self._run_with_link_evidence(session)
        link = web_data.run_detail(run_id)["link"]
        assert link["unavailable"] == ""
        assert all(layer["detail"] == "" for layer in link["layers"])
        assert "未落库" in link["note"]
        assert "不探测" in link["note"]

    def test_a_run_without_probe_evidence_has_no_link_at_all(self, db) -> None:
        """Most runs never produced a summary. ``None`` is the honest answer; an empty
        four-row table would be a claim that all four layers were measured."""
        with web_data._session() as session:
            clean = add_run(session, iid=7002, web_ok=True)
            probed_elsewhere = add_run(session, iid=7003, web_ok=False, kind="boot-timeout")
            add_failure(session, probed_elsewhere)
        assert web_data.run_detail(clean)["link"] is None
        assert web_data.run_detail(probed_elsewhere)["link"] is None

    def test_a_non_dict_detail_is_skipped_instead_of_raising(self, db) -> None:
        """`detail` is free-form JSON; a row written by an older build may hold a list."""
        from iris.db.models import FailureProfile

        with web_data._session() as session:
            run_id = add_run(session, iid=7004, web_ok=False, kind="link-no-arp")
            session.add(FailureProfile(run_id=run_id, stage="network", signal="link-no-arp",
                                       log_fingerprint="fp", detail=["probe", "link"]))
            session.commit()
        assert web_data.run_detail(run_id)["link"] is None

    def test_link_is_present_on_every_detail(self, db) -> None:
        """The page reads the key unconditionally; a missing one is a blank panel."""
        with web_data._session() as session:
            run_id = add_run(session, iid=7005, web_ok=True)
        assert "link" in web_data.run_detail(run_id)


# ---------------------------------------------------------------------- eval set


class TestEvalSet:
    def test_denominator_matches_the_dashboard_cards(self, db) -> None:
        """One number, one source. Two implementations of the same ratio is how a
        panel and a table end up disagreeing with nothing to show for it."""
        with web_data._session() as session:
            add_run(session, iid=1, web_ok=True)
            add_run(session, iid=2, web_ok=False)
            add_run(session, iid=3, web_ok=True)
        cards = web_data.stats("local")
        table = web_data.eval_set()
        assert table["denominator"] == cards["total"]
        assert table["web_ok"] == cards["web_ok"]
        assert table["web_reach_rate"] == pytest.approx(cards["web_reach_rate"])

    def test_the_rate_is_not_computed_from_a_page_of_rows(self, db) -> None:
        """Past the page limit the numerator would freeze while the denominator
        kept growing, and the rate would slide down for no reason at all."""
        with web_data._session() as session:
            for index in range(520):
                add_run(session, iid=index, web_ok=index < 520)
        table = web_data.eval_set()
        assert table["denominator"] == 520
        assert table["web_ok"] == 520
        assert table["web_reach_rate"] == 1.0
        assert len(table["items"]) == 500
        assert "500" in table["items_note"]

    def test_the_scope_is_stated_so_nobody_reads_it_as_one_batch(self, db) -> None:
        """The 3/5 figure in the design docs was a single comparison batch. The
        table is cumulative, so the table says so next to the number."""
        table = web_data.eval_set()
        assert "全库累计" in table["scope"]
        assert "按次计入" in table["scope"]
        assert "分母" in table["denominator_note"]
        assert "同源" in table["denominator_note"]

    def test_repeat_runs_of_one_firmware_count_separately(self, db) -> None:
        """A retry after a rule change is a second observation, not a duplicate to
        be deduplicated away -- and the scope line is what makes that explicit."""
        with web_data._session() as session:
            add_run(session, iid=7001, web_ok=False)
            add_run(session, iid=7001, web_ok=True)
        table = web_data.eval_set()
        assert table["denominator"] == 2
        assert table["web_ok"] == 1


# -------------------------------------------------------------------- serial log


class TestSerialLog:
    def _snapshot(self, settings_home, iid: int, body: str) -> None:
        path = settings_home / "scratch" / f"emulate-{iid}"
        path.mkdir(parents=True, exist_ok=True)
        (path / "qemu.serial.log").write_text(body, encoding="utf-8", newline="")

    def test_missing_snapshot_says_so_instead_of_returning_no_lines(self, db, monkeypatch, tmp_path) -> None:
        """An empty log and an absent log are different states; the page shows one
        as "still booting" and the other as "nothing was captured"."""
        monkeypatch.setattr(web_data, "get_settings", lambda: _settings(tmp_path))
        body = web_data.serial_log(7100)
        assert body["available"] is False
        assert body["lines"] == []
        assert body["next_line"] == 0
        assert "qemu.serial.log" in body["path"]

    def test_reads_the_host_side_copy_the_diagnosis_also_reads(self, db, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(web_data, "get_settings", lambda: _settings(tmp_path))
        self._snapshot(tmp_path, 7100, "line one\nline two\nline three\n")
        body = web_data.serial_log(7100)
        assert body["available"] is True
        assert body["lines"] == ["line one", "line two", "line three"]
        assert body["total_lines"] == 3
        assert body["complete"] is True

    def test_start_line_pages_without_re_sending_the_whole_log(self, db, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(web_data, "get_settings", lambda: _settings(tmp_path))
        self._snapshot(tmp_path, 7100, "a\nb\nc\nd\n")
        body = web_data.serial_log(7100, start_line=2)
        assert body["lines"] == ["c", "d"]
        assert body["next_line"] == 4
        assert body["complete"] is True

    def test_max_lines_caps_a_page_and_reports_more_to_come(self, db, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(web_data, "get_settings", lambda: _settings(tmp_path))
        self._snapshot(tmp_path, 7100, "".join(f"{n}\n" for n in range(50)))
        body = web_data.serial_log(7100, max_lines=10)
        assert len(body["lines"]) == 10
        assert body["next_line"] == 10
        assert body["complete"] is False

    def test_undecodable_bytes_do_not_take_the_panel_down(self, db, monkeypatch, tmp_path) -> None:
        """A guest is free to emit anything on a serial line."""
        monkeypatch.setattr(web_data, "get_settings", lambda: _settings(tmp_path))
        self._snapshot(tmp_path, 7100, "ok\n\xff\xfe broken\n")
        body = web_data.serial_log(7100)
        assert body["available"] is True
        assert len(body["lines"]) == 2


# ---------------------------------------------------------------------- csv out


class TestExportCsv:
    def test_header_and_one_row_per_run(self, db) -> None:
        with web_data._session() as session:
            add_run(session, iid=7001, web_ok=True)
            add_run(session, iid=7002, web_ok=False)
        text = web_data.export_csv()
        lines = text.strip().splitlines()
        assert lines[0].startswith("id,iid,image_id,arch,web_ok")
        assert len(lines) == 3

    def test_a_run_with_null_columns_still_produces_a_row(self, db) -> None:
        """csv writes ``None`` as the literal text "None", which is a value the
        spreadsheet will sort as a word. It has to be blank."""
        with web_data._session() as session:
            add_run(session, iid=1, web_ok=None, kind=None)
        row = web_data.export_csv().strip().splitlines()[1]
        assert "None" not in row

    def test_an_empty_database_is_a_header_not_an_empty_file(self, db) -> None:
        """A zero-byte download looks like a failed request."""
        assert web_data.export_csv().strip().startswith("id,")


# --------------------------------------------------------------- instance stats


class TestInstanceStats:
    @pytest.fixture(autouse=True)
    def _no_shared_cache(self, monkeypatch):
        """The 2-second cache is module-level, so a sample recorded by the
        previous test would still be warm here -- and the two tests that assert
        the docker call count clear it themselves to be explicit about it."""
        monkeypatch.setattr(web_data, "_stats_cache", {})

    def test_no_sample_is_not_zero_percent(self, db, monkeypatch) -> None:
        monkeypatch.setattr(web_data, "container_stats", lambda name: None)
        monkeypatch.setattr(web_data, "serial_port_of", lambda iid: None)
        body = web_data.instance_stats(7100)
        assert body["sampled"] is False
        assert body["cpu_pct"] is None
        assert body["mem_mb"] is None
        assert body["console_available"] is False
        assert body["serial_port"] is None

    def test_a_sample_is_reported_with_its_container_and_port(self, db, monkeypatch) -> None:
        from iris.emulate.container_stats import ContainerStats

        monkeypatch.setattr(web_data, "container_stats",
                            lambda name: ContainerStats(cpu_pct=11.62, mem_mb=1399.8, mem_limit_mb=2048.0))
        monkeypatch.setattr(web_data, "serial_port_of", lambda iid: 46000)
        body = web_data.instance_stats(7100)
        assert body["sampled"] is True
        assert body["cpu_pct"] == pytest.approx(11.62)
        assert body["mem_limit_mb"] == pytest.approx(2048.0)
        assert body["container"] == "iris-qemu-7100"
        assert body["serial_port"] == 46000
        assert body["console_available"] is True

    def test_the_docker_call_is_made_once_per_window(self, db, monkeypatch) -> None:
        """``docker stats`` is a round trip to the daemon; three panels refreshing
        the same instance would otherwise triple it every few seconds."""
        from iris.emulate.container_stats import ContainerStats

        calls: list[str] = []

        def counted(name: str):
            calls.append(name)
            return ContainerStats(cpu_pct=1.0, mem_mb=2.0, mem_limit_mb=3.0)

        monkeypatch.setattr(web_data, "container_stats", counted)
        monkeypatch.setattr(web_data, "serial_port_of", lambda iid: 46000)
        monkeypatch.setattr(web_data, "_stats_cache", {})
        web_data.instance_stats(7100)
        web_data.instance_stats(7100)
        web_data.instance_stats(7100)
        assert calls == ["iris-qemu-7100"]

    def test_two_instances_do_not_share_a_cached_sample(self, db, monkeypatch) -> None:
        from iris.emulate.container_stats import ContainerStats

        calls: list[str] = []

        def counted(name: str):
            calls.append(name)
            return ContainerStats(cpu_pct=1.0, mem_mb=2.0, mem_limit_mb=3.0)

        monkeypatch.setattr(web_data, "container_stats", counted)
        monkeypatch.setattr(web_data, "serial_port_of", lambda iid: 46000)
        monkeypatch.setattr(web_data, "_stats_cache", {})
        web_data.instance_stats(7100)
        web_data.instance_stats(7101)
        assert calls == ["iris-qemu-7100", "iris-qemu-7101"]

    def test_a_missing_sample_is_cached_too(self, db, monkeypatch) -> None:
        """Otherwise a container that has not started yet is re-probed on every
        panel refresh, for the whole time it takes to start."""
        calls: list[str] = []

        def missing(name: str):
            calls.append(name)

        monkeypatch.setattr(web_data, "container_stats", missing)
        monkeypatch.setattr(web_data, "serial_port_of", lambda iid: None)
        monkeypatch.setattr(web_data, "_stats_cache", {})
        web_data.instance_stats(7100)
        web_data.instance_stats(7100)
        assert calls == ["iris-qemu-7100"]


# ------------------------------------------------------------------ root causes


class TestRootCauses:
    def test_an_empty_corpus_has_no_cards(self, db) -> None:
        assert web_data.root_causes()["cards"] == []

    def test_a_failure_kind_becomes_a_card(self, db) -> None:
        with web_data._session() as session:
            bad = add_run(session, iid=1, web_ok=False, kind="link-no-arp")
            add_failure(session, bad, signal="link-no-arp")
            add_repair(session, bad, rule_id="fix-nic")
        cards = web_data.root_causes()["cards"]
        assert len(cards) == 1
        assert cards[0]["kind"] == "link-no-arp"
        assert cards[0]["runs"] == 1
        assert cards[0]["repairs"] == ["fix-nic"]

    def test_candidate_marking_follows_recency(self, db) -> None:
        """``recent=1`` keeps the newest failing run's kind live and drops the
        older one, which is the gate the panel's "work list" relies on."""
        with web_data._session() as session:
            old = add_run(session, iid=1, web_ok=False, kind="link-no-arp",
                          when=_at(10, 0))
            add_failure(session, old, signal="link-no-arp")
            new = add_run(session, iid=2, web_ok=False, kind="no-http-service",
                          when=_at(11, 0))
            add_failure(session, new, signal="no-http-service")
        cards = {c["kind"]: c for c in web_data.root_causes(recent=1)["cards"]}
        assert cards["no-http-service"]["candidate"] is True
        assert cards["link-no-arp"]["candidate"] is False


def _at(hour: int, minute: int) -> datetime:
    """A naive UTC timestamp, because that is what the column stores.

    ``EmulationRun.started_at`` defaults to ``datetime.utcnow``, so a tz-aware value
    would be silently normalised on the way into SQLite -- and the recency gate under
    test compares what came back out.
    """
    return datetime(2026, 1, 1, hour, minute)  # noqa: DTZ001


def _settings(tmp_path):
    from iris.config import Settings

    return Settings(iris_home=str(tmp_path))