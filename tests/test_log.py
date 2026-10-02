"""The log and progress output contract: ``[info] message``, unpadded, uniform.

Two regressions are guarded here:

* the level tag came out as ``[info     ]`` because structlog's ConsoleRenderer
  pads the level name to a table column, and stdlib records (uvicorn, FastAPI,
  the root logger) had no tag at all;
* the boot poll appended one line every five seconds instead of rewriting one.

The stdlib half is exercised through ``setup_logging`` and a real ``log.info``
call rather than by calling ``PlainFormatter`` directly, so the handler wiring
(``force=True``, the formatter actually being installed) is part of what the
test proves.
"""

from __future__ import annotations

import io
import logging

import pytest
import structlog

from iris.log import (
    LEVEL_TAGS,
    PlainFormatter,
    PlainRenderer,
    StatusLine,
    level_tag,
    setup_logging,
)


class _FakeTty(io.StringIO):
    """A StringIO that claims to be a terminal, to select the overwrite path."""

    def isatty(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _restore_logging():
    """setup_logging() reconfigures global state; undo it for the next test."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved_structlog = structlog.get_config()
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    structlog.configure(**saved_structlog)


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("info", "info"),
        ("INFO", "info"),
        ("debug", "debug"),
        ("warning", "warn"),
        ("WARNING", "warn"),
        ("warn", "warn"),
        ("error", "error"),
        ("exception", "error"),
        ("critical", "error"),
        ("success", "info"),
    ],
)
def test_level_tag_normalizes_to_closed_vocabulary(level, expected):
    assert level_tag(level) == expected


def test_level_tag_passes_through_unknown_level_lowercased():
    # Unknown levels keep their own text rather than being silently relabelled:
    # inventing an "info" tag for something unrecognised would misstate severity.
    assert level_tag("NOTICE") == "notice"


def test_level_tags_cover_every_structlog_and_stdlib_level():
    # A level missing from the map renders as its own name, which is exactly the
    # inconsistency this module exists to remove. Guard the map instead.
    assert set(LEVEL_TAGS) >= {
        "debug",
        "info",
        "warning",
        "error",
        "exception",
        "critical",
        "success",
    }


def test_plain_renderer_emits_unpadded_tag():
    line = PlainRenderer()(None, None, {"level": "info", "event": "提取 rootfs"})
    assert line == "[info] 提取 rootfs"


def test_plain_renderer_tag_is_never_column_padded():
    # The exact shape that was reported as ugly: no run of spaces before "]".
    line = PlainRenderer()(None, None, {"level": "info", "event": "x"})
    assert "] " in line
    assert not line.startswith("[info  ")
    assert "[info]" in line


def test_plain_renderer_defaults_to_info_when_level_absent():
    assert PlainRenderer()(None, None, {"event": "hello"}) == "[info] hello"


def test_plain_renderer_appends_extras_as_key_value_pairs():
    line = PlainRenderer()(None, None, {"level": "info", "event": "boot", "step": 1})
    assert line == "[info] boot step=1"


def test_plain_renderer_appends_traceback_for_exc_info_true():
    line = ""
    try:
        raise ZeroDivisionError("division by zero")
    except ZeroDivisionError:
        # exc_info=True means "the exception currently being handled", so this
        # only proves anything inside an except block.
        line = PlainRenderer()(None, None, {"level": "error", "event": "failed", "exc_info": True})
    assert line.startswith("[error] failed")
    assert "ZeroDivisionError" in line
    assert "division by zero" in line


def test_plain_renderer_appends_traceback_for_exception_instance():
    exc = ValueError("boom")
    line = PlainRenderer()(None, None, {"level": "error", "event": "failed", "exc_info": exc})
    assert "ValueError: boom" in line


def test_plain_renderer_prefers_rendered_stack_info_text():
    line = PlainRenderer()(
        None, None, {"level": "error", "event": "failed", "stack_info": "Stack (most recent call last): ..."}
    )
    assert line == "[error] failed\nStack (most recent call last): ..."


def test_plain_renderer_omits_traceback_when_none():
    line = PlainRenderer()(None, None, {"level": "info", "event": "ok", "exc_info": None})
    assert line == "[info] ok"


def test_plain_formatter_matches_renderer_shape():
    record = logging.LogRecord("uvicorn", logging.WARNING, __file__, 1, "port busy", None, None)
    assert PlainFormatter().format(record) == "[warn] port busy"


def test_plain_formatter_applies_argument_interpolation():
    record = logging.LogRecord("iris", logging.ERROR, __file__, 1, "port %s", ("8080",), None)
    assert PlainFormatter().format(record) == "[error] port 8080"


def test_setup_logging_routes_stdlib_records_through_plain_formatter(capsys):
    # Real execution path: a stdlib logger (uvicorn/FastAPI use these) must end
    # up with the same tag shape as structlog, not a bare unlabelled message.
    setup_logging("INFO")
    logging.getLogger("uvicorn.error").info("Application startup")
    logging.getLogger("iris").warning("port busy")
    out = capsys.readouterr().out
    assert "[info] Application startup" in out
    assert "[warn] port busy" in out


def test_setup_logging_routes_structlog_records_through_plain_renderer(capsys):
    setup_logging("INFO")
    structlog.get_logger("iris").info("提取 rootfs", step=1)
    out = capsys.readouterr().out
    assert "[info] 提取 rootfs step=1" in out
    assert "[info  " not in out


def test_setup_logging_is_idempotent_across_repeated_entry(capsys):
    # The CLI can be re-entered in one process. Without force=True the second
    # basicConfig is a no-op and the level would silently stay at the first one.
    setup_logging("DEBUG")
    setup_logging("WARNING")
    logging.getLogger("iris").info("should be filtered")
    logging.getLogger("iris").error("kept")
    out = capsys.readouterr().out
    assert "should be filtered" not in out
    assert "[error] kept" in out


def test_status_line_overwrites_in_place_on_a_terminal():
    stream = _FakeTty()
    status = StatusLine(stream)
    status.update("[10s] waiting for guest web on :8080")
    status.update("[15s] waiting for guest web on :8080")
    written = stream.getvalue()
    # Exactly two \r-prefixed fragments, no newline between them: the terminal
    # rewrites one physical line instead of appending.
    assert written.count("\n") == 0
    assert written.count("\r") == 2


def test_status_line_pads_shorter_update_so_no_tail_is_left_behind():
    stream = _FakeTty()
    status = StatusLine(stream)
    status.update("[100s] waiting for guest web on :8080")
    status.update("[5s] waiting")
    # Second fragment is padded back to the first one's length, so the leftover
    # "0 (HTTP 000)" from the longer line cannot survive on screen.
    second = stream.getvalue().split("\r")[2]
    assert second == "[5s] waiting" + " " * (len("[100s] waiting for guest web on :8080") - len("[5s] waiting"))


def test_status_line_close_terminates_the_line():
    stream = _FakeTty()
    status = StatusLine(stream)
    status.update("[15s] waiting")
    status.close()
    written = stream.getvalue()
    assert written.endswith("\n")


def test_status_line_close_leaves_the_verdict_to_the_log_record():
    """close() must not swallow the outcome: it emits no text of its own."""
    stream = _FakeTty()
    status = StatusLine(stream)
    status.update("[15s] waiting")
    status.close()
    print("[info] Web reachable at http://localhost:8080 after 79s", file=stream)
    assert "79s" in stream.getvalue().split("\n")[-2]


def test_status_line_close_is_safe_when_nothing_was_written():
    stream = _FakeTty()
    StatusLine(stream).close()
    # No phantom blank line from a close() on a line that never opened.
    assert stream.getvalue() == ""


def test_status_line_close_emits_nothing_on_a_non_terminal():
    # Each update already stood on its own line; a close would add a blank one.
    stream = io.StringIO()
    status = StatusLine(stream)
    status.update("waiting")
    status.close()
    assert stream.getvalue().splitlines() == ["[wait] waiting"]


def test_status_line_clear_erases_the_in_place_line():
    stream = _FakeTty()
    status = StatusLine(stream)
    status.update("[42s] waiting for guest web on :8080")
    status.clear()
    written = stream.getvalue()
    # Trailing spaces then a carriage return: the row is blank and ready for a
    # real log record, rather than having the next record spliced onto it.
    assert written.endswith("\r")
    assert written.endswith(" " * len("[42s] waiting for guest web on :8080") + "\r")
    assert "\n" not in written


def test_status_line_clear_then_log_record_starts_at_column_zero():
    stream = _FakeTty()
    status = StatusLine(stream)
    status.update("[42s] waiting for guest web on :8080 (HTTP 000)")
    status.clear()
    print("[info] Detected guest IP: 192.168.1.1", file=stream)
    written = stream.getvalue()
    # The record's own line must begin right after the erase, not after the
    # progress text.
    assert written.rstrip("\n").endswith("[info] Detected guest IP: 192.168.1.1")
    assert written.count("\n") == 1


def test_status_line_clear_resets_padding_so_next_update_is_not_padded():
    stream = _FakeTty()
    status = StatusLine(stream)
    status.update("[100s] waiting for guest web on :8080")
    status.clear()
    status.update("[5s] waiting")
    # After clear() the stale width must be gone, else the short update gets 85
    # phantom trailing spaces.
    assert stream.getvalue().split("\r")[-1] == "[5s] waiting"


def test_status_line_clear_is_a_noop_on_a_non_terminal():
    stream = io.StringIO()
    status = StatusLine(stream)
    status.update("waiting")
    status.clear()
    # Lines already stand on their own; erasing would emit a phantom record.
    assert stream.getvalue() == "[wait] waiting\n"


def test_status_line_clear_is_safe_before_any_update():
    stream = _FakeTty()
    StatusLine(stream).clear()
    assert stream.getvalue() == ""


def test_status_line_degrades_to_lines_when_not_a_terminal():
    # A redirected log must not be full of carriage returns.
    stream = io.StringIO()
    status = StatusLine(stream)
    status.update("[10s] waiting for guest web on :8080 (HTTP 000)")
    status.update("[15s] waiting for guest web on :8080 (HTTP 000)")
    lines = stream.getvalue().splitlines()
    assert "\r" not in stream.getvalue()
    assert lines == [
        "[wait] [10s] waiting for guest web on :8080 (HTTP 000)",
        "[wait] [15s] waiting for guest web on :8080 (HTTP 000)",
    ]


def test_status_line_degrades_when_stream_has_no_isatty():
    class _Bare:
        def __init__(self):
            self.written = []

        def write(self, text):
            self.written.append(text)

        def flush(self):
            pass

    stream = _Bare()
    status = StatusLine(stream)
    status.update("waiting")
    assert "".join(stream.written) == "[wait] waiting\n"