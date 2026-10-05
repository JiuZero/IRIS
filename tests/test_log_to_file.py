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
        config = uvicorn_log_config()
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            assert config["loggers"][name]["handlers"] == ["console"]
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
        assert logging.getLogger("uvicorn.access").handlers

    def test_all_three_uvicorn_run_calls_pass_it(self):
        """Otherwise the config exists and nothing uses it."""
        source = (ROOT / "src/iris/cli.py").read_text(encoding="utf-8")
        runs = re.findall(r"uvicorn\.run\((?:[^()]|\([^()]*\))*\)", source, re.DOTALL)
        assert len(runs) == 3, runs
        for call in runs:
            assert "log_config=uvicorn_log_config()" in call, call


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