"""`iris web`: the command a judge types, and the safety gate in front of it.

The command is exercised through its own function with ``uvicorn.run`` replaced,
because the alternative -- really binding a port -- tests the operating system
rather than this code, and a test that leaves a server listening is a test that
breaks the next one. What is asserted is everything that happens *before* the
server starts, because that is where the decisions live.

Faking ``uvicorn.run`` also makes the app object it was handed inspectable
afterwards, which is how the assembly is verified: the command has to install the
dashboard onto the app it passes on, not merely mention it.
"""

from __future__ import annotations

import os
import threading
import time
import webbrowser
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from iris.cli import _browser_host, _open_browser_soon, _web_app, _web_banner_rows, web_workbench


@pytest.fixture(autouse=True)
def _restore_api_token_env():
    """Put ``$IRIS_API_TOKEN`` back after every test in this file.

    ``web_workbench`` exports an explicit ``--api-token`` on purpose, so that a
    ``--reload`` worker inherits it. That write outlives the test that caused it
    unless something undoes it -- and the next suite to read the environment (the
    API security tests, among others) would answer 401 against a token nobody
    configured.
    """
    previous = os.environ.get("IRIS_API_TOKEN")
    yield
    if previous is None:
        os.environ.pop("IRIS_API_TOKEN", None)
    else:
        os.environ["IRIS_API_TOKEN"] = previous


@pytest.fixture
def served(monkeypatch):
    """Capture what the command handed to uvicorn, and stop it from serving."""
    calls: list[dict] = []

    def fake_run(target, **kwargs):
        calls.append({"target": target, **kwargs})

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return calls


@pytest.fixture(autouse=True)
def timers(monkeypatch):
    """Capture scheduled timers instead of arming them.

    The real ``Timer`` would fire 1.2 seconds into some later test. Here the delay
    is recorded and the callback kept, so a test can assert *when* the browser
    opens and call the callback itself to see *what* it opens.
    """
    scheduled: list[tuple[float, object]] = []
    real_timer = threading.Timer

    class Recorded(real_timer):
        def __init__(self, delay, function):
            super().__init__(delay, function)
            scheduled.append((delay, function))

    monkeypatch.setattr(threading, "Timer", Recorded)
    return scheduled


@pytest.fixture
def opened(monkeypatch):
    """Record browser launches instead of opening one."""
    seen: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: seen.append(url) or True)
    return seen


@pytest.fixture
def no_token(monkeypatch):
    """Run with no token configured anywhere, for the refusal cases."""
    from iris.api import auth
    from iris.config import Settings

    monkeypatch.setattr(auth, "get_settings", lambda: Settings(api_token=""))


def run(**kwargs) -> None:
    """Invoke the command with the defaults a bare `iris web` would use."""
    options = {"host": "127.0.0.1", "port": 9000, "api_token": "",
               "no_browser": True, "reload": False}
    options.update(kwargs)
    web_workbench(**options)


# ------------------------------------------------------------------- the gate


class TestTheLoopbackGate:
    def test_a_wildcard_bind_without_a_token_refuses(self, served, no_token) -> None:
        with pytest.raises(typer.Exit) as raised:
            run(host="0.0.0.0")
        assert raised.value.exit_code == 2
        assert served == []

    def test_a_routable_bind_is_refused_too(self, served, no_token) -> None:
        """Not just the wildcard: any address off this machine has the same
        exposure, and a demo on a laptop is often on wifi."""
        with pytest.raises(typer.Exit) as raised:
            run(host="192.168.1.20")
        assert raised.value.exit_code == 2
        assert served == []

    def test_a_wildcard_bind_with_a_token_starts(self, served) -> None:
        run(host="0.0.0.0", api_token="t")
        assert served and served[0]["host"] == "0.0.0.0"

    def test_loopback_needs_no_token(self, served, no_token) -> None:
        """The default case must stay zero-configuration, or the demo needs a setup
        step and the whole point is that it does not."""
        run()
        assert served and served[0]["host"] == "127.0.0.1"

    def test_localhost_counts_as_loopback(self, served, no_token) -> None:
        run(host="localhost")
        assert len(served) == 1


# ---------------------------------------------------------------- what it serves


class TestWhatItServes:
    def test_the_app_object_is_passed_not_an_import_string(self, served) -> None:
        """An import string would hand uvicorn the un-installed app: every page
        would 404 while the command reported a successful start."""
        run()
        from iris.api.server import app as api_app

        assert served[0]["target"] is api_app

    def test_the_dashboard_is_installed_before_serving(self, served) -> None:
        run()
        from iris.api.server import app as api_app

        paths = [getattr(route, "path", "") for route in api_app.routes]
        assert "/api/v1/stats" in paths
        assert "/ws/terminal" in paths

    def test_the_reload_path_goes_through_the_factory(self, served) -> None:
        """uvicorn re-executes an import string in a child process, so passing the
        object would raise there instead of reloading."""
        run(reload=True)
        assert served[0]["target"] == "iris.cli:_web_app"
        assert served[0]["reload"] is True

    def test_the_factory_returns_the_installed_app(self) -> None:
        from iris.api.server import app as api_app

        assert _web_app() is api_app

    def test_the_port_is_passed_through(self, served) -> None:
        run(port=9123)
        assert served[0]["port"] == 9123


# ---------------------------------------------------------------- the token


class TestTheTokenReachesTheWorker:
    def test_an_explicit_token_is_exported_for_the_reload_child(self, served, monkeypatch) -> None:
        """The child process re-imports the app; a token that only lived in this
        process would leave the worker serving unauthenticated."""
        monkeypatch.delenv("IRIS_API_TOKEN", raising=False)
        run(api_token="from-flag")
        assert os.environ["IRIS_API_TOKEN"] == "from-flag"

    def test_an_explicit_token_overrides_an_inherited_one(self, served, monkeypatch) -> None:
        monkeypatch.setenv("IRIS_API_TOKEN", "from-env")
        run(api_token="from-flag")
        assert os.environ["IRIS_API_TOKEN"] == "from-flag"

    def test_a_token_from_the_environment_is_not_rewritten(self, served, monkeypatch) -> None:
        """Rewriting it would be harmless but wrong in spirit: the flag was not
        given, so the environment is still the configuration."""
        monkeypatch.setenv("IRIS_API_TOKEN", "from-env")
        run()
        assert os.environ["IRIS_API_TOKEN"] == "from-env"


# ------------------------------------------------------------------- browser


class TestTheBrowser:
    def test_the_url_is_built_from_the_bind_address(self, served, opened, timers) -> None:
        run(port=9123, no_browser=False)
        _delay, open_it = timers[0]
        open_it()
        assert opened == ["http://127.0.0.1:9123/"]

    def test_a_wildcard_bind_opens_loopback(self, served, opened, timers) -> None:
        """``0.0.0.0`` is a bind address, not a destination. A browser pointed at
        it fails on some machines and works on others."""
        run(host="0.0.0.0", api_token="t", no_browser=False)
        _delay, open_it = timers[0]
        open_it()
        assert opened == ["http://127.0.0.1:9000/"]

    def test_the_open_is_delayed_so_the_listener_exists_first(self, served, timers) -> None:
        """Opening before ``uvicorn.run`` is a race the browser usually loses, and
        the user sees a connection error on a server that is about to work."""
        run(no_browser=False)
        assert timers[0][0] > 0

    def test_no_browser_opens_nothing(self, served, opened, timers) -> None:
        run()
        assert timers == []
        assert opened == []

    def test_a_failing_browser_is_not_a_traceback(self, monkeypatch, capsys) -> None:
        def boom(url):
            raise RuntimeError("no display")

        monkeypatch.setattr(webbrowser, "open", boom)
        _open_browser_soon("http://127.0.0.1:9000/", delay=0.01)
        time.sleep(0.2)
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "no display" in captured.err

    def test_the_wildcard_becomes_loopback_in_the_url(self) -> None:
        assert _browser_host("0.0.0.0") == "127.0.0.1"
        assert _browser_host("::") == "127.0.0.1"
        assert _browser_host("192.168.1.5") == "192.168.1.5"
        assert _browser_host(" localhost ") == "localhost"


# -------------------------------------------------------------------- banner


class TestTheBanner:
    def _rows(self, token: str = "") -> dict[str, str]:
        return dict(_web_banner_rows("127.0.0.1", 9000, token, "http://127.0.0.1:9000/"))

    def test_local_mode_is_stated(self) -> None:
        assert "local mode" in self._rows()["mode"]

    def test_token_mode_is_stated(self) -> None:
        assert self._rows("t")["mode"] == "token required"

    def test_the_console_row_follows_the_build(self) -> None:
        """The banner is the first thing a judge reads; claiming a console this
        build cannot provide is worse than saying nothing."""
        row = self._rows()["console"]
        assert "console available" in row or "one-way" in row

    def test_the_database_password_is_not_printed(self, monkeypatch) -> None:
        from iris.api import web_app
        from iris.config import Settings

        monkeypatch.setattr(web_app, "get_settings",
                            lambda: Settings(database_url="postgresql://iris:hunter2@db/iris"))
        assert "hunter2" not in self._rows()["database"]

    def test_every_row_has_a_label_and_a_value(self) -> None:
        for label, value in _web_banner_rows("127.0.0.1", 9000, "", "http://127.0.0.1:9000/"):
            assert label.strip(), value
            assert value.strip(), label

    def test_the_version_is_shown(self) -> None:
        from iris import __version__

        assert self._rows()["version"] == __version__


# ----------------------------------------------------- the unbuilt-frontend note


class TestTheUnbuiltFrontendNote:
    def test_an_unbuilt_frontend_is_called_out_at_startup(self, served, monkeypatch, capsys) -> None:
        """503 on every page with no explanation is what a judge sees if the wheel
        was installed without ``web/dist``. The startup output has to say so, with
        the command that fixes it."""
        from iris.api import web_app

        monkeypatch.setattr(web_app, "dist_dir", lambda: Path("/nowhere/web/dist"))
        run()
        captured = capsys.readouterr()
        assert "npm run build" in captured.out + captured.err

    def test_a_built_frontend_says_nothing(self, served, monkeypatch, capsys, tmp_path) -> None:
        from iris.api import web_app

        (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
        monkeypatch.setattr(web_app, "dist_dir", lambda: tmp_path)
        run()
        captured = capsys.readouterr()
        assert "npm run build" not in captured.out + captured.err


# ---------------------------------------------------------------- the command


class TestTheCommandItself:
    def test_it_is_registered_under_the_name_a_user_types(self) -> None:
        result = CliRunner().invoke(__import__("iris.cli", fromlist=["app"]).app, ["web", "--help"])
        assert result.exit_code == 0
        assert "workbench" in result.output

    def test_its_help_lists_the_documented_flags(self) -> None:
        result = CliRunner().invoke(__import__("iris.cli", fromlist=["app"]).app, ["web", "--help"])
        for flag in ("--host", "--port", "--api-token", "--no-browser", "--reload"):
            assert flag in result.output, flag

    def test_it_adds_no_configuration_flag(self) -> None:
        """Configuration comes from .env and IRIS_* like the rest of the CLI. A
        second source would be a second thing to get wrong at a demo."""
        result = CliRunner().invoke(__import__("iris.cli", fromlist=["app"]).app, ["web", "--help"])
        assert "--config" not in result.output