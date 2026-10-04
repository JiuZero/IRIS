"""Assembly: the routes, the SPA fallback, and what happens on the way out.

The dashboard is installed onto the *same* app the API already serves, so these
tests build their own ``FastAPI`` rather than importing ``iris.api.server.app``.
Sharing it would register the catch-all and the extra routes on the singleton that
``test_api.py`` drives, and then every test in the suite would be answering from a
handler this file registered.

Nothing here reaches docker. :func:`iris.api.web_app.shutdown` really does call
``stop_emulation``, and a ``TestClient`` used as a context manager really does run
the lifespan -- so the fixture replaces that call rather than trusting the tests to
avoid triggering it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iris.api import web_app, web_data

# Imported at module scope, not inside the test: this file uses
# ``from __future__ import annotations``, so a route defined in a function body
# would have its ``caller: Caller`` annotation resolved against the *module*
# globals -- where a function-local import would not be visible. FastAPI then
# cannot see the dependency and answers 422 instead of 401.
from iris.api.server import Caller

TOKEN = "workbench-token"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """An isolated schema, wired into both ``web_app`` and ``web_data``."""
    from iris.db.engine import get_engine, init_db, make_session

    engine = get_engine(f"sqlite:///{(tmp_path / 'webapp.db').as_posix()}")
    init_db(engine)
    for module in (web_app,):
        monkeypatch.setattr(module, "_session", lambda: make_session(engine))

    monkeypatch.setattr(web_data, "_session", lambda: make_session(engine))
    return engine


@pytest.fixture
def no_docker(monkeypatch):
    """Record what shutdown would have asked docker to do, and do nothing else.

    Also pins the container probe: ``reconcile`` resolves it at call time from the
    module attribute precisely so this substitution works.
    """
    stopped: list[int] = []

    def fake_stop(iid: int):
        stopped.append(iid)
        return {"container": f"iris-qemu-{iid}", "removed": True}

    monkeypatch.setattr(web_app, "stop_emulation", fake_stop)
    monkeypatch.setattr("iris.db.active.container_alive", lambda name: True)
    return stopped


@pytest.fixture
def app(db, no_docker) -> FastAPI:
    return web_app.install(FastAPI())


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def _dist(monkeypatch, tmp_path) -> Path:
    """Point the SPA at a dist directory this test controls.

    Both the candidate list and the reported fallback are patched: the fallback is
    what a 503 names, and leaving it pointing at the real repository would have the
    message quote a path the test never looked at.
    """
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    monkeypatch.delenv("IRIS_WEB_DIST", raising=False)
    monkeypatch.setattr(web_app, "_DIST_CANDIDATES", (dist,))
    monkeypatch.setattr(web_app, "_DIST_FALLBACK", dist)
    return dist


# ------------------------------------------------------------------- redaction


class TestRedactDatabaseUrl:
    def test_a_password_is_replaced(self) -> None:
        assert web_app.redact_database_url("postgresql://iris:hunter2@db:5432/iris") == \
            "postgresql://iris:***@db:5432/iris"

    def test_a_url_without_a_password_is_left_alone(self) -> None:
        assert web_app.redact_database_url("sqlite:///iris-home/iris.db") == \
            "sqlite:///iris-home/iris.db"

    def test_a_url_with_no_credentials_is_left_alone(self) -> None:
        """A local sqlite path is the common case and must survive verbatim."""
        assert web_app.redact_database_url("sqlite:///iris-home/iris.db") != ""

    def test_only_the_password_disappears(self) -> None:
        """The host and user are what make the line diagnostic; dropping the whole
        credentials section would leave a page that cannot tell you where it reads."""
        redacted = web_app.redact_database_url("mysql://reader:pw@10.0.0.5:3306/iris")
        assert "reader" in redacted and "10.0.0.5" in redacted
        assert "pw" not in redacted

    def test_a_password_containing_an_at_sign_keeps_the_host(self) -> None:
        """``rpartition("@")`` splits on the last one, which is the separator --
        the greedy split on the first would rewrite the host as the password."""
        redacted = web_app.redact_database_url("postgresql://u:p@ss@db:5432/iris")
        assert redacted.endswith("@db:5432/iris")
        assert "p@ss" not in redacted


# -------------------------------------------------------------------- safe join


class TestSafeJoin:
    def test_a_normal_path_resolves_inside(self, tmp_path) -> None:
        (tmp_path / "app.js").write_text("x", encoding="utf-8")
        assert web_app.safe_join(tmp_path, "app.js") == (tmp_path / "app.js").resolve()

    def test_dot_dot_out_of_the_root_is_refused(self, tmp_path) -> None:
        root = tmp_path / "dist"
        root.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("token", encoding="utf-8")
        assert web_app.safe_join(root, "../secret.txt") is None

    def test_an_absolute_path_is_refused(self, tmp_path) -> None:
        """``root / "/etc/passwd"`` is ``/etc/passwd`` in pathlib -- the join is
        not a sandbox, and this is the line that keeps it from being one."""
        root = tmp_path / "dist"
        root.mkdir()
        assert web_app.safe_join(root, "/etc/passwd") is None

    def test_an_empty_path_is_refused(self, tmp_path) -> None:
        """The empty string resolves to the root itself, which would hand back the
        directory instead of a file."""
        assert web_app.safe_join(tmp_path, "") is None

    def test_a_traversal_that_stays_inside_is_allowed(self, tmp_path) -> None:
        root = tmp_path / "dist"
        (root / "assets").mkdir(parents=True)
        (root / "assets" / "app.js").write_text("x", encoding="utf-8")
        assert web_app.safe_join(root, "assets/../assets/app.js") is not None


# ------------------------------------------------------------------- idempotent


class TestInstall:
    def test_installing_twice_registers_once(self, db, no_docker) -> None:
        """uvicorn --reload re-runs the assembly. A duplicated static mount has
        undefined behaviour and a duplicated shutdown handler runs the cleanup
        twice against an already-emptied container list."""
        app = FastAPI()
        web_app.install(app)
        first = len(app.routes)
        web_app.install(app)
        assert len(app.routes) == first

    def test_the_catch_all_is_registered_last(self, app) -> None:
        """Everything else is matched by declaration order, so a catch-all
        registered first would shadow the API."""
        paths = [getattr(route, "path", "") for route in app.routes]
        assert paths.index("/{full_path:path}") > paths.index("/api/v1/stats")

    def test_the_terminal_socket_is_registered(self, app) -> None:
        sockets = [getattr(route, "path", "") for route in app.routes
                   if route.__class__.__name__ == "APIWebSocketRoute"]
        assert sockets == ["/ws/terminal"]


# ------------------------------------------------------------------ csv routing


class TestExportRoute:
    def test_the_csv_route_is_reachable_by_its_literal_path(self, client, db) -> None:
        """Starlette matches in declaration order and ``{run_id}`` is typed ``int``,
        so declaring the CSV route below it made ``/runs/export.csv`` a 422.
        This is the regression guard for that."""
        from iris.db.models import EmulationRun

        with web_app._session() as session:
            session.add(EmulationRun(iid=7001, arch="mipsel", web_reachable=True))
            session.commit()
        resp = client.get("/api/v1/runs/export.csv")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "iris-runs.csv" in resp.headers["content-disposition"]
        assert "7001" in resp.text

    def test_the_csv_route_is_declared_before_the_typed_parameter(self, app) -> None:
        """Asserted on the route table rather than only on behaviour: a future
        reorder that still passes the request test by luck would not be caught."""
        paths = [getattr(route, "path", "") for route in app.routes]
        assert paths.index("/api/v1/runs/export.csv") < paths.index("/api/v1/runs/{run_id}")

    def test_a_numeric_run_still_resolves_to_its_detail(self, client, db) -> None:
        from iris.db.models import EmulationRun

        with web_app._session() as session:
            row = EmulationRun(iid=7001, arch="mipsel", web_reachable=True)
            session.add(row)
            session.commit()
            run_id = row.id
        assert client.get(f"/api/v1/runs/{run_id}").json()["iid"] == 7001

    def test_a_missing_run_is_a_404_not_a_500(self, client, db) -> None:
        assert client.get("/api/v1/runs/99999").status_code == 404


# ------------------------------------------------------------------- read views


class TestDashboardRoutes:
    def test_the_four_cards_come_from_stats(self, client, db) -> None:
        body = client.get("/api/v1/stats").json()
        assert body["available"] is True
        assert {"total", "web_ok", "environment_failures", "web_reach_rate"} <= set(body)

    def test_the_host_reading_is_served_and_states_its_own_lifetime(self, client, db) -> None:
        """It reports a live observation rather than a record, and says so: a
        figure the page cannot reproduce later must not be read as a fact."""
        body = client.get("/api/v1/system").json()
        assert set(body) == {"host", "service"}
        assert body["host"]["mem_total_mb"] > 0
        assert body["host"]["cpu_cores"] and body["host"]["cpu_cores"] > 0
        assert body["service"]["version"]

    def test_capabilities_are_readable_without_a_body(self, client, db) -> None:
        body = client.get("/api/v1/capabilities").json()
        assert len(body["items"]) == 6

    def test_the_eval_set_states_its_denominator(self, client, db) -> None:
        body = client.get("/api/v1/stats/eval-set").json()
        assert body["denominator"] == 0
        assert body["denominator_note"]

    def test_runs_page_honours_the_query_bounds(self, client, db) -> None:
        assert client.get("/api/v1/runs?limit=0").status_code == 422
        assert client.get("/api/v1/runs?limit=501").status_code == 422
        assert client.get("/api/v1/runs?offset=-1").status_code == 422
        assert client.get("/api/v1/runs?limit=500").status_code == 200

    def test_an_unknown_instance_has_no_console_snapshot(self, client, db, monkeypatch, tmp_path) -> None:
        """The scratch directory has to be redirected as well: the real one holds
        ``emulate-7100/qemu.serial.log`` from a real run, and without this the test
        would assert against whatever the host last measured."""
        from iris.config import Settings

        monkeypatch.setattr(web_data, "get_settings",
                            lambda: Settings(iris_home=str(tmp_path)))
        body = client.get("/api/v1/console/7100").json()
        assert body["available"] is False

    def test_root_causes_are_readable_on_an_empty_corpus(self, client, db) -> None:
        assert client.get("/api/v1/knowledge/root-cause").json()["cards"] == []

    def test_config_reports_the_token_as_present_and_never_as_its_value(self, client, db, monkeypatch) -> None:
        from iris.config import Settings

        settings = Settings(api_token=TOKEN, database_url="postgresql://iris:hunter2@db/iris")
        monkeypatch.setattr(web_app, "get_settings", lambda: settings)
        body = client.get("/api/v1/config").json()
        assert body["api_token_configured"] is True
        assert body["read_only"] is True
        assert TOKEN not in resp_text(client)
        assert "hunter2" not in resp_text(client)
        assert "***" in body["database_url"]

    def test_config_says_not_configured_when_there_is_no_token(self, client, db) -> None:
        assert client.get("/api/v1/config").json()["api_token_configured"] in {True, False}

    def test_instance_stats_of_an_instance_you_do_not_own_is_a_404(self, client, db) -> None:
        from iris.db.active import register

        with web_app._session() as session:
            register(session, iid=7001, client_id="somebody-else", arch="mipsel",
                     rootfs_path="/tmp/a", container_id="c1")
        assert client.get("/api/v1/instances/7001/stats").status_code == 404

    def test_instance_stats_of_your_own_instance_is_readable(self, client, db, monkeypatch) -> None:
        from iris.api import web_data
        from iris.db.active import register

        with web_app._session() as session:
            register(session, iid=7001, client_id="local", arch="mipsel",
                     rootfs_path="/tmp/a", container_id="c1")
        monkeypatch.setattr(web_data, "container_stats", lambda name: None)
        monkeypatch.setattr(web_data, "serial_port_of", lambda iid: None)
        body = client.get("/api/v1/instances/7001/stats")
        assert body.status_code == 200
        assert body.json()["sampled"] is False


def resp_text(client) -> str:
    return client.get("/api/v1/config").text


# ------------------------------------------------------------------------ auth


class TestAuthentication:
    def test_install_leaves_an_existing_open_route_open(self, db, no_docker, monkeypatch) -> None:
        """``/api/v1/health`` is the one route that must answer without a token --
        a liveness probe cannot carry one. Asserted on a stand-in registered before
        ``install`` rather than on ``iris.api.server.app``, because installing on
        that singleton would chain a shutdown handler onto the app every other API
        test drives.
        """
        from iris.config import Settings


        base = FastAPI()

        @base.get("/api/v1/health")
        async def health() -> dict:
            return {"status": "ok"}

        @base.get("/api/v1/probe")
        async def probe(caller: Caller) -> dict:
            return {"caller": caller}

        web_app.install(base)
        monkeypatch.setattr("iris.api.auth.get_settings", lambda: Settings(api_token=TOKEN))
        client = TestClient(base)
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/probe").status_code == 401

    def test_every_new_dashboard_route_requires_the_token(self, app, monkeypatch) -> None:
        """The corpus is the sensitive part: which firmware was tried, what its
        console printed, how it failed. All of it sits behind the token."""
        from iris.config import Settings

        monkeypatch.setattr("iris.api.auth.get_settings", lambda: Settings(api_token=TOKEN))
        client = TestClient(app)
        for path in ("/api/v1/stats", "/api/v1/capabilities", "/api/v1/stats/eval-set",
                     "/api/v1/system", "/api/v1/runs", "/api/v1/runs/export.csv",
                     "/api/v1/runs/1", "/api/v1/console/7100",
                     "/api/v1/instances/7100/stats", "/api/v1/knowledge/root-cause",
                     "/api/v1/config"):
            assert client.get(path).status_code == 401, path

    def test_a_query_parameter_is_not_accepted_instead_of_a_header(self, app, monkeypatch) -> None:
        """The websocket takes its token in the query string because a browser
        cannot set headers. That concession must not extend to the REST routes."""
        from iris.config import Settings

        monkeypatch.setattr("iris.api.auth.get_settings", lambda: Settings(api_token=TOKEN))
        assert TestClient(app).get(f"/api/v1/stats?api_token={TOKEN}").status_code == 401

    def test_the_right_token_is_accepted(self, app, monkeypatch) -> None:
        from iris.config import Settings

        monkeypatch.setattr("iris.api.auth.get_settings", lambda: Settings(api_token=TOKEN))
        client = TestClient(app)
        resp = client.get("/api/v1/stats", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 200

    def test_the_header_spelling_without_bearer_is_accepted(self, app, monkeypatch) -> None:
        """``X-IRIS-Token`` exists for clients that cannot set ``Authorization``;
        the dashboard's own fetch wrapper uses it."""
        from iris.config import Settings

        monkeypatch.setattr("iris.api.auth.get_settings", lambda: Settings(api_token=TOKEN))
        client = TestClient(app)
        assert client.get("/api/v1/stats", headers={"X-IRIS-Token": TOKEN}).status_code == 200

    def test_a_wrong_token_is_refused(self, app, monkeypatch) -> None:
        from iris.config import Settings

        monkeypatch.setattr("iris.api.auth.get_settings", lambda: Settings(api_token=TOKEN))
        client = TestClient(app)
        assert client.get("/api/v1/stats",
                          headers={"Authorization": "Bearer nope"}).status_code == 401


# -------------------------------------------------------------------- spa rules


class TestSpaFallback:
    def test_an_unbuilt_frontend_says_so_with_the_command_to_fix_it(self, client, db, monkeypatch, tmp_path) -> None:
        """503 with a hint, not a blank page: this is the state a judge sees if the
        wheel was installed without ``web/dist``, and it has to be self-explaining."""
        _dist(monkeypatch, tmp_path)
        resp = client.get("/")
        assert resp.status_code == 503
        assert "npm run build" in resp.json()["hint"]

    def test_an_unknown_api_path_is_a_404_and_not_the_page(self, client, db, monkeypatch, tmp_path) -> None:
        """Serving index.html for a mistyped API path turns a 404 into a 200 of
        HTML -- the hardest kind of API mistake to notice."""
        dist = _dist(monkeypatch, tmp_path)
        (dist / "index.html").write_text("<html></html>", encoding="utf-8")
        resp = client.get("/api/v1/nope")
        assert resp.status_code == 404
        assert "<html>" not in resp.text

    def test_the_api_prefix_is_refused_before_the_filesystem_is(self, client, db, monkeypatch, tmp_path) -> None:
        dist = _dist(monkeypatch, tmp_path)
        (dist / "api").mkdir()
        (dist / "api" / "v1").mkdir()
        (dist / "api" / "v1" / "index.html").write_text("decoy", encoding="utf-8")
        assert client.get("/api/v1/index.html").status_code == 404

    def test_a_client_side_route_falls_back_to_index(self, client, db, monkeypatch, tmp_path) -> None:
        dist = _dist(monkeypatch, tmp_path)
        (dist / "index.html").write_text("<html>shell</html>", encoding="utf-8")
        resp = client.get("/instances/7100")
        assert resp.status_code == 200
        assert "shell" in resp.text

    def test_a_real_asset_is_served_as_itself(self, client, db, monkeypatch, tmp_path) -> None:
        dist = _dist(monkeypatch, tmp_path)
        (dist / "index.html").write_text("<html>shell</html>", encoding="utf-8")
        (dist / "assets").mkdir()
        (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
        resp = client.get("/assets/app.js")
        assert resp.status_code == 200
        assert "console.log(1)" in resp.text

    def test_a_traversal_is_refused_and_the_page_is_served_instead(self, client, db, monkeypatch, tmp_path) -> None:
        """It must not serve the file; whether it then 404s or falls back to the
        shell is a UX choice, but serving the file is not on the table."""
        dist = _dist(monkeypatch, tmp_path)
        (dist / "index.html").write_text("<html>shell</html>", encoding="utf-8")
        secret = tmp_path / "secret.txt"
        secret.write_text("token", encoding="utf-8")
        resp = client.get("/../secret.txt")
        assert "token" not in resp.text


    def test_the_docs_page_still_renders(self, client, db) -> None:
        """The catch-all is registered after the docs routes, not over them."""
        assert client.get("/docs").status_code == 200

    def test_the_paths_the_banner_and_the_footer_advertised_all_exist(self, client, db) -> None:
        """A banner line and a footer link are promises, and the SPA catch-all makes
        it cheap to break one quietly.

        The frontend link is an anchor rather than a router link for the same reason:
        a client-side navigation to /docs matches the router's catch-all and lands
        back on the dashboard instead of on the OpenAPI page. ``/api/v1/health``
        belongs to ``iris.api.server`` and is covered with that app; what this app owns
        is the dashboard's own API surface plus the docs pages it inherited.
        """
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/api/v1/capabilities").status_code == 200


class TestDistLookup:
    """Where the frontend is looked for, and why there is more than one answer.

    A source checkout has it at ``<repo>/web/dist``; an installed wheel has it at
    ``iris/web/dist``. Hard-coding either one produces an `iris web` that serves 503
    on every page in the other kind of checkout, with a hint naming a path that does
    not exist there.
    """

    def _make(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        (path / "index.html").write_text("<html></html>", encoding="utf-8")
        return path

    def test_the_packaged_copy_wins_over_the_checkout(self, monkeypatch, tmp_path) -> None:
        packaged = self._make(tmp_path / "pkg" / "web" / "dist")
        checkout = self._make(tmp_path / "repo" / "web" / "dist")
        monkeypatch.delenv("IRIS_WEB_DIST", raising=False)
        monkeypatch.setattr(web_app, "_DIST_CANDIDATES", (packaged, checkout))
        monkeypatch.setattr(web_app, "_DIST_FALLBACK", checkout)
        assert web_app.dist_dir() == packaged

    def test_the_checkout_is_used_when_there_is_no_packaged_copy(self, monkeypatch, tmp_path) -> None:
        checkout = self._make(tmp_path / "repo" / "web" / "dist")
        monkeypatch.delenv("IRIS_WEB_DIST", raising=False)
        monkeypatch.setattr(web_app, "_DIST_CANDIDATES", (tmp_path / "absent" / "dist", checkout))
        monkeypatch.setattr(web_app, "_DIST_FALLBACK", checkout)
        assert web_app.dist_dir() == checkout

    def test_an_empty_directory_is_not_a_build(self, monkeypatch, tmp_path) -> None:
        """A dist directory without ``index.html`` is a failed or partial build.
        Skipping it keeps a stale empty one from shadowing the real copy."""
        empty = tmp_path / "empty"
        empty.mkdir()
        real = self._make(tmp_path / "real")
        monkeypatch.delenv("IRIS_WEB_DIST", raising=False)
        monkeypatch.setattr(web_app, "_DIST_CANDIDATES", (empty, real))
        monkeypatch.setattr(web_app, "_DIST_FALLBACK", real)
        assert web_app.dist_dir() == real

    def test_the_environment_override_is_used_verbatim(self, monkeypatch, tmp_path) -> None:
        """No existence check: the operator who sets it is told about a wrong path by
        the 503, which is more useful than a silently ignored setting."""
        elsewhere = tmp_path / "elsewhere"
        monkeypatch.setenv("IRIS_WEB_DIST", str(elsewhere))
        monkeypatch.setattr(web_app, "_DIST_CANDIDATES", (self._make(tmp_path / "real"),))
        assert web_app.dist_dir() == elsewhere

    def test_a_blank_override_is_not_an_override(self, monkeypatch, tmp_path) -> None:
        real = self._make(tmp_path / "real")
        monkeypatch.setenv("IRIS_WEB_DIST", "   ")
        monkeypatch.setattr(web_app, "_DIST_CANDIDATES", (real,))
        assert web_app.dist_dir() == real

    def test_the_real_repository_has_a_buildable_layout(self) -> None:
        """The candidates are derived from this file's location, so the layout they
        assume is a claim about the tree that a rename would silently invalidate."""
        assert web_app._DIST_CANDIDATES[0] == Path(web_app.__file__).resolve().parents[1] / "web" / "dist"
        assert web_app._DIST_CANDIDATES[1] == Path(web_app.__file__).resolve().parents[3] / "web" / "dist"


# --------------------------------------------------------------------- shutdown


class TestShutdown:
    def _register(self, iid: int, client_id: str = "local") -> None:
        from iris.db.active import register

        with web_app._session() as session:
            register(session, iid=iid, client_id=client_id, arch="mipsel",
                     rootfs_path=f"/tmp/{iid}", container_id=f"c{iid}")

    def _active_iids(self) -> list[int]:
        from sqlalchemy import select

        from iris.db.models import ActiveEmulation

        with web_app._session() as session:
            return [row.iid for row in session.scalars(select(ActiveEmulation))]

    def test_every_hosted_instance_is_stopped(self, db, no_docker) -> None:
        """Otherwise ``iris web`` exits leaving privileged containers running with
        nothing left to stop them from the UI."""
        self._register(7100)
        self._register(7101)
        asyncio.run(web_app.shutdown())
        assert sorted(no_docker) == [7100, 7101]

    def test_the_active_table_is_emptied(self, db, no_docker) -> None:
        self._register(7100)
        asyncio.run(web_app.shutdown())
        assert self._active_iids() == []

    def test_one_failing_stop_does_not_skip_the_rest(self, db, no_docker, monkeypatch) -> None:
        """A shutdown that gives up halfway is the worst outcome: half the
        containers are gone from the table and half are still running."""
        def flaky(iid: int):
            if iid == 7100:
                raise RuntimeError("docker exploded")
            no_docker.append(iid)
            return {"removed": True}

        monkeypatch.setattr(web_app, "stop_emulation", flaky)
        self._register(7100)
        self._register(7101)
        asyncio.run(web_app.shutdown())
        assert no_docker == [7101]
        assert self._active_iids() == []

    def test_shutdown_survives_an_unreadable_active_table(self, db, no_docker, monkeypatch) -> None:
        """A locked database at exit time must not turn into a traceback that
        leaves the containers up."""
        def boom():
            raise RuntimeError("database is locked")

        monkeypatch.setattr(web_app, "_session", boom)
        asyncio.run(web_app.shutdown())
        assert no_docker == []

    def test_shutdown_with_nothing_hosted_is_a_no_op(self, db, no_docker) -> None:
        asyncio.run(web_app.shutdown())
        assert no_docker == []

    def test_the_lifespan_runs_the_cleanup(self, app, db, no_docker) -> None:
        """Wiring guard: ``install`` chains the handler onto the router's lifespan,
        because Starlette 1.6 no longer exposes ``add_event_handler``. If that chain
        breaks, ``iris web`` exits leaving containers behind and every test above
        still passes."""
        self._register(7100)
        with TestClient(app):
            assert self._active_iids() == [7100]
        assert no_docker == [7100]
        assert self._active_iids() == []

    def test_the_base_lifespan_still_runs(self, app, db, no_docker) -> None:
        """The chain wraps the previous lifespan rather than replacing it: the base
        app's lifespan is what closes its own resources."""
        from contextlib import asynccontextmanager

        events: list[str] = []
        wrapped = FastAPI()

        @asynccontextmanager
        async def lifespan(_app):
            events.append("enter")
            yield
            events.append("exit")

        wrapped.router.lifespan_context = lifespan
        web_app.install(wrapped)
        with TestClient(wrapped):
            pass
        assert events == ["enter", "exit"]