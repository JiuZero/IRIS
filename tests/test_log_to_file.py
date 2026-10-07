"""A long-running service can keep a record, and every channel lands in it.

``iris web`` was the one command whose output existed only in the terminal it was
started from, and the half of it that came from uvicorn arrived in a different
shape with no timestamp at all -- so the record of a failed launch could not be
read against the rest of what happened.

Both halves are pinned here, and the awkward one is pinned deliberately: structlog
writes through its own logger factory straight to stdout, so attaching a
``FileHandler`` to the root logger captures uvicorn and nothing of IRIS. The file
is only worth having if it holds both, so a test writes one line each way and reads
the file back.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from iris.config import Settings
from iris.log import _file_handler, setup_logging, uvicorn_log_config

ROOT = Path(__file__).resolve().parents[1]
ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def _restore_logging():
    """setup_logging rewires global state; put it back for whatever runs next."""
    saved = logging.getLogger().handlers[:]
    saved_level = logging.getLogger().level
    yield
    root = logging.getLogger()
    root.handlers[:] = saved
    root.setLevel(saved_level)


class TestTheFileIsWrittenOnlyWhenAsked:
    def test_nothing_is_written_without_a_path(self, tmp_path, capsys):
        setup_logging("INFO", None)
        logging.getLogger("iris.test").info("not to a file")
        assert not list(tmp_path.iterdir())
        assert "not to a file" in capsys.readouterr().out

    def test_the_path_is_enough(self, tmp_path):
        log_file = tmp_path / "nested" / "iris.log"
        setup_logging("INFO", log_file)
        logging.getLogger("iris.test").info("stdlib line")
        assert "stdlib line" in log_file.read_text(encoding="utf-8")

    def test_structlog_lines_reach_the_file_too(self, tmp_path):
        """The half a root FileHandler silently misses."""
        from structlog import get_logger
        log_file = tmp_path / "iris.log"
        setup_logging("INFO", log_file)
        get_logger("iris.test").info("structlog line", iid=6715)
        text = log_file.read_text(encoding="utf-8")
        assert "structlog line" in text
        assert "6715" in text, "extras are dropped by the renderer's message join"

    def test_both_channels_produce_the_same_shape_in_the_file(self, tmp_path):
        from structlog import get_logger
        log_file = tmp_path / "iris.log"
        setup_logging("INFO", log_file)
        logging.getLogger("iris.test").info("from stdlib")
        get_logger("iris.test").info("from structlog")
        shapes = [re.sub(r"^\S+ ", "", line)
                  for line in log_file.read_text(encoding="utf-8").splitlines()
                  if line.strip()]
        assert shapes == ["[info] from stdlib", "[info] from structlog"]

    def test_a_second_file_does_not_inherit_the_first(self, tmp_path):
        """A cached structlog logger keeps the renderer it was built with."""
        first, second = tmp_path / "a.log", tmp_path / "b.log"
        from structlog import get_logger
        setup_logging("INFO", first)
        get_logger("iris.test").info("into a")
        setup_logging("INFO", second)
        get_logger("iris.test").info("into b")
        assert "into b" in second.read_text(encoding="utf-8")
        assert "into b" not in first.read_text(encoding="utf-8")


class TestTheFileIsReadableAndBounded:
    def test_the_file_carries_no_escape_sequences(self, tmp_path):
        """Whatever the console does, a log file has to be greppable."""
        from structlog import get_logger
        log_file = tmp_path / "iris.log"
        setup_logging("INFO", log_file)
        get_logger("iris.test").info("plain text")
        assert not ANSI.search(log_file.read_text(encoding="utf-8"))

    def test_a_traceback_lands_in_the_file(self, tmp_path):
        from structlog import get_logger
        log_file = tmp_path / "iris.log"
        setup_logging("INFO", log_file)
        try:
            raise ValueError("guest did not come up")
        except ValueError:
            get_logger("iris.test").error("emulation failed", exc_info=True)
        assert "ValueError" in log_file.read_text(encoding="utf-8")

    def test_the_handler_rotates_rather_than_growing_without_limit(self, tmp_path):
        handler = _file_handler(tmp_path / "iris.log", level="INFO")
        assert handler.maxBytes > 0
        assert handler.backupCount > 0

    def test_the_rotation_keeps_only_a_few_generations(self, tmp_path):
        from iris.log import _LOG_BACKUPS
        assert 0 < _LOG_BACKUPS <= 5, "a week-long service has to survive its own log"


class TestAnUnwritableLogDoesNotStopTheTool:
    def test_a_file_under_a_path_that_cannot_be_made_is_reported_not_raised(self, tmp_path, capsys):
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        handler = _file_handler(blocker / "sub" / "iris.log", level="INFO")
        assert handler is None
        assert "cannot write the log file" in capsys.readouterr().err

    def test_the_console_still_works_after_that(self, tmp_path, capsys):
        blocker = tmp_path / "blocker2"
        blocker.write_text("not a directory", encoding="utf-8")
        setup_logging("INFO", blocker / "sub" / "iris.log")
        logging.getLogger("iris.test").info("still on the console")
        assert "still on the console" in capsys.readouterr().out


class TestThePathFollowsIrisHome:
    def test_the_default_keeps_nothing_on_disk(self):
        assert Settings(iris_home=ROOT / "iris-home").log_to_file is False

    def test_the_file_lives_under_the_home_it_was_given(self):
        home = ROOT / "iris-home"
        assert Settings(iris_home=home).log_file == home / "logs" / "iris.log"

    def test_a_throwaway_home_gets_a_throwaway_log(self, tmp_path):
        """The reason it is derived rather than a literal path."""
        assert Settings(iris_home=tmp_path).log_file.is_relative_to(tmp_path)

    def test_the_setting_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("IRIS_LOG_TO_FILE", "true")
        assert Settings(iris_home=ROOT / "iris-home").log_to_file is True


class TestUvicornLogsInTheSameShape:
    def test_every_uvicorn_logger_uses_the_iris_formatter(self):
        """No handler of their own, and none disabled: the root does the writing."""
        config = uvicorn_log_config()
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            assert config["loggers"][name]["handlers"] == []
            assert config["loggers"][name]["propagate"] is True
        assert config["handlers"]["console"]["formatter"] == "iris"
        assert config["formatters"]["iris"]["()"] is not None

    def test_the_access_log_is_not_left_without_a_timestamp(self):
        """uvicorn's own access record is `INFO: 127.0.0.1:52344 - "GET /" 200`."""
        from iris.log import PlainFormatter
        record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0,
                                   '127.0.0.1:52344 - "GET / HTTP/1.1" 200 OK', None, None)
        rendered = PlainFormatter(color=False).format(record)
        assert re.match(r"^\S+ \[info\] 127\.0\.0\.1:52344", rendered), rendered

    def test_existing_loggers_are_not_disabled(self):
        """uvicorn's default config sets this False too; True would erase IRIS's own."""
        assert uvicorn_log_config()["disable_existing_loggers"] is False

    def test_the_config_is_applied_by_dictconfig_without_losing_the_root_handler(self):
        """A dictConfig that mentions no ``root`` must leave IRIS's handlers alone."""
        import logging.config
        setup_logging("INFO")
        root = logging.getLogger()
        before = list(root.handlers)
        assert before, "IRIS installs a console handler; there is nothing to protect"
        logging.config.dictConfig(uvicorn_log_config())
        assert list(root.handlers) == before, "uvicorn's config replaced IRIS's own"
        assert logging.getLogger("uvicorn.access").propagate is True

    def test_a_uvicorn_record_reaches_a_file_written_by_setup_logging(self, tmp_path):
        """The regression: its own handler plus ``propagate: False`` left the file empty."""
        import logging.config
        log_file = tmp_path / "iris.log"
        setup_logging("INFO", log_file)
        logging.config.dictConfig(uvicorn_log_config())
        logging.getLogger("uvicorn.access").info('127.0.0.1:52344 - "GET /api/v1/health 200')
        text = log_file.read_text(encoding="utf-8")
        assert "GET /api/v1/health" in text, "uvicorn's access log never reached the file"
        assert re.search(r"^\S+ \[info\] 127\.0\.0\.1:52344", text, re.MULTILINE), text

    def test_all_three_uvicorn_run_calls_pass_it(self):
        """Otherwise the config exists and nothing uses it."""
        source = (ROOT / "src/iris/cli.py").read_text(encoding="utf-8")
        runs = re.findall(r"uvicorn\.run\((?:[^()]|\([^()]*\))*\)", source, re.DOTALL)
        assert len(runs) == 3, runs
        for call in runs:
            assert "log_config=uvicorn_log_config()" in call, call

    def test_uvicorn_can_write_use_colors_into_this_config(self):
        """``configure_logging`` indexes ``formatters['default']`` and ``['access']``
        whenever use_colors is a bool; a dict missing them dies inside uvicorn
        before the server binds."""
        import uvicorn

        config = uvicorn_log_config()
        uvicorn.Config("iris.api.server:app", log_config=config, use_colors=True)
        uvicorn.Config("iris.api.server:app", log_config=uvicorn_log_config(),
                       use_colors=False)

    def test_structlog_still_writes_after_dictconfig_closed_the_handler(self, tmp_path):
        """dictConfig calls logging.shutdown, which closes the file handler.

        A sink holding the stream object raises ``ValueError: I/O operation on
        closed file`` on the first record after the server starts -- the file would
        work until launch and then lose exactly the records worth keeping.
        """
        import logging.config

        from structlog import get_logger

        log_file = tmp_path / "iris.log"
        setup_logging("INFO", log_file)
        logging.config.dictConfig(uvicorn_log_config())
        get_logger("iris.test").info("after dictconfig", iid=1)
        text = log_file.read_text(encoding="utf-8")
        assert "after dictconfig" in text
        assert "iid=1" in text, "the sink lost the extras along with the stream"


class TestTheServiceReallyWritesBoth:
    """One real child process, so nothing is asserted about a mechanism that is off."""

    def test_a_real_child_writes_a_stdlib_and_a_structlog_line_to_one_file(self, tmp_path):
        log_file = tmp_path / "iris.log"
        code = (
            "import logging, structlog;"
            "from iris.log import setup_logging;"
            f"setup_logging('INFO', r'{log_file}');"
            "logging.getLogger('probe').info('stdlib reached disk');"
            "structlog.get_logger('probe').info('structlog reached disk')"
        )
        result = subprocess.run([sys.executable, "-c", code],
                                capture_output=True, text=True, cwd=ROOT, check=False)
        assert result.returncode == 0, result.stderr
        text = log_file.read_text(encoding="utf-8")
        assert "stdlib reached disk" in text
        assert "structlog reached disk" in text

    def test_a_real_iris_serve_lands_its_access_log_in_the_file(self, tmp_path):
        """The 0.3.25 claim, checked the only way that counts: run the command.

        A uvicorn ``dictConfig`` that gives its own loggers a console handler and
        ``propagate: False`` leaves a real ``iris serve`` writing a 0-byte log --
        the test above passes anyway, because it never goes through that path.
        """
        import json
        import socket
        import time
        import urllib.error
        import urllib.request

        home = tmp_path / "home"
        log_file = home / "logs" / "iris.log"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "IRIS_IRIS_HOME": str(home),
            "IRIS_LOG_TO_FILE": "true",
            "NO_COLOR": "1",
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "iris.cli", "serve", "start", "--port", str(port)],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    pytest.fail(f"iris serve exited early:\n{proc.stdout.read()}")
                try:
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/api/v1/health", timeout=2) as resp:
                        assert json.loads(resp.read())["status"] == "ok"
                    break
                except (urllib.error.URLError, OSError):
                    time.sleep(0.3)
            else:
                pytest.fail("iris serve never answered /api/v1/health")
        finally:
            proc.terminate()
            proc.wait(timeout=30)

        text = log_file.read_text(encoding="utf-8")
        assert "GET /api/v1/health" in text, (
            "the access log stayed out of the log file, so the file records nothing "
            f"of the service's own traffic:\n{text}"
        )
        assert re.search(r"^\S+ \[info\] .*GET /api/v1/health", text, re.MULTILINE), text
        assert not ANSI.search(text), "the file has to stay greppable"