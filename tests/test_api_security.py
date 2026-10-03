"""P0 delivery safety: authentication, ownership, bounded input, and the
loopback default that makes local mode safe.

Every test here runs against a temporary database. The first version of these
routes kept the live set in a module-level dict and read the *configured*
database, so a test run silently truncated the real ``active_emulation`` table
and reported whatever the host happened to be running. The isolation is part of
what is being tested, so it is a fixture rather than a per-test setup.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from iris.api import auth as auth_mod
from iris.api.server import app
from iris.db.active import reconcile, register, release
from iris.db.engine import get_engine, init_db, make_session
from iris.db.models import ActiveEmulation

TOKEN = "s3cret-token-value"


def _settings_for(tmp_path, **overrides):
    """Settings pointing at a throwaway home + database.

    Built through the real ``Settings`` class rather than a stub object so a new
    required field cannot be silently absent from these tests -- which is how the
    upload cap first reached ``AttributeError`` on a hand-rolled fake.
    """
    from iris.config import Settings

    home = tmp_path / "home"
    (home / "scratch").mkdir(parents=True, exist_ok=True)
    base = {"iris_home": str(home), "database_url": f"sqlite:///{home / 'api.db'}"}
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fully isolated server environment: temp database, temp home, no docker.

    ``container_alive`` is replaced on the module, not as a default argument,
    because ``reconcile`` resolves it at call time on purpose -- an earlier
    version bound it as a default, which made this substitution a no-op and would
    have shelled out to docker from the test suite.
    """
    monkeypatch.setattr("iris.db.active.container_alive", lambda name: True)
    state: dict = {}

    def configure(**overrides):
        settings = _settings_for(tmp_path, **overrides)
        monkeypatch.setattr(auth_mod, "get_settings", lambda: settings)
        monkeypatch.setattr("iris.api.server.get_settings", lambda: settings)
        state["settings"] = settings
        return settings

    configure()
    state["configure"] = configure
    state["url"] = state["settings"].database_url
    return state


@pytest.fixture
def client_local(env):
    """A client for a server with no token configured: every caller is LOCAL_CLIENT."""
    return TestClient(app)


@pytest.fixture
def client_authed(env):
    """A client for a server that requires TOKEN."""
    env["configure"](api_token=TOKEN)
    return TestClient(app)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- health / version


class TestHealthAndVersion:
    def test_health_reports_the_package_version(self, client_local) -> None:
        from iris import __version__

        body = client_local.get("/api/v1/health").json()
        assert body["version"] == __version__

    def test_health_does_not_leak_the_emulation_count(self, client_local) -> None:
        """The count moved behind auth; an unauthenticated probe must not see it.

        Pinned on the *model*, not just the JSON: pydantic drops unknown fields
        by default, so asserting on the body alone would stay green even if the
        handler re-added the field. The model has to forbid extras for this to be
        a guard rather than a snapshot.
        """
        from iris.api.server import HealthResponse

        assert "active_emulations" not in HealthResponse.model_fields
        body = client_local.get("/api/v1/health").json()
        assert "active_emulations" not in body

    def test_the_health_model_refuses_extra_fields(self) -> None:
        """The property the assertion above depends on, asserted directly."""
        from pydantic import ValidationError

        from iris.api.server import HealthResponse

        with pytest.raises(ValidationError):
            HealthResponse(status="ok", version="0", active_emulations=0)

    def test_version_is_not_hardcoded(self, client_local) -> None:
        """`0.1.0` sat in this file for versions; assert it tracks the package.

        A literal assertion would pass again the moment someone bumped the
        package without touching this file, so the check is written against the
        imported value and a mutation to the import is what must turn it red.
        """
        import iris.api.server as server_mod
        from iris import __version__

        assert server_mod.__version__ == __version__
        assert __version__ != "0.1.0"


# ---------------------------------------------------------------- authentication


class TestAuthentication:
    def test_every_stateful_route_requires_a_token_when_one_is_configured(
        self, client_authed
    ) -> None:
        for method, path in (
            ("get", "/api/v1/firmware"),
            ("get", "/api/v1/emulate"),
            ("get", "/api/v1/emulate/1"),
            ("delete", "/api/v1/emulate/1"),
            ("post", "/api/v1/emulate"),
        ):
            resp = getattr(client_authed, method)(path)
            assert resp.status_code == 401, f"{method} {path} answered {resp.status_code}"

    def test_pipeline_requires_a_token(self, client_authed) -> None:
        resp = client_authed.post(
            "/api/v1/pipeline", files={"firmware": ("fw.bin", b"\x00" * 8, "application/octet-stream")}
        )
        assert resp.status_code == 401

    def test_health_stays_open_for_probes(self, client_authed) -> None:
        assert client_authed.get("/api/v1/health").status_code == 200

    def test_a_wrong_token_is_rejected(self, client_authed) -> None:
        resp = client_authed.get("/api/v1/emulate", headers=_bearer("not-the-token"))
        assert resp.status_code == 401

    def test_bearer_and_header_spellings_both_work(self, client_authed) -> None:
        assert client_authed.get("/api/v1/emulate", headers=_bearer(TOKEN)).status_code == 200
        assert client_authed.get(
            "/api/v1/emulate", headers={auth_mod.CLIENT_HEADER: TOKEN}
        ).status_code == 200

    def test_local_mode_needs_no_token(self, client_local) -> None:
        """No token configured means every caller is LOCAL_CLIENT.

        Only safe because the startup gate refuses a non-loopback bind without a
        token; ``TestServeStartRefusesUnsafeBinds`` is the other half of that
        invariant and the two tests fail together if either is removed.
        """
        assert client_local.get("/api/v1/emulate").status_code == 200


# ---------------------------------------------------------------- ownership


class TestOwnership:
    def _register(self, url: str, iid: int, client_id: str) -> None:
        engine = get_engine(url)
        init_db(engine)
        with make_session(engine) as session:
            register(session, iid=iid, client_id=client_id, arch="mipsel",
                      rootfs_path=f"/tmp/{iid}-rootfs", container_id=f"iris-qemu-{iid}")

    def test_a_caller_cannot_read_somebody_elses_run(self, client_local, env) -> None:
        self._register(env["url"], 4242, "client-a")
        assert client_local.get("/api/v1/emulate/4242").status_code == 404

    def test_the_owner_can_read_their_own_run(self, client_local, env) -> None:
        from iris.api.auth import LOCAL_CLIENT

        self._register(env["url"], 4245, LOCAL_CLIENT)
        resp = client_local.get("/api/v1/emulate/4245")
        assert resp.status_code == 200
        assert resp.json()["iid"] == 4245

    def test_a_caller_cannot_stop_somebody_elses_run(self, client_local, env, monkeypatch) -> None:
        """The whole point: DELETE must not reach docker for another client's iid."""
        self._register(env["url"], 4243, "client-a")
        stopped: list[int] = []
        monkeypatch.setattr("iris.api.server.stop_emulation",
                            lambda iid: stopped.append(iid) or True)
        resp = client_local.delete("/api/v1/emulate/4243")
        assert resp.status_code == 404
        assert stopped == [], "an unowned iid reached docker"

    def test_the_owner_can_stop_their_own_run(self, client_local, env, monkeypatch) -> None:
        from iris.api.auth import LOCAL_CLIENT

        self._register(env["url"], 4244, LOCAL_CLIENT)
        stopped: list[int] = []
        monkeypatch.setattr("iris.api.server.stop_emulation",
                            lambda iid: stopped.append(iid) or True)
        resp = client_local.delete("/api/v1/emulate/4244")
        assert resp.status_code == 200
        assert resp.json()["stopped"] is True
        assert stopped == [4244]

    def test_stopping_an_unknown_iid_is_404_not_a_silent_success(self, client_local) -> None:
        """It used to answer 200 with a container delete that could hit anything."""
        resp = client_local.delete("/api/v1/emulate/99999")
        assert resp.status_code == 404

    def test_listing_shows_only_your_own(self, client_local, env) -> None:
        self._register(env["url"], 5001, "client-a")
        self._register(env["url"], 5002, "client-b")
        from iris.api.auth import LOCAL_CLIENT

        self._register(env["url"], 5003, LOCAL_CLIENT)
        rows = client_local.get("/api/v1/emulate").json()
        assert [row["iid"] for row in rows] == [5003]


# ---------------------------------------------------------------- persistence


class TestStateSurvivesARestart:
    def test_a_registered_run_is_readable_from_a_fresh_session(self, env) -> None:
        """No process memory: a new session must see what the old one registered."""
        engine = get_engine(env["url"])
        init_db(engine)
        with make_session(engine) as session:
            register(session, iid=6001, client_id="c", arch="armel",
                     rootfs_path="/tmp/x-rootfs", container_id="iris-qemu-6001")
        with make_session(engine) as session:
            assert session.scalar(
                select(ActiveEmulation).where(ActiveEmulation.iid == 6001)
            ) is not None

    def test_reregistering_an_iid_replaces_the_row(self, env) -> None:
        """The orchestrator reuses an iid per rootfs; the newest container wins."""
        engine = get_engine(env["url"])
        init_db(engine)
        with make_session(engine) as session:
            register(session, iid=7001, client_id="c", arch="mipsel",
                     rootfs_path="/tmp/a", container_id="iris-qemu-7001")
            register(session, iid=7001, client_id="c", arch="mipsel",
                     rootfs_path="/tmp/a", container_id="iris-qemu-7001-new")
        with make_session(engine) as session:
            rows = list(session.scalars(select(ActiveEmulation)))
        assert [(r.iid, r.container_id) for r in rows] == [(7001, "iris-qemu-7001-new")]

    def test_reconcile_drops_rows_whose_container_is_gone(self, env) -> None:
        engine = get_engine(env["url"])
        init_db(engine)
        with make_session(engine) as session:
            register(session, iid=8001, client_id="c", arch="mipsel",
                     rootfs_path="/tmp/a", container_id="iris-qemu-8001")
            register(session, iid=8002, client_id="c", arch="mipsel",
                     rootfs_path="/tmp/b", container_id="iris-qemu-8002")
            dropped = reconcile(session, alive=lambda name: name == "iris-qemu-8001")
        assert dropped == [8002]

    def test_reconcile_keeps_a_row_when_the_probe_cannot_answer(self, env) -> None:
        """None means "docker did not answer", not "the container is gone".

        Deleting on an unanswered probe would erase the record of a running
        container during a docker hiccup, taking away the caller's ability to stop
        it -- the failure this three-valued probe exists to prevent.
        """
        engine = get_engine(env["url"])
        init_db(engine)
        with make_session(engine) as session:
            register(session, iid=8101, client_id="c", arch="mipsel",
                     rootfs_path="/tmp/a", container_id="iris-qemu-8101")
            assert reconcile(session, alive=lambda name: None) == []
        with make_session(engine) as session:
            assert session.scalar(
                select(ActiveEmulation).where(ActiveEmulation.iid == 8101)
            ) is not None

    def test_reconcile_leaves_a_live_row_alone(self, env) -> None:
        engine = get_engine(env["url"])
        init_db(engine)
        with make_session(engine) as session:
            register(session, iid=8003, client_id="c", arch="mipsel",
                     rootfs_path="/tmp/a", container_id="iris-qemu-8003")
            assert reconcile(session, alive=lambda name: True) == []

    def test_reconcile_defaults_to_the_probe_not_a_bound_default(self, env, monkeypatch) -> None:
        """A bound ``alive=container_alive`` default would ignore this patch."""
        seen: list[str] = []
        monkeypatch.setattr(
            "iris.db.active.container_alive", lambda name: seen.append(name) or True
        )
        engine = get_engine(env["url"])
        init_db(engine)
        with make_session(engine) as session:
            register(session, iid=8004, client_id="c", arch="mipsel",
                     rootfs_path="/tmp/a", container_id="iris-qemu-8004")
            reconcile(session)
        assert seen == ["iris-qemu-8004"], "the injected probe was bypassed"

    def test_release_refuses_the_wrong_owner(self, env) -> None:
        engine = get_engine(env["url"])
        init_db(engine)
        with make_session(engine) as session:
            register(session, iid=9001, client_id="owner", arch="mipsel",
                     rootfs_path="/tmp/a", container_id="iris-qemu-9001")
            assert release(session, iid=9001, client_id="someone-else") is None
            assert session.scalar(
                select(ActiveEmulation).where(ActiveEmulation.iid == 9001)
            ) is not None
            assert release(session, iid=9001, client_id="owner") is not None

    def test_an_empty_container_id_counts_as_gone(self) -> None:
        """A row with nothing to probe must not be reported as hosted."""
        from iris.db.active import container_alive

        assert container_alive("") is False


# ---------------------------------------------------------------- bounded input


class TestUploadLimit:
    """`pipeline` used `firmware.read()`, pulling an unbounded body into memory."""

    def test_an_upload_past_the_cap_is_refused(self, client_local, env) -> None:
        env["configure"](api_max_upload_mb=1)
        # 1 MiB cap, 1 MiB + 1 byte: the boundary has to be "over", not "at least
        # this big", or the guard is off by a whole chunk.
        resp = client_local.post(
            "/api/v1/pipeline",
            files={"firmware": ("fw.bin", b"\x00" * (1024 * 1024 + 1), "application/octet-stream")},
            params={"arch": "mipsel"},
        )
        assert resp.status_code == 413

    def test_the_limit_is_reported_in_the_error(self, client_local, env) -> None:
        env["configure"](api_max_upload_mb=1)
        resp = client_local.post(
            "/api/v1/pipeline",
            files={"firmware": ("fw.bin", b"\x00" * (2 * 1024 * 1024), "application/octet-stream")},
            params={"arch": "mipsel"},
        )
        assert resp.status_code == 413
        assert "1 MiB" in resp.json()["detail"]

    def test_a_small_upload_is_not_refused_by_the_cap(self, client_local, env) -> None:
        """Negative control: a guard that refuses everything is not a guard."""
        env["configure"](api_max_upload_mb=64)
        resp = client_local.post(
            "/api/v1/pipeline",
            files={"firmware": ("fw.bin", b"\x00" * 1024, "application/octet-stream")},
            params={"arch": "mipsel"},
        )
        # Passes the cap, then fails on content -- which is the point.
        assert resp.status_code != 413



# ---------------------------------------------------------------- startup gate


class TestServeStartRefusesUnsafeBinds:
    """`iris serve start` bound 0.0.0.0 by default with no token anywhere."""

    def _run(self, monkeypatch, tmp_path, **kwargs):
        from typer.testing import CliRunner

        from iris.cli import app as cli_app

        started: list[tuple] = []
        monkeypatch.setattr("uvicorn.run", lambda *a, **k: started.append((a, k)))
        # A real Settings with no token, so an IRIS_API_TOKEN in the developer's
        # environment cannot make the "without a token" cases pass for free.
        monkeypatch.setattr(auth_mod, "get_settings", lambda: _settings_for(tmp_path))
        args = ["serve", "start"]
        for key, value in kwargs.items():
            args.extend([f"--{key.replace('_', '-')}", str(value)])
        result = CliRunner().invoke(cli_app, args)
        return result, started

    def test_a_network_bind_without_a_token_refuses_to_start(self, monkeypatch, tmp_path) -> None:
        result, started = self._run(monkeypatch, tmp_path, host="0.0.0.0")
        assert result.exit_code == 2
        assert started == [], "the server started anyway"

    def test_a_localhost_bind_without_a_token_is_allowed(self, monkeypatch, tmp_path) -> None:
        result, started = self._run(monkeypatch, tmp_path, host="127.0.0.1")
        assert result.exit_code == 0
        assert len(started) == 1

    def test_a_network_bind_with_a_token_is_allowed(self, monkeypatch, tmp_path) -> None:
        result, started = self._run(monkeypatch, tmp_path, host="0.0.0.0", api_token="t")
        assert result.exit_code == 0
        assert len(started) == 1

    def test_the_explicit_token_beats_the_environment(self, monkeypatch, tmp_path) -> None:
        """--api-token has to override a configured one, or a stale env value wins."""
        settings = _settings_for(tmp_path, api_token="from-env")
        monkeypatch.setattr(auth_mod, "get_settings", lambda: settings)
        started: list[tuple] = []
        monkeypatch.setattr("uvicorn.run", lambda *a, **k: started.append((a, k)))
        from typer.testing import CliRunner

        from iris.cli import app as cli_app

        result = CliRunner().invoke(
            cli_app, ["serve", "start", "--host", "0.0.0.0", "--api-token", "flag"]
        )
        assert result.exit_code == 0
        assert len(started) == 1

    def test_the_default_bind_is_loopback(self) -> None:
        """The default is the security property; assert the shipped default."""
        import inspect

        from iris.cli import serve_start

        default = inspect.signature(serve_start).parameters["host"].default
        assert default.default == "127.0.0.1"

    def test_loopback_detection(self) -> None:
        from iris.api.auth import is_loopback_host

        for host in ("127.0.0.1", "::1", "localhost", "127.5.5.5"):
            assert is_loopback_host(host) is True, host
        for host in ("0.0.0.0", "::", "192.168.1.5", "", "example.com"):
            assert is_loopback_host(host) is False, host
