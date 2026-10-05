"""External rule plugins: what the installer refuses, and what it accepts.

The whole contract of ``iris.api.plugins`` is a promise about the word "installed".
Installing must mean the engine will act on the document, because the engine's own
answer to a key it does not know is "record a warning and ignore it". Every test here
is therefore about the boundary between *rejected* (422, nothing on disk) and
*accepted* (on disk, and the engine loads it without a single warning).

Two directories are in play and both are redirected per test. ``Settings.rules_dir``
walks up from the working directory looking for a tree holding ``rules/`` and
``src/``, so in a checkout it resolves to the real repository and a test that wrote
into it would be editing the rule library. It is patched as a property on the class
for exactly that reason: there is no instance field to point elsewhere.

Neither ``test_docs_claims.py`` nor any other file may depend on the count of the
built-in rules. The assertions below use a purpose-built built-in directory holding
one rule with a known id, so adding or removing a real rule in ``rules/`` cannot
turn any of them red.
"""

from __future__ import annotations

import pytest

from iris.api import plugins, web_app, web_data
from iris.api.plugins import MAX_PLUGIN_BYTES, PluginRejected, install_plugin, remove_plugin

TOKEN = "workbench-token"

#: One built-in rule, used as the collision target. ``id`` deliberately differs from
#: the file name: the six rules IRIS ships happen to be named after their ids, and a
#: test that leaned on that coincidence would pass here and misreport a plugin whose
#: document and filename disagree.
BUILTIN_ID = "shipped-rule"
BUILTIN_YAML = """\
id: shipped-rule
description: rule that ships with the test
stage: boot
detect:
  - path_exists: etc/inittab
actions:
  - edit: {}
"""


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An isolated built-in rules directory and an isolated plugin directory.

    Yields ``(builtin_dir, plugin_dir)``. ``config._settings`` is replaced rather
    than mutated so that ``iris_home`` -- and with it the plugin directory -- points
    into ``tmp_path``; ``conftest`` has already repointed ``database_url`` at a
    throwaway file, and the restored value is whatever the session fixture set.
    """
    from iris import config

    builtin = tmp_path / "builtin"
    builtin.mkdir()
    (builtin / f"{BUILTIN_ID}.yaml").write_text(BUILTIN_YAML, encoding="utf-8")
    monkeypatch.setattr(config.Settings, "rules_dir", property(lambda self: builtin))
    monkeypatch.setattr(config, "_settings", config.Settings(iris_home=tmp_path / "home"))
    return builtin, tmp_path / "home" / "plugins"


def rule_yaml(rule_id: str = "vendor-fix", description: str = "vendor supplied",
              **extra: str) -> bytes:
    """A minimal document the engine accepts, with extra top-level keys appended.

    ``description`` is a named parameter rather than something to pass through
    ``extra``: the template already emits it, and a duplicate key would win by YAML's
    last-one rule rather than by the test's intent.
    """
    body = "\n".join(f"{key}: {value}" for key, value in extra.items())
    return f"id: {rule_id}\ndescription: {description}\nstage: boot\n{body}\n".encode()


def install(name: str = "vendor.yaml", body: bytes | None = None, rule_id: str = "vendor-fix"):
    return install_plugin(name, body if body is not None else rule_yaml(rule_id))


# ------------------------------------------------------------------ happy path


class TestInstallAccepts:
    def test_a_valid_document_lands_in_the_plugin_directory(self, store) -> None:
        """Accepted means three things at once: the file exists, the engine will load
        it, and the answer names what was written."""
        _builtin, plugin_dir = store
        assert install() == {
            "id": "vendor-fix",
            "origin": "external",
            "source_file": "vendor-fix.yaml",
        }
        assert (plugin_dir / "vendor-fix.yaml").is_file()

    def test_the_stored_document_is_what_the_engine_loads(self, store) -> None:
        """Round trip through the engine, not just a file-exists check: a plugin that
        is on disk but not in the load set is exactly the failure this feature was
        built to remove."""
        _builtin, plugin_dir = store
        install()
        loaded = plugins.load_rule_file(plugin_dir / "vendor-fix.yaml")
        assert loaded is not None
        assert loaded.id == "vendor-fix"
        assert loaded.warnings == []

    def test_a_yml_upload_is_stored_under_the_id(self, store) -> None:
        """Storing under the uploaded name would let two documents with one id sit
        side by side under different spellings, with the loser invisible. The id is
        unique by the collision check, so it is the safe name."""
        _builtin, plugin_dir = store
        install("anything-at-all.yml")
        assert sorted(p.name for p in plugin_dir.iterdir()) == ["vendor-fix.yaml"]

    def test_a_document_whose_id_differs_from_its_filename_still_loads(self, store) -> None:
        """The point of carrying ``source_file`` on the rule rather than guessing it
        from the id: a mismatch must not silently drop the document."""
        install()
        entries = plugins.list_plugins()
        assert [item["rule"].id for item in entries] == [BUILTIN_ID, "vendor-fix"]
        assert entries[1]["source_file"] == "vendor-fix.yaml"


# --------------------------------------------------------------------- refusals


class TestInstallRejects:
    @pytest.mark.parametrize(
        ("name", "body", "expect"),
        [
            ("vendor.txt", rule_yaml(), "yaml"),
            ("vendor.yaml", b"", "空"),
            ("vendor.yaml", b"   \n", "空"),
            ("vendor.yaml", b"\xff\xfe\x00bad", "UTF-8"),
            ("vendor.yaml", b"id: [unclosed\n", "YAML"),
            ("vendor.yaml", b"- a\n- b\n", "映射"),
            ("vendor.yaml", b"{}\n", "id"),
            ("vendor.yaml", b"description: no id\n", "id"),
            ("vendor.yaml", b"id: Vendor-Fix\n", "不合法"),
            ("vendor.yaml", b"id: vendor fix\n", "不合法"),
            ("vendor.yaml", b"id: ../../escape\n", "不合法"),
            ("vendor.yaml", rule_yaml(rule_id=BUILTIN_ID), "冲突"),
        ],
    )
    def test_a_rejected_upload_leaves_nothing_on_disk(self, store, name, body, expect) -> None:
        """Every rejection carries a reason an author can act on, and the reason
        names the actual problem rather than the exception class."""
        _builtin, plugin_dir = store
        with pytest.raises(PluginRejected) as caught:
            install(name, body)
        assert expect in caught.value.detail
        assert not plugin_dir.exists() or list(plugin_dir.iterdir()) == []

    def test_an_oversized_document_is_refused_by_the_reader(self, store) -> None:
        """The size cap is enforced while reading, so an oversized body never exists
        in memory whole -- that is the reader's job, tested in ``test_api.py``. Here
        it is asserted that the store refuses one it is handed."""
        _builtin, plugin_dir = store
        with pytest.raises(PluginRejected) as caught:
            install(body=b"id: big\n" + b"x" * MAX_PLUGIN_BYTES)
        assert "上限" in caught.value.detail
        assert not plugin_dir.exists()

    def test_a_document_at_the_cap_is_still_accepted(self, store) -> None:
        """Off-by-one guard on the boundary: the cap rejects *past* it, not at it.
        The padding goes into ``description`` so the id and the document stay valid
        while the body reaches the limit exactly."""
        assert install_plugin(
            "cap.yaml", f"id: cap\ndescription: {'d' * (MAX_PLUGIN_BYTES - 22)}\n".encode()
        )["id"] == "cap"

    @pytest.mark.parametrize(
        ("body", "expect"),
        [
            (rule_yaml(fil_glob="etc/*"), "detect"),
            (rule_yaml(path_exits="etc/inittab"), "detect"),
            (rule_yaml(shel="rm -rf /"), "actions"),
            (rule_yaml(docs="see the manual"), "顶层键"),
        ],
    )
    def test_a_key_the_engine_does_not_know_is_refused(self, store, body, expect) -> None:
        """The engine would load these happily and simply not act on them, which is
        the failure mode worth refusing: an upload that reports success for a
        condition that is never evaluated."""
        _builtin, plugin_dir = store
        with pytest.raises(PluginRejected) as caught:
            install(body=body)
        assert expect in caught.value.detail
        assert not plugin_dir.exists() or list(plugin_dir.iterdir()) == []

    def test_a_typo_inside_a_nested_all_group_is_found(self, store) -> None:
        """The key check recurses, so a misspelling buried two levels down is still
        caught rather than reaching the engine as a silently inert sub-condition."""
        _builtin, _plugin = store
        body = b"id: nested\ndetect:\n  - all:\n      - path_exists: etc/inittab\n      - fil_glob: etc/*\n"
        with pytest.raises(PluginRejected) as caught:
            install(body=body)
        assert "fil_glob" in caught.value.detail

    def test_a_path_in_the_filename_is_refused_not_normalised(self, store) -> None:
        """``Path("a/b.yaml").name`` reduces to ``b.yaml``; accepting that would let
        the request decide where inside the directory the file lands."""
        with pytest.raises(PluginRejected) as caught:
            install("sub/vendor.yaml")
        assert "文件名" in caught.value.detail

    def test_an_empty_filename_is_refused(self, store) -> None:
        with pytest.raises(PluginRejected):
            install("")

    def test_the_engine_warning_is_handed_back_verbatim(self, store) -> None:
        """Platform-independent proof that a warned document is not installed: the
        rejection repeats the engine's own wording rather than paraphrasing it."""
        _builtin, plugin_dir = store
        body = b"id: warned\ndetect:\n  - fil_glob: etc/*\n"
        with pytest.raises(PluginRejected) as caught:
            install(body=body)
        assert "fil_glob" in caught.value.detail
        assert not plugin_dir.exists() or list(plugin_dir.iterdir()) == []


# --------------------------------------------------------------------- removal


class TestRemovePlugin:
    def test_an_installed_plugin_is_removed(self, store) -> None:
        _builtin, plugin_dir = store
        install()
        assert remove_plugin("vendor-fix.yaml").name == "vendor-fix.yaml"
        assert list(plugin_dir.iterdir()) == []

    @pytest.mark.parametrize("name", ["../../rules/x.yaml", "sub/x.yaml", "", ".."])
    def test_a_traversing_name_is_refused(self, store, name) -> None:
        """Refused rather than reduced to a bare file name: normalising
        ``../../rules/x.yaml`` to ``x.yaml`` would delete an unrelated file in the
        plugin directory and answer 200."""
        with pytest.raises(PluginRejected):
            remove_plugin(name)

    def test_an_absent_file_is_refused(self, store) -> None:
        with pytest.raises(PluginRejected) as caught:
            remove_plugin("never-installed.yaml")
        assert "没有这个文件" in caught.value.detail

    def test_a_yml_is_not_removable(self, store) -> None:
        """The installer only ever writes ``.yaml``, so refusing ``.yml`` keeps "what
        I can uninstall" and "what I can see" the same set."""
        _builtin, plugin_dir = store
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "hand-written.yml").write_text(rule_yaml().decode(), encoding="utf-8")
        with pytest.raises(PluginRejected):
            remove_plugin("hand-written.yml")
        assert (plugin_dir / "hand-written.yml").is_file()

    def test_the_built_in_directory_is_out_of_reach(self, store) -> None:
        """There is no name that removes a shipped rule: the plugin directory is the
        only one reachable, and the built-ins are not in it."""
        builtin, _plugin = store
        with pytest.raises(PluginRejected):
            remove_plugin(f"{BUILTIN_ID}.yaml")
        assert (builtin / f"{BUILTIN_ID}.yaml").is_file()


# -------------------------------------------------------------------- listing


class TestListPlugins:
    def test_origin_says_where_each_rule_came_from(self, store) -> None:
        """A merged list that does not distinguish origins cannot answer "can I
        uninstall this one", and editing a shipped rule is not what installing a
        plugin means."""
        install()
        origins = {item["rule"].id: item["origin"] for item in plugins.list_plugins()}
        assert origins == {BUILTIN_ID: "builtin", "vendor-fix": "external"}

    def test_a_plugin_missing_at_first_is_not_an_error(self, tmp_path, monkeypatch) -> None:
        """A fresh install has no plugin directory yet. That is the normal state, so
        the listing reports the built-ins rather than failing."""
        from iris import config

        monkeypatch.setattr(config.Settings, "rules_dir", property(lambda self: tmp_path / "none"))
        monkeypatch.setattr(config, "_settings", config.Settings(iris_home=tmp_path / "home"))
        assert plugins.list_plugins() == []

    def test_a_built_in_wins_a_hand_placed_collision(self, store) -> None:
        """Unreachable through the endpoint, which refuses a colliding id outright --
        so this is somebody dropping a file in. The first definition is kept and the
        loser's absence is visible in the count rather than the shadowing being
        silent."""
        _builtin, plugin_dir = store
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "shadow.yaml").write_text(
            rule_yaml(rule_id=BUILTIN_ID, description="the impostor").decode(), encoding="utf-8"
        )
        entries = plugins.list_plugins()
        assert [item["rule"].id for item in entries] == [BUILTIN_ID]
        assert entries[0]["rule"].description == "rule that ships with the test"


class TestEngineLoadsBothDirectories:
    def test_a_plugin_is_applied_alongside_the_built_ins(self, store) -> None:
        """The whole point of the feature: after an install the engine sees the new
        rule, without anything re-pointing a directory."""
        from iris.config import get_settings
        from iris.rules.engine import load_all_rules

        install()
        ids = [rule.id for rule in load_all_rules(get_settings().effective_rules_dirs)]
        assert ids == [BUILTIN_ID, "vendor-fix"]


# ---------------------------------------------------------------------- routes


@pytest.fixture
def client(store):
    """A TestClient over a freshly installed dashboard, with the ledger reachable.

    ``web_app.install`` is idempotent per app, so a new ``FastAPI`` per test keeps
    this file from registering the catch-all on the app ``test_web_app.py`` drives.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from iris.db.engine import get_engine, init_db, make_session

    engine = get_engine(f"sqlite:///{(store[0].parent / 'web.db').as_posix()}")
    init_db(engine)
    import iris.api.web_data as wd

    original = wd._session
    wd._session = lambda: make_session(engine)
    try:
        yield TestClient(web_app.install(FastAPI()))
    finally:
        wd._session = original


def upload(client, name: str = "vendor.yaml", body: bytes | None = None):
    return client.post(
        "/api/v1/plugins",
        files={"file": (name, body if body is not None else rule_yaml(), "application/x-yaml")},
    )


class TestPluginRoutes:
    def test_both_new_routes_require_the_token(self, client, monkeypatch) -> None:
        """Every stateful route depends on the token; a plugin installer that ran
        unauthenticated would let anyone on the network write into the rule set."""
        from iris.config import Settings

        monkeypatch.setattr("iris.api.auth.get_settings", lambda: Settings(api_token=TOKEN))
        assert client.post("/api/v1/plugins",
                           files={"file": ("v.yaml", rule_yaml())}).status_code == 401
        assert client.delete("/api/v1/plugins/vendor-fix.yaml").status_code == 401

    def test_installing_returns_the_document_it_wrote(self, client) -> None:
        resp = upload(client)
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"id": "vendor-fix", "origin": "external", "source_file": "vendor-fix.yaml"}
        # ``extra="forbid"`` is only worth having if the shape is asserted from the
        # outside, so this counts the keys rather than trusting the model.
        assert set(body) == {"id", "origin", "source_file"}

    def test_an_invalid_upload_is_422_with_a_reason_the_author_can_use(self, client) -> None:
        resp = upload(client, body=b"id: bad\nfil_glob: etc/*\n")
        assert resp.status_code == 422
        assert "顶层键" in resp.json()["detail"]

    def test_removing_echoes_the_name_it_deleted(self, client) -> None:
        upload(client)
        resp = client.delete("/api/v1/plugins/vendor-fix.yaml")
        assert resp.status_code == 200
        assert resp.json() == {"removed": "vendor-fix.yaml"}

    def test_removing_twice_is_404(self, client) -> None:
        """A stale page re-sending a delete should re-fetch, not retry: 404 rather
        than 422 says the thing it asked for is gone."""
        upload(client)
        client.delete("/api/v1/plugins/vendor-fix.yaml")
        assert client.delete("/api/v1/plugins/vendor-fix.yaml").status_code == 404

    def test_a_traversing_delete_never_reaches_the_built_ins(self, client) -> None:
        """Records where the traversal is actually stopped, which is not where this
        file's other test would lead you to expect it.

        A ``{name}`` path parameter cannot carry a slash: the ASGI layer decodes
        ``%2F`` before routing, so the URL collapses onto ``/api/v1/rules/...`` and
        never matches this route -- hence 405 rather than 404. That is the stronger
        position, but it makes ``remove_plugin``'s own name check look redundant from
        the outside, and it is not: it is the only guard for any caller that is not
        an HTTP client. ``TestRemovePlugin::test_a_traversing_name_is_refused``
        covers that one, and the built-in file is asserted still on disk here.
        """
        resp = client.delete("/api/v1/plugins/..%2F..%2Frules%2Fshipped-rule.yaml")
        assert resp.status_code in {404, 405}
        assert client.get("/api/v1/rules").json()["items"][0]["id"] == BUILTIN_ID

    def test_the_listing_reports_both_directories_and_the_origin(self, client) -> None:
        """What the page renders: where rules are read from, and which of them the
        operator uploaded."""
        upload(client)
        body = client.get("/api/v1/rules").json()
        assert len(body["dirs"]) == 2
        assert body["dirs"][1].endswith("plugins")
        origins = {item["id"]: item["origin"] for item in body["items"]}
        assert origins == {BUILTIN_ID: "builtin", "vendor-fix": "external"}

    def test_an_installed_plugin_shows_up_with_no_reload(self, client) -> None:
        """The page must not need a restart to see what it just uploaded."""
        assert "vendor-fix" not in {i["id"] for i in client.get("/api/v1/rules").json()["items"]}
        upload(client)
        assert "vendor-fix" in {i["id"] for i in client.get("/api/v1/rules").json()["items"]}


class TestRuleListingStaysHonest:
    def test_the_tallies_are_counts_of_ledger_rows_not_of_matches(self, store, monkeypatch) -> None:
        """``origin`` and ``source_file`` were added to the item; the counts that were
        already there must keep meaning what they meant -- repairs *attempted and
        recorded* against a rule id, not the engine's per-run match report. A row that
        was never applied still increments ``recorded``, which is the distinction a
        reader is entitled to check rather than take on trust."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from iris.db.engine import get_engine, init_db, make_session
        from iris.db.models import EmulationRun, RepairAction

        engine = get_engine(f"sqlite:///{(store[0].parent / 'tally.db').as_posix()}")
        init_db(engine)
        monkeypatch.setattr(web_data, "_session", lambda: make_session(engine))
        with make_session(engine) as session:
            run = EmulationRun(iid=7100, arch="mipsel", web_reachable=False,
                               ping_reachable=False, ip="10.0.2.15", result=False)
            session.add(run)
            session.commit()
            session.add(RepairAction(run_id=run.id, source="rules", rule_id=BUILTIN_ID,
                                     applied=False, promoted=False))
            session.commit()

        body = TestClient(web_app.install(FastAPI())).get("/api/v1/rules").json()
        item = next(i for i in body["items"] if i["id"] == BUILTIN_ID)
        assert (item["recorded"], item["applied"], item["promoted"]) == (1, 0, 0)
        assert item["origin"] == "builtin"
        assert item["source_file"] == f"{BUILTIN_ID}.yaml"

    def test_an_absent_rules_directory_yields_an_empty_list(self, tmp_path, monkeypatch) -> None:
        """A wheel install has no ``rules/`` beside it, and a plugin directory that has
        never been created is the normal state of a fresh install. Both must read as
        "nothing here" rather than as an error, because a plugin centre that 500s is
        worse than one that honestly shows nothing."""
        from iris import config
        from iris.db.engine import get_engine, init_db, make_session

        monkeypatch.setattr(config.Settings, "rules_dir",
                            property(lambda self: tmp_path / "absent"))
        monkeypatch.setattr(config, "_settings", config.Settings(iris_home=tmp_path / "home"))
        engine = get_engine(f"sqlite:///{(tmp_path / 'empty.db').as_posix()}")
        init_db(engine)
        monkeypatch.setattr(web_data, "_session", lambda: make_session(engine))
        body = web_data.rule_plugins()
        assert body["items"] == []
        assert len(body["dirs"]) == 2


def test_plugin_dir_lives_under_iris_home(tmp_path, monkeypatch) -> None:
    """The plugin directory is inside ``iris_home``, so ``IRIS_IRIS_HOME`` moves the
    installed plugins along with everything else -- which is what makes an isolated
    verification run isolated. It also keeps uploaded plugins out of the git-tracked
    source tree, which is why ``effective_rules_dirs`` lists a second directory at all
    instead of pointing ``rules_dir`` somewhere writable."""
    from iris import config

    monkeypatch.setattr(config, "_settings", config.Settings(iris_home=tmp_path))
    assert config.get_settings().plugin_dir == tmp_path / "plugins"
    assert config.get_settings().effective_rules_dirs[1] == tmp_path / "plugins"
    assert config.get_settings().effective_rules_dirs[0] == config.get_settings().rules_dir


def test_the_installer_validates_against_the_engine_s_own_key_lists() -> None:
    """Against ``engine``'s lists rather than a second copy: a copy drifts the first
    time a key is added, and then uploads start being rejected for using a key the
    engine supports."""
    from iris.rules import engine

    assert plugins.RULE_KEYS is engine.RULE_KEYS
    assert plugins.DETECT_KEYS is engine.DETECT_KEYS
    assert plugins.ACTION_KEYS is engine.ACTION_KEYS