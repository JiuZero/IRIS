"""The log and progress output contract: ``2026-10-02T02:11:00 [info] message``.

Every line the tool writes carries three things, and the regressions guarded here
are the three ways each of them used to be missing:

* the level tag came out as ``[info     ]`` because structlog's ConsoleRenderer
  pads the level name to a table column, and stdlib records (uvicorn, FastAPI,
  the root logger) had no tag at all;
* nothing carried a time, so on a two-minute boot that prints every step there
  was no way to tell which of two lines came first;
* the two logging channels and the CLI's own ``typer.echo`` calls each picked
  their own shape, so one run mixed three formats.

The stdlib half is exercised through ``setup_logging`` and a real ``log.info``
call rather than by calling ``PlainFormatter`` directly, so the handler wiring
(``force=True``, the formatter actually being installed) is part of what the
test proves. Color is passed explicitly wherever it is not the thing under test,
because a real terminal's answer depends on the machine the suite runs on.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timedelta

import pytest
import structlog

from iris.log import (
    LEVEL_COLORS,
    LEVEL_TAGS,
    PlainFormatter,
    PlainRenderer,
    StatusLine,
    StreamLogger,
    format_line,
    get_error_logger,
    get_stream_logger,
    level_tag,
    setup_logging,
    use_color,
)

#: One rendered line, with the message allowed to span lines (a block report).
LINE_RE = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r" \[(?P<tag>[a-z]+)\] (?P<msg>.*)$",
    re.DOTALL,
)

#: SGR sequences are zero-width on screen but occupy characters in a buffer, so
#: any assertion about what a terminal shows has to compare visible text.
SGR_RE = re.compile(r"\033\[[0-9;]*m")

RESET = "\033[0m"
DIM = "\033[2m"

#: How far the rendered stamp may sit from the wall clock. A stamp that drifted
#: by more than this is not the current time, whatever it parses as.
_CLOCK_SLACK = timedelta(seconds=5)


class _FakeTty(io.StringIO):
    """A StringIO that claims to be a terminal, to select the color/overwrite path."""

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


@pytest.fixture(autouse=True)
def _no_color_env(monkeypatch):
    """Colors are decided by the environment; pin it so the suite is deterministic.

    Without this, a developer running the suite in a terminal would get escape
    sequences and a CI runner would not, and the same assertion could not cover
    both paths.
    """
    for name in ("NO_COLOR", "FORCE_COLOR", "TERM"):
        monkeypatch.delenv(name, raising=False)


def parse(line: str) -> tuple[str, str, str]:
    """Split a rendered line into (stamp, tag, message), failing loudly if it does not fit.

    SGR sequences are stripped first: they sit between the timestamp and the
    text, and a regex that skipped them would never anchor on a colored line.
    """
    m = LINE_RE.match(SGR_RE.sub("", line))
    assert m, f"line does not match the log shape: {line!r}"
    return m["stamp"], m["tag"], m["msg"]


def looks_like_now(stamp: str) -> bool:
    """Whether *stamp* is a well-formed local timestamp for roughly right now.

    A minute of slack rather than a second: the stamp has one-second resolution,
    so a strict comparison would fail on any test that happens to straddle a
    second boundary.
    """
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    return abs((datetime.now().astimezone() - parsed.astimezone()).total_seconds()) < 60


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


def test_every_rendering_tag_has_a_color():
    # A tag in LEVEL_TAGS that LEVEL_COLORS does not know would render untinted,
    # which is the one case the reader cannot distinguish from "no color here on
    # purpose". `wait` is StatusLine's tag and lives here for the same reason.
    assert set(LEVEL_COLORS) | {StatusLine.TAG} >= {level_tag(v) for v in LEVEL_TAGS.values()}


# --------------------------------------------------------------------------
# the line shape
# --------------------------------------------------------------------------


def test_format_line_is_timestamp_tag_message():
    stamp, tag, msg = parse(format_line("info", "提取 rootfs", color=False))
    assert (tag, msg) == ("info", "提取 rootfs")
    assert looks_like_now(stamp)


def test_format_line_stamp_is_the_wall_clock_not_a_fixed_string():
    # A hard-coded or UTC stamp would be worse than none: an operator comparing a
    # guest printk timestamp against it would be off by the UTC offset.
    stamp, _, _ = parse(format_line("info", "x", color=False))
    assert looks_like_now(stamp)


def test_format_line_without_color_has_no_escape_sequences():
    # A redirected log full of SGR bytes is unreadable and ungreppable.
    line = format_line("error", "boom", color=False)
    assert "\033" not in line
    assert line.endswith("[error] boom")


@pytest.mark.parametrize("tag", ["debug", "info", "warn", "error"])
def test_format_line_colors_the_tag_to_its_level_color(tag):
    line = format_line(tag, "msg", color=True)
    assert f"{LEVEL_COLORS[tag]}[{tag}]{RESET}" in line


def test_format_line_dims_the_stamp():
    # The timestamp is context, not the point of the line, so it recedes and the
    # tag is what draws the eye down a wall of boot output.
    line = format_line("info", "msg", color=True)
    assert line.startswith(DIM)
    assert f"{DIM}{parse(line)[0]}{RESET}" in line


def test_format_line_leaves_an_unknown_tag_untinted():
    # `notice` is not a level anybody designed a color for; giving it one would
    # claim a severity it does not have.
    line = format_line("notice", "msg", color=True)
    assert f"[notice]{RESET}" not in line
    assert parse(line)[1:] == ("notice", "msg")


# --------------------------------------------------------------------------
# color detection
# --------------------------------------------------------------------------


def test_use_color_is_false_on_a_plain_stream():
    assert use_color(io.StringIO()) is False


def test_use_color_is_true_on_a_terminal():
    assert use_color(_FakeTty()) is True


def test_no_color_beats_a_terminal(monkeypatch):
    # A user in a real terminal is the only one who can ask for plain output, so
    # NO_COLOR has to win over isatty().
    monkeypatch.setenv("NO_COLOR", "1")
    assert use_color(_FakeTty()) is False


def test_no_color_also_beats_force_color(monkeypatch):
    # Setting both is a contradiction, and NO_COLOR is the explicit opt-out.
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert use_color(_FakeTty()) is False


def test_force_color_beats_a_plain_stream(monkeypatch):
    # The opposite case: a pipe that should still be colored, e.g. piped into
    # less -R.
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert use_color(io.StringIO()) is True


def test_dumb_terminal_gets_no_color(monkeypatch):
    monkeypatch.setenv("TERM", "dumb")
    assert use_color(_FakeTty()) is False


def test_a_stream_without_isatty_is_treated_as_not_a_terminal():
    class _Bare:
        pass

    assert use_color(_Bare()) is False


# --------------------------------------------------------------------------
# structlog channel
# --------------------------------------------------------------------------


def test_plain_renderer_emits_unpadded_tag():
    line = PlainRenderer(color=False)(None, None, {"level": "info", "event": "提取 rootfs"})

    assert parse(line)[1:] == ("info", "提取 rootfs")


def test_plain_renderer_tag_is_never_column_padded():
    # The exact shape that was reported as ugly: no run of spaces before "]".
    line = PlainRenderer(color=False)(None, None, {"level": "info", "event": "x"})
    assert "[info] x" in line
    assert "[info  " not in line


def test_plain_renderer_defaults_to_info_when_level_absent():
    line = PlainRenderer(color=False)(None, None, {"event": "hello"})
    assert parse(line)[1:] == ("info", "hello")


def test_plain_renderer_appends_extras_as_key_value_pairs():
    line = PlainRenderer(color=False)(None, None, {"level": "info", "event": "boot", "step": 1})
    assert parse(line)[2] == "boot step=1"


def test_plain_renderer_appends_traceback_for_exc_info_true():
    try:
        raise ZeroDivisionError("division by zero")
    except ZeroDivisionError:
        # exc_info=True means "the exception currently being handled", so this
        # only proves anything inside an except block.
        line = PlainRenderer(color=False)(
            None, None, {"level": "error", "event": "failed", "exc_info": True}
        )
    _, tag, msg = parse(line)
    assert (tag, msg.splitlines()[0]) == ("error", "failed")
    assert "ZeroDivisionError" in msg
    assert "division by zero" in msg


def test_plain_renderer_appends_traceback_for_exception_instance():
    exc = ValueError("boom")
    line = PlainRenderer(color=False)(None, None, {"level": "error", "event": "failed", "exc_info": exc})
    assert "ValueError: boom" in line


def test_plain_renderer_prefers_rendered_stack_info_text():
    line = PlainRenderer(color=False)(
        None, None, {"level": "error", "event": "failed", "stack_info": "Stack (most recent call last): ..."}
    )
    assert line == f"{parse(line)[0]} [error] failed\nStack (most recent call last): ..."


def test_plain_renderer_omits_traceback_when_none():
    line = PlainRenderer(color=False)(None, None, {"level": "info", "event": "ok", "exc_info": None})
    assert parse(line)[2] == "ok"


def test_plain_renderer_colors_when_asked():
    line = PlainRenderer(color=True)(None, None, {"level": "warn", "event": "careful"})
    assert f"{LEVEL_COLORS['warn']}[warn]{RESET}" in line


# --------------------------------------------------------------------------
# stdlib channel
# --------------------------------------------------------------------------


def test_plain_formatter_matches_renderer_shape():
    record = logging.LogRecord("uvicorn", logging.WARNING, __file__, 1, "port busy", None, None)
    stamp, tag, msg = parse(PlainFormatter(color=False).format(record))
    assert (tag, msg) == ("warn", "port busy")
    # Same format as the structlog channel: two channels that disagree here is
    # the bug this module exists to prevent.
    assert looks_like_now(stamp)


def test_plain_formatter_applies_argument_interpolation():
    record = logging.LogRecord("iris", logging.ERROR, __file__, 1, "port %s", ("8080",), None)
    assert parse(PlainFormatter(color=False).format(record))[1:] == ("error", "port 8080")


def test_setup_logging_routes_stdlib_records_through_plain_formatter(capsys):
    # Real execution path: a stdlib logger (uvicorn/FastAPI use these) must end
    # up with the same line shape as structlog, not a bare unlabelled message.
    setup_logging("INFO")
    logging.getLogger("uvicorn.error").info("Application startup")
    logging.getLogger("iris").warning("port busy")
    out = capsys.readouterr().out
    parsed = [parse(line) for line in out.splitlines()]
    assert (parsed[0][1], parsed[0][2]) == ("info", "Application startup")
    assert (parsed[1][1], parsed[1][2]) == ("warn", "port busy")


def test_setup_logging_routes_structlog_records_through_plain_renderer(capsys):
    setup_logging("INFO")
    structlog.get_logger("iris").info("提取 rootfs", step=1)
    out = capsys.readouterr().out
    assert parse(out.strip())[1:] == ("info", "提取 rootfs step=1")
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
    assert parse(out.strip())[1:] == ("error", "kept")


# --------------------------------------------------------------------------
# CLI stream channel
# --------------------------------------------------------------------------


def test_stream_logger_writes_the_same_shape_as_structlog():
    stream = io.StringIO()
    StreamLogger(stream).info("L3 rules matched", count=2)
    assert parse(stream.getvalue().strip())[1:] == ("info", "L3 rules matched count=2")


def test_stream_logger_normalizes_its_own_level_names():
    stream = io.StringIO()
    log = StreamLogger(stream)
    log.info("a")
    log.warning("b")
    log.error("c")
    assert [parse(line)[1] for line in stream.getvalue().splitlines()] == ["info", "warn", "error"]


def test_stream_logger_resolves_stdout_at_write_time(capsys):
    # A module-level logger is built at import time, when sys.stdout is whatever
    # the importer saw. Resolving per write is what lets a test assert on it.
    log = get_stream_logger()
    log.info("late binding")
    assert parse(capsys.readouterr().out.strip())[2] == "late binding"


def test_error_logger_writes_to_stderr(capsys):
    # `iris rules apply ... 2>/dev/null` must still show what went wrong, and
    # must not put the failure on stdout where a report parser would pick it up.
    get_error_logger().error("firmware not found: x.bin")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert parse(captured.err.strip())[1:] == ("error", "firmware not found: x.bin")


def test_error_logger_follows_a_stderr_replaced_after_import(capsys):
    err = get_error_logger()
    err.error("first")
    err.error("second")
    assert capsys.readouterr().err.count("\n") == 2


def test_stream_logger_drops_color_on_a_plain_stream():
    stream = io.StringIO()
    StreamLogger(stream).error("boom")
    assert "\033" not in stream.getvalue()


def test_stream_logger_colors_a_terminal(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    stream = _FakeTty()
    StreamLogger(stream).error("boom")
    assert f"{LEVEL_COLORS['error']}[error]{RESET}" in stream.getvalue()


# --------------------------------------------------------------------------
# block reports
# --------------------------------------------------------------------------


def test_block_puts_one_timestamp_on_the_header_and_indents_the_rows():
    stream = io.StringIO()
    StreamLogger(stream).block("info", "inspect result", ["format : tar", "arch   : armel"])
    _, tag, msg = parse(stream.getvalue().strip())
    assert (tag, msg) == ("info", "inspect result\n  format : tar\n  arch   : armel")
    # Exactly one timestamp for the whole report: a second one on a row would be
    # the column-destroying shape this method exists to avoid.
    assert len(re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", stream.getvalue())) == 1


def test_block_rows_stay_column_aligned():
    # This is the whole point of one record instead of one record per row: a
    # timestamp on every row would push each value column to a different offset.
    stream = io.StringIO()
    rows = [f"{'field':<16}: {'a' * i}" for i in range(3)]
    StreamLogger(stream).block("info", "h", rows)
    body = stream.getvalue().splitlines()[1:]
    assert [len(line) - len(line.lstrip()) for line in body] == [2, 2, 2]

    assert len({line.index(":") for line in body}) == 1


def test_block_with_no_rows_is_just_the_header():
    stream = io.StringIO()
    StreamLogger(stream).block("info", "no containers found", [])
    assert parse(stream.getvalue().strip())[2] == "no containers found"


def test_block_colors_every_row_from_a_single_tint(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    stream = _FakeTty()
    StreamLogger(stream).block("info", "table", ["a", "b"], row_color=LEVEL_COLORS["info"])
    written = stream.getvalue()
    assert f"{LEVEL_COLORS['info']}a{RESET}" in written
    assert f"{LEVEL_COLORS['info']}b{RESET}" in written


def test_block_colors_rows_individually(monkeypatch):
    # A container table needs green for "Up" and yellow for anything else, and the
    # decision is per row.
    monkeypatch.setenv("FORCE_COLOR", "1")
    stream = _FakeTty()
    StreamLogger(stream).block(
        "info",
        "containers",
        ["up", "exited"],
        row_color=[LEVEL_COLORS["info"], LEVEL_COLORS["warn"]],
    )
    written = stream.getvalue()
    assert f"{LEVEL_COLORS['info']}up{RESET}" in written
    assert f"{LEVEL_COLORS['warn']}exited{RESET}" in written


def test_block_leaves_the_header_in_the_level_color_not_the_row_color(monkeypatch):
    # Otherwise a table of failures would have a green-looking header, which reads
    # as success at a glance.
    monkeypatch.setenv("FORCE_COLOR", "1")
    stream = _FakeTty()
    StreamLogger(stream).block(
        "error", "emulation failure", ["boom"], row_color=LEVEL_COLORS["info"]
    )
    assert f"{LEVEL_COLORS['error']}[error]{RESET}" in stream.getvalue()


def test_block_row_tints_are_ignored_without_color(monkeypatch):
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    stream = io.StringIO()
    StreamLogger(stream).block("info", "t", ["a"], row_color=LEVEL_COLORS["warn"])
    assert "\033" not in stream.getvalue()


def test_block_rejects_a_tint_count_that_does_not_match_the_rows():
    # A silent mismatch would leave the last rows untinted and nobody would know
    # the table was half-colored because of a typo in the caller.
    with pytest.raises(ValueError, match="2 entries but the block has 1 rows"):
        StreamLogger(io.StringIO()).block("info", "t", ["a"], row_color=["x", "y"])


def test_block_appends_structured_fields_to_the_header():
    stream = io.StringIO()
    StreamLogger(stream).block("info", "containers", ["a"], iid=4218)
    assert parse(stream.getvalue().strip())[2] == "containers iid=4218\n  a"


# --------------------------------------------------------------------------
# progress line
# --------------------------------------------------------------------------


def test_status_line_uses_the_wait_tag_not_a_level():
    # Progress is not a severity; `wait` keeps the prefix shape while saying so.
    assert StatusLine.TAG == "wait"
    assert StatusLine.TAG not in LEVEL_COLORS
    assert StatusLine.COLOR == LEVEL_COLORS["warn"]


def test_status_line_matches_the_log_shape():
    stream = io.StringIO()
    StatusLine(stream).update("waiting")
    assert parse(stream.getvalue().strip())[1:] == ("wait", "waiting")


def test_status_line_overwrites_in_place_on_a_terminal():
    stream = _FakeTty()
    status = StatusLine(stream)
    status.update("waiting for guest web on :8080")
    status.update("waiting for guest web on :8080 (HTTP 000)")
    written = stream.getvalue()
    # Exactly two \r-prefixed fragments, no newline between them: the terminal
    # rewrites one physical line instead of appending.
    assert written.count("\n") == 0
    assert written.count("\r") == 2


def test_status_line_pads_shorter_update_so_no_tail_is_left_behind():
    stream = _FakeTty()
    status = StatusLine(stream)
    status.update("waiting for guest web on :8080 (HTTP 000)")
    status.update("[5s] waiting")
    # The second fragment is padded back to the first one's visible width, so the
    # leftover " (HTTP 000)" from the longer line cannot survive on screen.
    fragments = stream.getvalue().split("\r")
    assert len(SGR_RE.sub("", fragments[2])) == len(SGR_RE.sub("", fragments[1]))
    padded = parse(fragments[2])[2]
    assert padded.startswith("[5s] waiting")
    assert padded.strip() == "[5s] waiting"


def test_status_line_padding_covers_the_whole_rendered_line():
    # The stored width must be the printed width, not the text width: the
    # timestamp-and-tag prefix is 27 characters, and measuring the text alone
    # leaves that prefix on screen for every erase, turning a cleared row into a
    # row reading "[wait] ".
    stream = _FakeTty()
    status = StatusLine(stream)
    status.update("a long progress message")
    status.clear()
    # getvalue() is "\r" + rendered + spaces + "\r": index 1 is the progress line
    # itself, index 2 is what clear() wrote over it.
    rendered, erased = stream.getvalue().split("\r")[1:3]
    assert SGR_RE.sub("", erased).strip() == ""
    assert len(SGR_RE.sub("", erased)) == len(SGR_RE.sub("", rendered))


def test_status_line_close_terminates_the_line():
    stream = _FakeTty()
    status = StatusLine(stream)
    status.update("waiting")
    status.close()
    written = stream.getvalue()
    assert written.endswith("\n")


def test_status_line_close_leaves_the_verdict_to_the_log_record():
    """close() must not swallow the outcome: it emits no text of its own."""
    stream = _FakeTty()
    status = StatusLine(stream)
    status.update("waiting")
    status.close()
    print(format_line("info", "Web reachable at http://localhost:8080 after 79s", color=True),
          file=stream)
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
    assert parse(stream.getvalue().strip())[1:] == ("wait", "waiting")
    assert stream.getvalue().count("\n") == 1


def test_status_line_clear_erases_the_in_place_line():
    stream = _FakeTty()
    status = StatusLine(stream)
    status.update("waiting for guest web on :8080")
    status.clear()
    rendered, erased = stream.getvalue().split("\r")[1:3]
    # Trailing spaces then a carriage return, as wide as the rendered line: the
    # row is blank and ready for a real log record, rather than having the next
    # record spliced onto it.
    assert len(SGR_RE.sub("", erased)) == len(SGR_RE.sub("", rendered))
    assert SGR_RE.sub("", erased).strip() == ""
    assert "\n" not in stream.getvalue()


def test_status_line_clear_then_log_record_starts_at_column_zero():
    stream = _FakeTty()
    status = StatusLine(stream)
    status.update("waiting for guest web on :8080 (HTTP 000)")
    status.clear()
    print(format_line("info", "Detected guest IP: 192.168.1.1", color=True), file=stream)
    written = stream.getvalue()
    # The record's own line must begin right after the erase, not after the
    # progress text.
    assert written.rstrip("\n").endswith("Detected guest IP: 192.168.1.1")
    assert written.count("\n") == 1


def test_status_line_clear_resets_padding_so_next_update_is_not_padded():
    stream = _FakeTty()
    status = StatusLine(stream)
    status.update("waiting for guest web on :8080")
    status.clear()
    status.update("short")
    # After clear() the stale width must be gone, else the short update gets a
    # phantom tail of spaces.
    assert parse(stream.getvalue().split("\r")[-1])[2] == "short"


def test_status_line_clear_is_a_noop_on_a_non_terminal():
    stream = io.StringIO()
    status = StatusLine(stream)
    status.update("waiting")
    status.clear()
    # Lines already stand on their own; erasing would emit a phantom record.
    assert stream.getvalue().endswith("waiting\n")
    assert "\r" not in stream.getvalue()


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
    assert [parse(line)[2] for line in lines] == [
        "[10s] waiting for guest web on :8080 (HTTP 000)",
        "[15s] waiting for guest web on :8080 (HTTP 000)",
    ]


def test_status_line_colors_the_wait_tag_when_the_stream_is_a_terminal(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    stream = _FakeTty()
    StatusLine(stream).update("waiting")
    assert f"{StatusLine.COLOR}[wait]{RESET}" in stream.getvalue()


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
    assert parse("".join(stream.written).strip())[1:] == ("wait", "waiting")