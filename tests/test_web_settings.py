"""The settings store, and the panel that writes it.

The gap 0.3.29 left open was not a missing feature but a missing *route*: six LLM
settings and four runtime ones existed on ``Settings`` and could only be reached
by hand-editing ``.env``. This file pins the way that was closed, and most of what
it pins are the *refusals*, because the whole safety argument for letting a browser
write configuration rests on them:

* **only the ten named fields are writable.** ``api_token`` is refused because the
  request that writes it can lock its own author out of the route that would undo
  it; ``iris_home`` and ``database_url`` are refused because they name stores that
  already hold data, so a panel that changed them would promise a migration nothing
  performs;
* **``.env`` is never written.** The panel writes ``<iris_home>/settings.json``,
  which is IRIS's own file and sits *below* the environment, so ``IRIS_*`` in a
  launch script stays authoritative;
* **a credential is never echoed.** ``llm_api_key`` is reported as configured or
  not, on both the read and the write path, the same bargain ``api_token`` keeps;
* **a save is atomic.** An interrupted write must not leave a truncated file that
  the next start cannot read;
* **a corrupt file is loud.** Silently treating it as "nothing saved" would show
  defaults that no longer apply to anything.

The frontend contract is pinned at the end: ``types.ts`` is hand-written with no
generator behind it, so a field renamed on one side and not the other is a blank
input in the panel.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iris.api import web_app
from iris.config_store import (
    EDITABLE_KEYS,
    EDITABLE_SETTINGS,
    LOG_LEVELS,
    SECRET_KEYS,
    SETTING_FILENAME,
    SettingRejected,
    clear_settings_file,
    normalise_patch,
    read_settings_file,
    settings_path,
    stored_values,
    write_settings_file,
)

TOKEN = "workbench-token"
PROJECT = Path(__file__).resolve().parents[1]

CONFIG_URL = "/api/v1/config"

#: The three fields whose absence from the table is a decision, not an oversight.
#: Each is asserted against ``EDITABLE_KEYS`` directly so that adding one back has
#: to come with a change to this tuple.
UNWRITABLE = ("api_token", "iris_home", "database_url")


@pytest.fixture
def home(tmp_path) -> Path:
    """An isolated data root, so a save never lands in the real ``iris-home``."""
    return tmp_path / "home"


@pytest.fixture
def client(home, monkeypatch) -> TestClient:
    """A workbench whose settings live in ``tmp_path`` and whose token is set.

    One patched singleton rather than two modules, because ``iris.api.web_app`` and
    ``iris.api.auth`` both read ``iris.config.get_settings()`` -- so the token the
    refusals below assert on and the values the routes report are the same object.
    Building on the session's settings keeps the isolated ``database_url`` from
    ``conftest`` rather than replacing it.

    The token matters for the refusal tests below: without one the server is in
    local mode and hands out the loopback identity, so an auth assertion would pass
    for the wrong reason.
    """
    from iris import config

    home.mkdir()
    monkeypatch.setattr(
        config,
        "_settings",
        config.get_settings().model_copy(update={"iris_home": home, "api_token": TOKEN}),
    )
    return TestClient(web_app.install(FastAPI()))


@pytest.fixture
def auth(client) -> TestClient:
    client.headers.update({"X-IRIS-Token": TOKEN})
    return client


def _rows(payload: dict) -> dict[str, dict]:
    return {row["key"]: row for row in payload["rows"]}


class TestTheTableOfWritableFields:
    """``EDITABLE_SETTINGS`` is the single definition of what a browser may write,
    so these are the tests that keep it honest."""

    def test_it_covers_the_llm_layer_and_the_runtime_fields(self) -> None:
        """The gap this feature closes, stated as the table itself."""

        assert {
            "llm_base_url",
            "llm_api_key",
            "llm_model",
            "llm_timeout_sec",
            "llm_max_retries",
            "llm_max_context_chars",
            "log_level",
            "log_to_file",
            "api_max_upload_mb",
            "download_mirror",
        } == set(EDITABLE_KEYS)

    @pytest.mark.parametrize("key", UNWRITABLE)
    def test_the_dangerous_three_are_not_in_it(self, key: str) -> None:
        assert key not in EDITABLE_KEYS

    def test_every_key_is_a_real_field_on_settings(self) -> None:
        """A key that is not on ``Settings`` would be accepted by the store and then
        silently ignored by the build -- a save that reports success and changes
        nothing."""

        from iris.config import Settings

        fields = set(Settings.model_fields)
        assert set(EDITABLE_KEYS) <= fields

    def test_the_llm_settings_are_adjacent_in_the_table(self) -> None:
        """Ordered by subsystem, not alphabetically: the person opening the panel is
        looking for "the LLM one", and a sorted list puts six settings in three
        separate places."""

        keys = [item.key for item in EDITABLE_SETTINGS]
        llm = [i for i, key in enumerate(keys) if key.startswith("llm_")]
        assert llm == list(range(llm[0], llm[0] + len(llm)))

    def test_the_widget_vocabulary_is_closed(self) -> None:
        """``kind`` drives both the input widget and the JSON type. A fifth value
        would render as a raw text box for a switch, sending ``"true"`` where a
        boolean belongs."""

        assert {item.kind for item in EDITABLE_SETTINGS} <= {"text", "number", "switch", "secret"}

    def test_every_number_carries_its_bounds(self) -> None:
        """Unbounded numbers are how ``api_max_upload_mb = 0`` rejects every upload
        and ``llm_timeout_sec = -1`` fails every diagnosis in a way that reads as a
        broken endpoint."""

        for item in EDITABLE_SETTINGS:
            if item.kind == "number":
                assert item.minimum is not None and item.maximum is not None
                assert item.minimum < item.maximum

    def test_a_secret_is_the_only_thing_in_the_secret_list(self) -> None:
        assert SECRET_KEYS == {item.key for item in EDITABLE_SETTINGS if item.kind == "secret"}
        assert SECRET_KEYS == {"llm_api_key"}


class TestReadingAndWriting:
    def test_nothing_saved_reads_as_empty(self, home: Path) -> None:
        assert read_settings_file(home) == {}

    def test_a_save_lands_under_the_home_not_in_the_working_directory(self, home: Path) -> None:
        """It moves with the data, which is the whole reason it is not ``.env``."""

        write_settings_file(home, {"llm_model": "qwen2.5:7b"})
        assert settings_path(home) == home / SETTING_FILENAME

    def test_a_save_merges_rather_than_replaces(self, home: Path) -> None:
        """Partial saves are what the panel does: saving the LLM endpoint must not
        revert an upload limit someone set an hour ago."""

        write_settings_file(home, {"llm_model": "qwen2.5:7b"})
        write_settings_file(home, {"api_max_upload_mb": 128})
        stored = read_settings_file(home)
        assert stored == {"llm_model": "qwen2.5:7b", "api_max_upload_mb": 128}

    def test_a_reset_drops_only_the_named_keys(self, home: Path) -> None:
        write_settings_file(home, {"llm_model": "qwen2.5:7b", "llm_timeout_sec": 30.0})
        clear_settings_file(home, ["llm_model"])
        assert read_settings_file(home) == {"llm_timeout_sec": 30.0}

    def test_resetting_a_key_that_was_never_saved_is_not_an_error(self, home: Path) -> None:
        """The panel offers the button whenever a row has a stored value, but the
        file may have been edited underneath it."""

        clear_settings_file(home, ["llm_model"])
        assert read_settings_file(home) == {}

    def test_a_save_leaves_no_temp_file_behind(self, home: Path) -> None:
        """The rename consumes the temp file. A leftover ``settings.json.tmp`` would
        be a copy of the settings file, secret included, sitting next to it."""

        write_settings_file(home, {"llm_api_key": "sk-secret"})
        assert [p.name for p in home.iterdir()] == [SETTING_FILENAME]

    def test_the_written_file_is_valid_json_with_a_trailing_newline(self, home: Path) -> None:
        write_settings_file(home, {"llm_model": "qwen2.5:7b", "llm_api_key": "sk-secret"})
        text = settings_path(home).read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert json.loads(text)["llm_api_key"] == "sk-secret"

    def test_a_corrupt_file_is_an_error_rather_than_an_empty_dict(self, home: Path) -> None:
        """Silently reading this as "nothing saved" would show defaults that no
        longer apply to anything -- the one outcome nobody could debug."""

        home.mkdir()
        settings_path(home).write_text("{not json", encoding="utf-8")
        with pytest.raises(SettingRejected):
            read_settings_file(home)

    def test_a_file_that_is_not_an_object_is_refused(self, home: Path) -> None:
        home.mkdir()
        settings_path(home).write_text("[1, 2]", encoding="utf-8")
        with pytest.raises(SettingRejected):
            read_settings_file(home)

    def test_a_key_outside_the_table_is_dropped_on_write(self, home: Path) -> None:
        """Someone hand-adds a key to the file. It survives neither the write nor the
        read into ``Settings``, so keeping it would be keeping a lie."""

        home.mkdir()
        settings_path(home).write_text('{"llm_model": "m", "api_token": "stolen"}', encoding="utf-8")
        write_settings_file(home, {"log_level": "DEBUG"})
        assert read_settings_file(home) == {"llm_model": "m", "log_level": "DEBUG"}


class TestTheStoreRedactsSecrets:
    def test_stored_values_turns_a_secret_into_a_bool(self, home: Path) -> None:
        write_settings_file(home, {"llm_api_key": "sk-secret", "llm_model": "m"})
        reported = stored_values(home)
        assert reported["llm_api_key"] is True
        assert reported["llm_model"] == "m"

    def test_an_absent_secret_reads_as_false(self, home: Path) -> None:
        assert stored_values(home).get("llm_api_key", False) is False


class TestWhatTheStoreRefuses:
    """These are the tests the feature stands or falls on: a panel that can write
    any field of ``Settings`` is a panel that can lock its author out."""

    @pytest.mark.parametrize("key", UNWRITABLE)
    def test_a_field_outside_the_table_is_refused(self, key: str) -> None:
        with pytest.raises(SettingRejected) as caught:
            normalise_patch({key: "anything"})
        assert key in caught.value.detail

    def test_the_refusal_names_every_offending_key_at_once(self) -> None:
        """Fixing a form one error per round trip is a worse experience than being
        told the whole list."""

        with pytest.raises(SettingRejected) as caught:
            normalise_patch({"api_token": "x", "iris_home": "y", "llm_model": "ok"})
        assert "api_token" in caught.value.detail and "iris_home" in caught.value.detail
        # The one legal key in the same patch is not what was refused.
        assert "llm_model" not in caught.value.detail

    def test_a_number_below_its_floor_is_refused(self) -> None:
        with pytest.raises(SettingRejected) as caught:
            normalise_patch({"llm_timeout_sec": 0})
        assert "1" in caught.value.detail

    def test_a_number_above_its_ceiling_is_refused(self) -> None:
        with pytest.raises(SettingRejected):
            normalise_patch({"api_max_upload_mb": 99999})

    def test_a_number_sent_as_text_is_refused(self) -> None:
        """``"64"`` and ``64`` in the same setting is how a file ends up holding two
        answers to one question, and the widget is what decides which is sent."""

        with pytest.raises(SettingRejected):
            normalise_patch({"api_max_upload_mb": "64"})

    def test_a_switch_sent_as_text_is_refused(self) -> None:
        with pytest.raises(SettingRejected):
            normalise_patch({"log_to_file": "true"})

    @pytest.mark.parametrize("level", ["TRACE", "debugging", "", "verbose"])
    def test_a_log_level_outside_the_enum_is_refused(self, level: str) -> None:
        """``logging.getLevelName`` ignores an unknown level silently, so a typo here
        would be the reason nobody found out their logs were not more verbose."""

        with pytest.raises(SettingRejected):
            normalise_patch({"log_level": level})

    @pytest.mark.parametrize("level", LOG_LEVELS)
    def test_every_declared_log_level_is_accepted(self, level: str) -> None:
        assert normalise_patch({"log_level": level})["log_level"] == level

    def test_a_log_level_is_upper_cased_on_the_way_in(self) -> None:
        """Otherwise ``debug`` and ``DEBUG`` are two spellings of one setting, and a
        row comparing the saved value against the live one reports a pending restart
        on a setting that is already in effect."""

        assert normalise_patch({"log_level": "debug"})["log_level"] == "DEBUG"

    def test_a_refused_patch_writes_nothing(self, home: Path) -> None:
        """Checked field by field before any write, so one bad value in a form of ten
        does not leave nine of them on disk."""

        with pytest.raises(SettingRejected):
            write_settings_file(home, {"llm_model": "m", "api_max_upload_mb": 0})
        assert not settings_path(home).exists()

    def test_resetting_an_unwritable_field_is_refused(self, home: Path) -> None:
        with pytest.raises(SettingRejected):
            clear_settings_file(home, ["api_token"])
        assert not settings_path(home).exists()


class TestTheOverlaySitsBetweenInitAndTheEnvironment:
    """The precedence is the argument for the whole feature: a value clicked in the
    panel has to beat a stale ``.env`` and still lose to ``IRIS_*`` in a launch
    script, or either the panel lies or a deployment is silently reconfigured."""

    def test_a_saved_value_beats_the_default(self, home: Path, monkeypatch) -> None:
        from iris.config import Settings

        monkeypatch.setenv("IRIS_IRIS_HOME", str(home))
        write_settings_file(home, {"llm_model": "qwen2.5:7b"})
        assert Settings().llm_model == "qwen2.5:7b"

    def test_a_saved_value_beats_an_environment_variable(self, home: Path, monkeypatch) -> None:
        from iris.config import Settings

        monkeypatch.setenv("IRIS_IRIS_HOME", str(home))
        monkeypatch.setenv("IRIS_LLM_MODEL", "from-the-launch-script")
        write_settings_file(home, {"llm_model": "qwen2.5:7b"})
        assert Settings().llm_model == "qwen2.5:7b"

    def test_the_environment_still_reaches_a_field_nothing_saved(self, home: Path, monkeypatch) -> None:
        """Otherwise a panel saving one field would freeze the rest of the
        deployment's configuration at its defaults."""

        from iris.config import Settings

        monkeypatch.setenv("IRIS_IRIS_HOME", str(home))
        monkeypatch.setenv("IRIS_LLM_MODEL", "from-the-launch-script")
        write_settings_file(home, {"llm_timeout_sec": 30.0})
        assert Settings().llm_model == "from-the-launch-script"
        assert Settings().llm_timeout_sec == 30.0

    def test_an_explicit_argument_still_wins(self, home: Path, monkeypatch) -> None:
        """A test that passes ``Settings(llm_model=...)`` means it, so the overlay
        stays below ``init_settings``."""

        from iris.config import Settings

        monkeypatch.setenv("IRIS_IRIS_HOME", str(home))
        write_settings_file(home, {"llm_model": "qwen2.5:7b"})
        assert Settings(llm_model="explicit").llm_model == "explicit"

    def test_a_reset_hands_the_field_back_to_the_environment(
        self, home: Path, monkeypatch
    ) -> None:
        from iris.config import Settings

        monkeypatch.setenv("IRIS_IRIS_HOME", str(home))
        monkeypatch.setenv("IRIS_LLM_MODEL", "from-the-launch-script")
        write_settings_file(home, {"llm_model": "qwen2.5:7b"})
        clear_settings_file(home, ["llm_model"])
        assert Settings().llm_model == "from-the-launch-script"

    def test_a_corrupt_file_still_starts_the_service(self, home: Path, monkeypatch) -> None:
        """Refusing to boot over a corrupt preferences file would turn a panel
        mistake into an outage: the emulation itself depends on none of these
        fields. It degrades to the environment and says so."""

        from iris.config import Settings

        monkeypatch.setenv("IRIS_IRIS_HOME", str(home))
        monkeypatch.setenv("IRIS_LLM_MODEL", "from-the-launch-script")
        home.mkdir()
        settings_path(home).write_text("{truncated", encoding="utf-8")
        assert Settings().llm_model == "from-the-launch-script"

    def test_the_overlay_file_follows_a_relocated_home(
        self, tmp_path, monkeypatch
    ) -> None:
        """The home is read by a separate probe class rather than a nested
        ``Settings``, because a nested one would re-enter
        ``settings_customise_sources`` and look for the very file whose location it
        is discovering -- and recurse until the stack ran out."""

        from iris.config import Settings

        relocated = tmp_path / "elsewhere"
        relocated.mkdir()
        write_settings_file(relocated, {"llm_model": "moved-with-the-data"})
        monkeypatch.setenv("IRIS_IRIS_HOME", str(relocated))
        assert Settings().llm_model == "moved-with-the-data"


class TestTheRoutesAreBehindTheToken:
    """Every one of these routes writes something that changes how the service
    behaves. An unauthenticated write is how a browser becomes a remote config
    editor."""

    @pytest.mark.parametrize("method,path", [
        ("get", CONFIG_URL),
        ("put", CONFIG_URL),
        ("delete", f"{CONFIG_URL}?keys=llm_model"),
    ])
    def test_an_anonymous_call_is_refused(self, client: TestClient, method: str, path: str) -> None:
        body = {"values": {"llm_model": "m"}} if method == "put" else None
        assert client.request(method, path, json=body).status_code == 401


class TestReadingTheConfiguration:
    def test_it_offers_every_writable_field_as_a_row(self, auth: TestClient) -> None:
        rows = _rows(auth.get(CONFIG_URL).json())
        assert set(rows) == set(EDITABLE_KEYS)

    def test_a_row_carries_everything_the_form_needs(self, auth: TestClient) -> None:
        row = _rows(auth.get(CONFIG_URL).json())["llm_timeout_sec"]
        assert row["kind"] == "number"
        assert row["minimum"] is not None and row["maximum"] is not None
        assert row["help"]

    def test_it_reports_the_effective_values(self, auth: TestClient) -> None:
        """The row is the running configuration, not just an empty form: somebody
        opening the panel needs to see what is in effect."""

        body = auth.get(CONFIG_URL).json()
        rows = _rows(body)
        assert rows["api_max_upload_mb"]["value"] == 64
        assert rows["download_mirror"]["value"].startswith("https://")

    def test_the_api_token_is_reported_as_a_fact_and_not_as_a_value(self, auth: TestClient) -> None:
        assert auth.get(CONFIG_URL).json()["api_token_configured"] is True
        assert TOKEN not in auth.get(CONFIG_URL).text

    def test_a_saved_value_is_reported_as_stored_and_pending(self, auth: TestClient) -> None:
        """``get_settings()`` is a singleton with no reload, so "saved" and "in
        effect" are different facts until the next start."""

        assert auth.put(CONFIG_URL, json={"values": {"api_max_upload_mb": 128}}).status_code == 200
        row = _rows(auth.get(CONFIG_URL).json())["api_max_upload_mb"]
        assert row["stored"] == 128
        assert row["value"] == 64
        assert row["pending_restart"] is True

    def test_a_saved_value_already_in_effect_is_not_pending(
        self, auth: TestClient, home: Path
    ) -> None:
        """The comparison has to survive a JSON round trip: ``64.0`` in the file
        against a live ``64`` would otherwise report a pending restart forever on a
        setting that is already in effect."""

        write_settings_file(
            home,
            {"api_max_upload_mb": 64, "download_mirror": "https://gh-proxy.com/"},
        )
        rows = _rows(auth.get(CONFIG_URL).json())
        assert rows["api_max_upload_mb"]["pending_restart"] is False
        assert rows["download_mirror"]["pending_restart"] is False

    def test_a_saved_field_the_environment_outranks_is_still_pending(
        self, auth: TestClient, home: Path
    ) -> None:
        """Not the other way round: the overlay sits *above* ``IRIS_*``, so a saved
        value that differs from the live one is a genuine pending restart. Pinned
        because the natural reading of the badge is "the environment will win", and
        here it will not -- the precedence itself is verified in
        ``TestTheOverlaySitsBetweenInitAndTheEnvironment``."""

        write_settings_file(home, {"log_level": "WARNING"})
        row = _rows(auth.get(CONFIG_URL).json())["log_level"]
        assert row["value"] == "INFO"
        assert row["stored"] == "WARNING"
        assert row["pending_restart"] is True

    def test_a_saved_secret_reports_presence_not_value(self, auth: TestClient) -> None:
        auth.put(CONFIG_URL, json={"values": {"llm_api_key": "sk-do-not-leak"}})
        row = _rows(auth.get(CONFIG_URL).json())["llm_api_key"]
        assert row["secret_configured"] is True
        # ``stored`` answers "is there an override on file", which is ``True`` here.
        # Reporting ``False`` for an unsaved key would claim the file said "no key"
        # when it said nothing at all.
        assert row["stored"] is True
        assert row["value"] is None

    def test_a_secret_is_never_reported_as_pending(self, auth: TestClient) -> None:
        """Deciding whether a saved key is the running one means comparing against the
        key, and comparing means reading it. The row says "configured" instead."""

        auth.put(CONFIG_URL, json={"values": {"llm_api_key": "sk-do-not-leak"}})
        assert _rows(auth.get(CONFIG_URL).json())["llm_api_key"]["pending_restart"] is False

    def test_an_unconfigured_secret_reports_nothing_on_file(self, auth: TestClient) -> None:
        row = _rows(auth.get(CONFIG_URL).json())["llm_api_key"]
        assert row["secret_configured"] is False
        assert row["stored"] is None

    def test_a_saved_secret_can_be_cleared_from_the_panel(self, auth: TestClient, home: Path) -> None:
        """Otherwise a key set through the panel could never be removed through it,
        which is the "set it and you cannot take it back" failure the ``DELETE``
        route exists to avoid."""

        auth.put(CONFIG_URL, json={"values": {"llm_api_key": "sk-do-not-leak"}})
        assert _rows(auth.get(CONFIG_URL).json())["llm_api_key"]["stored"] is not None
        auth.delete(CONFIG_URL, params={"keys": ["llm_api_key"]})
        assert _rows(auth.get(CONFIG_URL).json())["llm_api_key"]["stored"] is None
        assert "llm_api_key" not in read_settings_file(home)

    def test_an_unreadable_store_reports_itself_instead_of_failing(
        self, auth: TestClient, home: Path
    ) -> None:
        """The form stays visible and says why, rather than the route 500ing --
        somebody still needs to read the effective configuration with a broken
        preferences file on disk."""

        settings_path(home).write_text("{truncated", encoding="utf-8")
        body = auth.get(CONFIG_URL).json()
        assert body["writable"] is False
        assert SETTING_FILENAME in body["detail"]
        assert len(body["rows"]) == len(EDITABLE_SETTINGS)

    def test_it_says_where_the_values_come_from(self, auth: TestClient) -> None:
        """The precedence is the feature's safety argument and it is stated in the
        response, not only in the source."""

        assert SETTING_FILENAME in auth.get(CONFIG_URL).json()["note"]

    def test_the_database_url_is_redacted(self, auth: TestClient, monkeypatch, home: Path) -> None:
        from iris import config
        from iris.config import Settings

        monkeypatch.setattr(
            config,
            "_settings",
            Settings(iris_home=home, database_url="postgresql://iris:hunter2@db/iris"),
        )
        body = auth.get(CONFIG_URL)
        assert "hunter2" not in body.text
        assert "***" in body.json()["database_url"]


def write_settings_file_of(client: TestClient, values: dict) -> None:
    """Save through the route, so the test exercises the path a person takes."""
    assert client.put(CONFIG_URL, json={"values": values}).status_code == 200



class TestSaving:
    def test_a_saved_value_lands_in_the_store(self, auth: TestClient, home: Path) -> None:
        resp = auth.put(CONFIG_URL, json={"values": {"llm_base_url": "http://127.0.0.1:11434/v1"}})
        assert resp.status_code == 200
        assert read_settings_file(home)["llm_base_url"] == "http://127.0.0.1:11434/v1"

    def test_the_answer_names_what_it_saved(self, auth: TestClient) -> None:
        body = auth.put(CONFIG_URL, json={"values": {"llm_model": "m", "llm_timeout_sec": 30}}).json()
        assert body["saved"] == ["llm_model", "llm_timeout_sec"]
        assert body["cleared"] == []

    def test_a_partial_save_leaves_the_other_fields_alone(
        self, auth: TestClient, home: Path
    ) -> None:
        """The panel holds ten rows in one form; saving the LLM section must not
        revert a log level somebody set an hour ago."""

        auth.put(CONFIG_URL, json={"values": {"log_level": "DEBUG"}})
        auth.put(CONFIG_URL, json={"values": {"llm_model": "m"}})
        assert read_settings_file(home) == {"log_level": "DEBUG", "llm_model": "m"}

    def test_saving_a_restart_only_field_says_so(self, auth: TestClient) -> None:
        body = auth.put(CONFIG_URL, json={"values": {"log_level": "DEBUG"}}).json()
        assert body["restart_required"] is True

    def test_saving_an_llm_field_also_says_a_restart_is_needed(self, auth: TestClient) -> None:
        """``get_settings()`` is a singleton with no reload, so the LLM client is built
        per diagnosis *from the same settings object the process started with*. An
        earlier version of this claimed the LLM fields would apply to the next
        diagnosis, on the reasoning that the client is built per call -- which is
        true of the client and false of where its settings come from.
        """

        body = auth.put(CONFIG_URL, json={"values": {"llm_model": "m"}}).json()
        assert body["restart_required"] is True

    def test_saving_the_value_already_in_effect_needs_no_restart(self, auth: TestClient) -> None:
        """The badge is the difference between "saved" and "in effect", so it has to
        go away when the two agree -- otherwise a person learns to ignore it and the
        rows that genuinely need a restart stop being read."""

        body = auth.put(CONFIG_URL, json={"values": {"api_max_upload_mb": 64}}).json()
        assert body["restart_required"] is False

    def test_saving_a_text_field_equal_to_the_running_one_needs_no_restart(
        self, auth: TestClient
    ) -> None:
        body = auth.put(
            CONFIG_URL, json={"values": {"download_mirror": "https://gh-proxy.com/"}}
        ).json()
        assert body["restart_required"] is False

    def test_a_saved_key_into_a_process_without_one_needs_a_restart(
        self, auth: TestClient
    ) -> None:
        """Saving a key into a process that started without one cannot possibly be in
        effect yet. The comparison runs server-side and only the answer leaves, so
        the row keeps saying "configured" while this says "restart"."""

        body = auth.put(CONFIG_URL, json={"values": {"llm_api_key": "sk-x"}}).json()
        assert body["restart_required"] is True
        assert "sk-x" not in json.dumps(body, ensure_ascii=False)

    @pytest.mark.parametrize("key", UNWRITABLE)
    def test_a_field_outside_the_table_is_a_422(self, auth: TestClient, key: str) -> None:
        """422 rather than 400: the body was well-formed JSON naming fields this
        service knows, and what was wrong was the value -- which is what 422 is for."""

        resp = auth.put(CONFIG_URL, json={"values": {key: "anything"}})
        assert resp.status_code == 422
        assert key in resp.json()["detail"]

    def test_a_value_out_of_range_is_a_422_with_a_readable_reason(self, auth: TestClient) -> None:
        resp = auth.put(CONFIG_URL, json={"values": {"llm_timeout_sec": 0}})
        assert resp.status_code == 422
        assert resp.json()["detail"]

    def test_a_refused_save_writes_nothing(self, auth: TestClient, home: Path) -> None:
        """One bad field in a form of ten must not leave nine of them on disk."""

        auth.put(CONFIG_URL, json={"values": {"llm_model": "m", "api_max_upload_mb": 0}})
        assert not settings_path(home).exists()

    def test_a_secret_never_comes_back_in_the_answer(self, auth: TestClient) -> None:
        resp = auth.put(CONFIG_URL, json={"values": {"llm_api_key": "sk-do-not-leak"}})
        assert "sk-do-not-leak" not in resp.text

    def test_the_store_is_created_under_a_home_that_does_not_exist_yet(
        self, auth: TestClient
    ) -> None:
        """A fresh install has no ``iris-home`` at all; a save is not allowed to be
        the thing that fails with a missing-directory error."""

        assert auth.put(CONFIG_URL, json={"values": {"llm_model": "m"}}).status_code == 200

    def test_an_empty_body_is_a_422_rather_than_a_silent_no_op(self, auth: TestClient) -> None:
        """``values`` is required, so a caller that sends ``{}`` gets told rather
        than getting a 200 that reports nothing saved."""

        assert auth.put(CONFIG_URL, json={}).status_code == 422


class TestResetting:
    def test_a_reset_drops_the_named_override(self, auth: TestClient, home: Path) -> None:
        auth.put(CONFIG_URL, json={"values": {"llm_model": "m", "llm_timeout_sec": 30}})
        resp = auth.delete(CONFIG_URL, params={"keys": ["llm_model"]})
        assert resp.status_code == 200
        assert resp.json()["cleared"] == ["llm_model"]
        assert read_settings_file(home) == {"llm_timeout_sec": 30}

    def test_several_keys_reset_at_once(self, auth: TestClient, home: Path) -> None:
        """Sent as repeated query parameters, not one comma-joined string: the route
        reads a list, and a joined string would arrive as a single unknown key."""

        auth.put(CONFIG_URL, json={"values": {"llm_model": "m", "llm_timeout_sec": 30}})
        auth.delete(CONFIG_URL, params={"keys": ["llm_model", "llm_timeout_sec"]})
        assert read_settings_file(home) == {}

    def test_a_reset_does_not_claim_to_have_saved_anything(self, auth: TestClient) -> None:
        body = auth.delete(CONFIG_URL, params={"keys": ["llm_model"]}).json()
        assert body["saved"] == []

    def test_a_reset_says_when_the_field_goes_back_to_needing_a_restart(
        self, auth: TestClient
    ) -> None:
        assert auth.delete(CONFIG_URL, params={"keys": ["log_level"]}).json()[
            "restart_required"
        ] is True

    def test_dropping_an_override_reports_a_restart_because_the_fallback_is_unknown(
        self, auth: TestClient, home: Path
    ) -> None:
        """The next start falls back to ``IRIS_*`` or the default, and the settings
        object in hand was itself resolved *through* the override being removed -- so
        "the running value happens to equal the fallback" is not knowable from here.
        Guessing would be a badge that lies in whichever direction the guess went.
        """

        write_settings_file(home, {"api_max_upload_mb": 64})
        body = auth.delete(CONFIG_URL, params={"keys": ["api_max_upload_mb"]}).json()
        assert body["restart_required"] is True

    def test_resetting_an_llm_field_reports_a_restart_too(self, auth: TestClient) -> None:
        """Same reason as the save: the running process still holds the endpoint it
        resolved at start."""

        body = auth.delete(CONFIG_URL, params={"keys": ["llm_model"]}).json()
        assert body["restart_required"] is True

    @pytest.mark.parametrize("key", UNWRITABLE)
    def test_resetting_a_field_outside_the_table_is_a_422(
        self, auth: TestClient, key: str
    ) -> None:
        resp = auth.delete(CONFIG_URL, params={"keys": [key]})
        assert resp.status_code == 422
        assert key in resp.json()["detail"]

    def test_resetting_a_nothing_is_accepted(self, auth: TestClient) -> None:
        """``keys`` is optional. A panel that clicked the button with nothing named
        is a bug in the panel, not a reason to fail the request."""

        assert auth.delete(CONFIG_URL).status_code == 200


class TestTheFrontendDeclaration:
    """``types.ts`` is hand-written with no generator behind it, so the only thing
    standing between a field renamed on one side and a blank input in the panel is a
    comparison of the two declarations."""

    def _frontend_interface(self, name: str) -> set[str]:
        source = (PROJECT / "web/src/lib/types.ts").read_text(encoding="utf-8")
        body = re.search(rf"export interface {name}\b[^{{]*\{{(.*?)\n\}}", source, re.DOTALL)
        assert body, f"{name} is not declared in types.ts"
        return set(re.findall(r"^\s*(\w+)\??:", body.group(1), re.MULTILINE))

    @pytest.mark.parametrize("name,model_name", [
        ("SettingRow", "SettingRow"),
        ("EffectiveConfig", "ConfigResponse"),
        ("ConfigUpdateResult", "ConfigUpdateResponse"),
    ])
    def test_the_declared_fields_are_the_same_on_both_sides(
        self, name: str, model_name: str
    ) -> None:
        frontend = self._frontend_interface(name)
        server = set(getattr(web_app, model_name).model_fields)

        assert frontend == server, (
            f"{name} has drifted from {model_name}: "
            f"only in types.ts {sorted(frontend - server)}; "
            f"only on the server {sorted(server - frontend)}"
        )

    def test_the_widget_vocabulary_is_the_same_on_both_sides(self) -> None:
        source = (PROJECT / "web/src/lib/types.ts").read_text(encoding="utf-8")
        declared = re.search(r"export type SettingKind =([^\n]+)", source)
        frontend = set(re.findall(r"'([^']+)'", declared.group(1)))

        assert frontend == {item.kind for item in EDITABLE_SETTINGS}

    def test_the_browser_cannot_see_a_credential_field(self) -> None:
        """``llm_api_key`` is writable, so a save posts it -- but a *response* that
        carried the field would put the key in every panel that renders a config."""

        source = (PROJECT / "web/src/lib/types.ts").read_text(encoding="utf-8")
        body = re.search(r"export interface SettingRow\b[^{]*\{(.*?)\n\}", source, re.DOTALL)
        assert "api_key" not in body.group(1)

    @pytest.mark.parametrize("path", [CONFIG_URL])
    def test_every_config_endpoint_is_reached_from_the_browser(self, path: str) -> None:
        """An endpoint nothing fetches is an endpoint nobody can see."""

        source = (PROJECT / "web/src/lib/api.ts").read_text(encoding="utf-8")
        assert path in source

    def test_the_save_and_reset_hooks_exist(self) -> None:
        queries = (PROJECT / "web/src/hooks/queries.ts").read_text(encoding="utf-8")
        for hook in ("useSaveConfig", "useResetConfig"):
            assert f"export function {hook}(" in queries, f"{hook} does not exist"

    def test_the_panel_actually_renders_the_editable_rows(self) -> None:
        """A settings table the panel does not render is a table nobody writes from,
        and the panel would still be saying "read-only"."""

        source = (PROJECT / "web/src/components/SettingsSheet.tsx").read_text(encoding="utf-8")
        assert "<SettingField" in source
        assert "useSaveConfig" in source
        assert "useResetConfig" in source

    def test_the_panel_does_not_still_call_the_configuration_read_only(self) -> None:
        """The sentence it used to carry was true when the only configuration was
        ``.env``. It is the reason somebody might still believe these fields cannot
        be changed, so it has to go rather than be left beside an editable form."""

        source = (PROJECT / "web/src/components/SettingsSheet.tsx").read_text(encoding="utf-8")
        assert "read_only" not in source

    def test_the_key_is_not_hardcoded_into_the_frontend(self) -> None:
        """The writable set is one table in the server. A second copy in the browser
        is a list that drifts, and the drift shows up as a field the server refuses."""

        source = (PROJECT / "web/src/lib/types.ts").read_text(encoding="utf-8")
        assert "llm_api_key" not in source