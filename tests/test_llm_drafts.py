"""Drafts: the directory the engine does not load from, and the one door out of it.

Every claim this feature makes about safety reduces to two facts, and both are tested
here rather than asserted in a docstring:

* **a draft cannot take effect without a person.** ``iris_home/ai-drafts`` is a
  different directory from ``plugin_dir``, the engine loads from the latter, and
  :func:`iris.rules.engine.load_rules` is called against the plugin directory after an
  install to prove the document is one the engine accepts. A test that only checked
  "the file was written" would pass with the two directories merged, which is the
  failure this whole design exists to prevent.
* **installing a draft is not a privileged path.** It goes through
  :func:`iris.api.plugins.install_plugin`, so a document a person could not upload is
  not accepted from a model either. Pinned by installing a draft whose document has an
  unknown key and asserting 422-with-the-loader-s-own-objection and nothing on disk.

The accepted path is pinned too, because it is the loop's whole point: an accepted
draft becomes a loaded plugin *and* leaves a ``repair_action(source="llm",
promoted=True)`` row, which is what makes "the next failure of this kind costs nothing"
visible in the workbench rather than a claim in a changelog.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from iris.api import plugins, web_app
from iris.api.plugins import PluginRejected
from iris.db.models import EmulationRun, RepairAction
from iris.llm import drafts
from iris.llm.drafts import (
    DRAFT_STATUS_ACCEPTED,
    DRAFT_STATUS_PENDING,
    DRAFT_STATUS_REJECTED,
    install_draft,
    list_drafts,
    load_draft,
    reject_draft,
    save_draft,
)
from iris.llm.schema import PluginDraft

TOKEN = "workbench-token"

DRAFT_YAML = """\
id: llm-vendor-fix
description: 让 web 服务在 init 脚本里被拉起
stage: boot
detect:
  - path_exists: etc/init.d/rcS
actions:
  - edit: {}
"""

#: A document with a key the engine does not know. The loader's answer about it is
#: the whole point of routing a draft through the same chain as an upload.
UNKNOWN_KEY_YAML = """\
id: llm-unknown-key
description: 声明了一个引擎不认识的键
stage: boot
detect:
  - always: true
retry_times: 5
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated ``iris_home`` with a built-in rules directory of its own.

    ``Settings.rules_dir`` walks up from the working directory looking for a tree
    holding ``rules/`` and ``src/``, so in a checkout it resolves to the real
    repository -- and a test that installed into it would be editing the shipped rule
    library. It is patched as a class property for the same reason
    ``test_api_plugins`` does: there is no instance field to point elsewhere.
    """
    from iris import config

    builtin = tmp_path / "builtin"
    builtin.mkdir()
    monkeypatch.setattr(config.Settings, "rules_dir", property(lambda self: builtin))
    monkeypatch.setattr(config, "_settings", config.Settings(iris_home=tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def db(home, tmp_path, monkeypatch):
    """An isolated schema, so a promoted-repair row lands in a throwaway database.

    ``database_url`` is repointed as well as ``web_app._session``: the promoted-repair
    write opens its own engine from the settings' URL rather than going through the
    web layer's session factory, so patching only the latter would send the row to
    the session-wide test database -- which has no schema, and would turn a passing
    assertion into a logged warning and a silently missing row.
    """
    from iris import config
    from iris.db.engine import get_engine, init_db, make_session

    url = f"sqlite:///{(tmp_path / 'llm-drafts.db').as_posix()}"
    engine = get_engine(url)
    init_db(engine)
    monkeypatch.setattr(web_app, "_session", lambda: make_session(engine))
    monkeypatch.setattr(config, "_settings", config.get_settings().model_copy(
        update={"database_url": url}
    ))
    return engine


def _draft(rule_id: str = "llm-vendor-fix", yaml_text: str = DRAFT_YAML) -> PluginDraft:
    return PluginDraft(
        rule_id=rule_id,
        stage="boot",
        description="让 web 服务在 init 脚本里被拉起",
        yaml=yaml_text,
    )


def _record_run(engine, iid: int) -> int:
    """One recorded run for ``iid``, so a promoted repair has a parent to hang on."""
    with _writer(engine) as session:
        run = EmulationRun(iid=iid, arch="mips", result=False, result_kind="web_unreachable")
        session.add(run)
        session.commit()
        return run.id


def _writer(engine):
    from iris.db.engine import make_session

    return make_session(engine)


class TestADraftIsNotSomethingTheEngineLoads:
    def test_saving_writes_into_the_draft_directory_not_the_plugin_one(self, home) -> None:
        save_draft(_draft(), iid=7, confidence=0.5, diagnosis="串口日志显示 web 未启动")

        draft_dir = drafts.drafts_dir()
        assert draft_dir == home / "ai-drafts"
        assert (draft_dir / "llm-vendor-fix.yaml").is_file()
        assert (draft_dir / "llm-vendor-fix.meta.json").is_file()
        assert not (home / "plugins").exists(), (
            "a draft that lands in the plugin directory is loaded on the next run "
            "with nobody having read it"
        )

    def test_the_draft_directory_is_outside_every_directory_the_engine_reads(self, home) -> None:
        """Checked against the engine's own load set, not against a hand-written list:
        a new load path added later would be a way this guarantee stops holding."""
        from iris.config import get_settings

        save_draft(_draft(), iid=7, confidence=0.5, diagnosis="x")
        assert drafts.drafts_dir() not in [
            Path(d) for d in get_settings().effective_rules_dirs
        ]
        assert [item["rule"].id for item in plugins.list_plugins()] == []

    def test_the_metadata_is_what_makes_the_document_reviewable(self, home) -> None:
        record = save_draft(_draft(), iid=7, confidence=0.5, diagnosis="web 进程退出")

        meta = json.loads(
            (drafts.drafts_dir() / "llm-vendor-fix.meta.json").read_text(encoding="utf-8")
        )
        assert meta["rule_id"] == "llm-vendor-fix"
        assert meta["source_iid"] == 7
        assert meta["confidence"] == 0.5
        assert meta["diagnosis"] == "web 进程退出"
        assert meta["status"] == DRAFT_STATUS_PENDING
        assert record.created_at and record.created_at[0].isdigit()

    def test_a_pending_id_cannot_be_overwritten(self, home) -> None:
        """Overwriting would silently replace a review that had not happened yet."""
        save_draft(_draft(), iid=7, confidence=0.5, diagnosis="第一次的判断")
        with pytest.raises(PluginRejected, match="不能覆盖"):
            save_draft(_draft(), iid=7, confidence=0.5, diagnosis="第二次的判断")

        loaded, _record = load_draft("llm-vendor-fix")
        assert loaded.yaml == DRAFT_YAML

    @pytest.mark.parametrize("rule_id", ["", "   "])
    def test_a_draft_without_an_id_is_refused(self, home, rule_id) -> None:
        with pytest.raises(PluginRejected, match="rule_id"):
            save_draft(_draft(rule_id=rule_id), iid=None, confidence=0.0, diagnosis="")

    def test_listing_is_newest_first_and_an_empty_directory_is_an_answer(self, home) -> None:
        assert list_drafts() == []
        save_draft(_draft("older"), iid=1, confidence=0.1, diagnosis="旧")
        _age("older", "2026-10-01T00:00:00+00:00")
        save_draft(_draft("newer"), iid=2, confidence=0.2, diagnosis="新")

        assert [record.rule_id for record in list_drafts()] == ["newer", "older"]

    def test_metadata_whose_document_is_gone_is_dropped_not_half_listed(self, home) -> None:
        """A record without its document cannot be reviewed, and listing it would
        hand back an install button that 404s."""
        save_draft(_draft(), iid=7, confidence=0.5, diagnosis="x")
        (drafts.drafts_dir() / "llm-vendor-fix.yaml").unlink()

        assert list_drafts() == []
        assert not (drafts.drafts_dir() / "llm-vendor-fix.meta.json").exists()

    def test_unreadable_metadata_does_not_take_the_listing_down(self, home) -> None:
        save_draft(_draft("good"), iid=1, confidence=0.1, diagnosis="x")
        (drafts.drafts_dir() / "broken.meta.json").write_text("{not json", encoding="utf-8")
        (drafts.drafts_dir() / "broken.yaml").write_text("id: broken\n", encoding="utf-8")

        assert [record.rule_id for record in list_drafts()] == ["good"]


def _age(rule_id: str, created_at: str) -> None:
    """Backdate one draft's timestamp, so ordering is decided by data not by clock."""
    meta_path = drafts.drafts_dir() / f"{rule_id}.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["created_at"] = created_at
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


class TestInstallingADraftGoesThroughTheSameChain:
    def test_a_valid_draft_lands_in_the_plugin_directory_and_loads(self, home) -> None:
        save_draft(_draft(), iid=7, confidence=0.5, diagnosis="x")

        result = install_draft("llm-vendor-fix")

        assert result["installed"] == {
            "id": "llm-vendor-fix",
            "origin": "external",
            "source_file": "llm-vendor-fix.yaml",
        }
        installed = plugins.load_rule_file(home / "plugins" / "llm-vendor-fix.yaml")
        assert installed is not None and installed.warnings == []

    def test_a_document_with_an_unknown_key_is_refused_and_nothing_is_written(self, home) -> None:
        """The engine's answer about an unknown key is "record a warning and ignore
        it", which is why a draft must go through the loader rather than around it."""
        save_draft(_draft("llm-unknown-key", UNKNOWN_KEY_YAML), iid=7, confidence=0.4,
                   diagnosis="x")

        with pytest.raises(PluginRejected):
            install_draft("llm-unknown-key")

        assert not (home / "plugins").exists()
        assert load_draft("llm-unknown-key")[1].status == DRAFT_STATUS_PENDING, (
            "a refused draft stays pending: the loader did not accept it, so the "
            "review has not happened"
        )

    def test_an_accepted_draft_is_marked_and_keeps_its_provenance(self, home) -> None:
        save_draft(_draft(), iid=7, confidence=0.5, diagnosis="web 进程退出")
        install_draft("llm-vendor-fix")

        _document, record = load_draft("llm-vendor-fix")
        assert record.status == DRAFT_STATUS_ACCEPTED
        assert record.source_iid == 7

    def test_installing_an_unknown_draft_is_refused(self, home) -> None:
        with pytest.raises(PluginRejected, match="不存在"):
            install_draft("never-written")


class TestThePromotedRepairIsRecorded:
    def test_accepting_a_draft_leaves_a_promoted_llm_row_against_its_run(self, home, db) -> None:
        """This row is the loop's closing argument: once it exists, the same failure
        next time is a rule-engine hit with no model call."""
        run_id = _record_run(db, iid=7)
        save_draft(_draft(), iid=7, confidence=0.72, diagnosis="web 进程退出")

        assert install_draft("llm-vendor-fix")["promoted_recorded"] is True

        with _writer(db) as session:
            rows = session.scalars(
                select(RepairAction).where(RepairAction.run_id == run_id)
            ).all()
        assert len(rows) == 1
        assert (rows[0].source, rows[0].promoted) == ("llm", True)
        assert rows[0].rule_id == "llm-vendor-fix"
        assert "0.72" in rows[0].evidence and "web 进程退出" in rows[0].evidence

    def test_a_draft_without_a_live_run_is_installed_but_not_recorded(self, home, db) -> None:
        """The missing row is visible rather than wrong: the run was cleared, and a
        fabricated parent would be worse than none."""
        save_draft(_draft(), iid=404, confidence=0.5, diagnosis="x")

        result = install_draft("llm-vendor-fix")

        assert result["promoted_recorded"] is False
        assert (home / "plugins" / "llm-vendor-fix.yaml").is_file()

    def test_a_hand_written_draft_has_no_run_and_says_so(self, home, db) -> None:
        save_draft(_draft(), iid=None, confidence=0.0, diagnosis="")
        assert install_draft("llm-vendor-fix")["promoted_recorded"] is False


class TestRejectingADraft:
    def test_the_document_goes_and_the_record_of_the_refusal_stays(self, home) -> None:
        save_draft(_draft(), iid=7, confidence=0.5, diagnosis="不成立")

        removed = reject_draft("llm-vendor-fix")

        assert removed == drafts.drafts_dir() / "llm-vendor-fix.yaml"
        assert not removed.exists()
        assert _status("llm-vendor-fix") == DRAFT_STATUS_REJECTED

    def test_rejecting_twice_is_refused_rather_than_claiming_a_second_removal(self, home) -> None:
        """The leftover metadata is exactly what makes a stale page's second press a
        refusal: reporting "removed" again would credit two operators with one
        refusal."""
        save_draft(_draft(), iid=7, confidence=0.5, diagnosis="不成立")
        reject_draft("llm-vendor-fix")

        with pytest.raises(PluginRejected, match="不存在"):
            reject_draft("llm-vendor-fix")

    def test_an_accepted_draft_cannot_be_refused_afterwards(self, home) -> None:
        """Its plugin is installed and loading on the next run. A refusal would leave
        the metadata saying "已拒绝" about a document the engine is about to apply --
        the review record contradicting what the engine actually does."""
        save_draft(_draft(), iid=7, confidence=0.5, diagnosis="x")
        install_draft("llm-vendor-fix")

        with pytest.raises(PluginRejected, match="已处理过"):
            reject_draft("llm-vendor-fix")

        assert (home / "plugins" / "llm-vendor-fix.yaml").is_file()
        assert _status("llm-vendor-fix") == DRAFT_STATUS_ACCEPTED

    def test_rejecting_something_that_was_never_saved_is_refused(self, home) -> None:
        with pytest.raises(PluginRejected, match="不存在"):
            reject_draft("never-written")


def _status(rule_id: str) -> str:
    """One draft's status, read from the metadata file directly.

    Not ``load_draft``: that pair-loads the document too, and a rejected draft has
    none by design -- which is the fact this helper is here to read past.
    """
    meta_path = drafts.drafts_dir() / f"{rule_id}.meta.json"
    return json.loads(meta_path.read_text(encoding="utf-8"))["status"]


class TestTheDraftEndpointsOverHttp:
    @pytest.fixture
    def client(self, home, db, monkeypatch) -> TestClient:
        from iris.api import auth as auth_module
        from iris.config import Settings

        monkeypatch.setattr(auth_module, "get_settings", lambda: Settings(api_token=TOKEN))
        return TestClient(web_app.install(FastAPI()))

    @pytest.fixture
    def auth(self, client) -> TestClient:
        client.headers.update({"X-IRIS-Token": TOKEN})
        return client

    def test_the_listing_carries_the_document_text_itself(self, auth) -> None:
        """A review that cannot read the document it is confirming is a rubber stamp,
        so the text on disk is what the response carries -- not the model's summary."""
        save_draft(_draft(), iid=7, confidence=0.5, diagnosis="web 进程退出")

        body = auth.get("/api/v1/ai/drafts").json()

        assert body["drafts"][0]["yaml"] == DRAFT_YAML
        assert body["drafts"][0]["status"] == DRAFT_STATUS_PENDING
        assert "不会进引擎加载路径" in body["note"]

    def test_saving_through_the_api_then_installing_goes_through_the_loader(self, auth, home) -> None:
        auth.post("/api/v1/ai/drafts", json={
            "rule_id": "llm-unknown-key",
            "stage": "boot",
            "description": "声明了一个引擎不认识的键",
            "yaml": UNKNOWN_KEY_YAML,
            "iid": 7,
            "confidence": 0.4,
            "diagnosis": "x",
        })
        assert (drafts.drafts_dir() / "llm-unknown-key.yaml").is_file()

        response = auth.post("/api/v1/ai/drafts/llm-unknown-key/install")

        assert response.status_code == 422
        assert response.json()["detail"]
        assert not (home / "plugins").exists()

    def test_a_pending_draft_that_collides_is_422(self, auth) -> None:
        payload = {"rule_id": "llm-vendor-fix", "stage": "boot",
                   "description": "d", "yaml": DRAFT_YAML}
        assert auth.post("/api/v1/ai/drafts", json=payload).status_code == 200
        assert auth.post("/api/v1/ai/drafts", json=payload).status_code == 422

    def test_removing_a_draft_is_a_404_for_a_stale_page(self, auth) -> None:
        save_draft(_draft(), iid=7, confidence=0.5, diagnosis="x")
        assert auth.delete("/api/v1/ai/drafts/llm-vendor-fix").json() == {
            "removed": "llm-vendor-fix.yaml"
        }
        assert auth.delete("/api/v1/ai/drafts/llm-vendor-fix").status_code == 404

    @pytest.mark.parametrize("body", [
        {"rule_id": "x"},                                   # missing required fields
        {"rule_id": "x", "stage": "boot", "description": "d", "yaml": "y", "nope": 1},
    ])
    def test_an_unknown_key_in_a_save_body_is_refused(self, auth, body) -> None:
        assert auth.post("/api/v1/ai/drafts", json=body).status_code == 422

    def test_confidence_outside_zero_to_one_is_refused(self, auth) -> None:
        response = auth.post("/api/v1/ai/drafts", json={
            "rule_id": "x", "stage": "boot", "description": "d", "yaml": "y",
            "confidence": 2.0,
        })
        assert response.status_code == 422

    @pytest.mark.parametrize("method,path", [
        ("get", "/api/v1/ai/drafts"),
        ("post", "/api/v1/ai/drafts"),
        ("post", "/api/v1/ai/drafts/x/install"),
        ("delete", "/api/v1/ai/drafts/x"),
    ])
    def test_every_draft_route_needs_a_token(self, client, method, path) -> None:
        """The whole point of an endpoint that can change how future runs are
        repaired is that it is behind the same credential as the rest of the API."""
        assert getattr(client, method)(path).status_code == 401