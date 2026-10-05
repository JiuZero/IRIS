"""One output shape for the whole tool: ``2026-10-02T02:11:00 [info] message``.

Two channels feed the terminal and they used to disagree. ``structlog`` rendered
through ``ConsoleRenderer``, which pads the level name to a fixed width
(``[info     ]``) and colors it — the padding is a column layout meant for a
table, and on a non-color terminal it is just a run of trailing spaces. Every
other library in the process (uvicorn, FastAPI, the ``logging`` root logger)
printed the bare message with no level at all. So a single run mixed ``[info   ]``
lines with unlabelled ones, and nothing carried a time at all — for a tool whose
runs take two minutes and print every step, "which of these two lines came first"
was unanswerable after the fact.

Every line now carries an ISO-like local timestamp, a level tag normalized to a
closed vocabulary, and a color when the destination can show one. Both channels
(``structlog`` and stdlib ``logging``) render through the same ``format_line``,
so the two shapes cannot drift apart again.

Color follows the usual terminal conventions — dim timestamp, cyan ``debug``,
green ``info``, yellow ``warn``, red ``error`` — and is dropped entirely when the
stream is not a terminal, when ``NO_COLOR`` is set, or when ``TERM=dumb``. A
redirected log therefore stays free of escape sequences.

``StatusLine`` covers the third kind of output: a single line that is rewritten in
place while a long operation is in flight, instead of appending a new line every
few seconds. It degrades to plain line output when stdout is not a terminal, so
a redirected log never ends up full of carriage returns.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import traceback
from collections.abc import Sequence
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO

import structlog

#: Log level -> the tag shown in brackets. Closed set on purpose: ``structlog``
#: calls it ``warning`` and ``logging`` calls it ``WARNING``, and neither spelling
#: is what a reader scanning a wall of boot output would guess.
LEVEL_TAGS = {
    "debug": "debug",
    "info": "info",
    "warning": "warn",
    "warn": "warn",
    "error": "error",
    "exception": "error",
    "critical": "error",
    "success": "info",
}

#: Tag -> SGR code. Kept beside LEVEL_TAGS so a new level cannot be added to the
#: vocabulary without also being given a color.
LEVEL_COLORS = {
    "debug": "\033[36m",  # cyan
    "info": "\033[32m",   # green
    "warn": "\033[33m",   # yellow
    "error": "\033[31m",  # red
}

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"

_DIM = "\033[2m"
_RESET = "\033[0m"


def level_tag(level: str) -> str:
    """Normalized bracketed tag for a level name, e.g. ``warning`` -> ``warn``."""
    return LEVEL_TAGS.get(str(level).lower(), str(level).lower())


def use_color(stream: TextIO | None) -> bool:
    """Whether *stream* can show SGR sequences.

    ``NO_COLOR`` is checked first so a user can force plain output from a real
    terminal; ``FORCE_COLOR`` does the opposite for a pipe that should still be
    colored. A non-tty stream is the default answer because a log file full of
    escape bytes is worse than a colorless one.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


#: SGR sequences occupy bytes in a buffer but no cells on a screen. Anything that
#: has to line up with what a terminal shows has to measure without them.
_SGR_RE = re.compile(r"\033\[[0-9;]*m")


def _visible_width(text: str) -> int:
    """How many terminal columns *text* occupies once SGR sequences are ignored."""
    return len(_SGR_RE.sub("", text))


def _stamp() -> str:
    """The current local wall clock, e.g. ``2026-10-02T02:11:00``.

    Local, not UTC, and stated as a wall clock with no offset: an operator reading
    a boot log wants to know whether "02:11" is the minute the guest died, and
    that is the clock on their wall. ``astimezone()`` attaches the zone so the
    value is unambiguous internally, while the rendering stays a bare local time
    because that is the shape every other line of a terminal log uses.
    """
    return datetime.now().astimezone().strftime(TIMESTAMP_FORMAT)


def format_line(tag: str, message: str, color: bool) -> str:
    """Render one output line: the single shape both logging channels share.

    An unknown tag gets no color rather than a fallback one: a level outside
    :data:`LEVEL_COLORS` is a level nobody designed a color for, and guessing one
    would make it look like a severity it does not have.
    """
    stamp = _stamp()
    tint = LEVEL_COLORS.get(tag, "") if color else ""
    if not tint:
        # A tag with no color of its own also gets no reset: emitting RESET alone
        # would leave a stray escape byte in a stream that is otherwise plain,
        # which is exactly what "this line is untinted on purpose" must not do.
        return f"{stamp} [{tag}] {message}"
    return f"{_DIM}{stamp}{_RESET} {tint}[{tag}]{_RESET} {message}"


class PlainFormatter(logging.Formatter):
    """Format stdlib records the same way structlog ones are rendered."""

    def __init__(self, stream: TextIO | None = None, color: bool | None = None) -> None:
        super().__init__()
        self._stream = stream
        self._color = color

    def format(self, record: logging.LogRecord) -> str:
        color = use_color(self._stream or sys.stdout) if self._color is None else self._color
        return format_line(level_tag(record.levelname), record.getMessage(), color)


class PlainRenderer:
    """``structlog`` processor emitting ``timestamp [level] event`` with no padding.

    Replaces ``ConsoleRenderer``, whose column-aligned output was the reason the
    level tag appeared as ``[info     ]``. Exceptions are kept as a trailing
    traceback rather than being folded into the message, because a boot log is
    read by scrolling, not by parsing.

    ``sink`` receives a second copy of every rendered line. structlog writes through
    its own logger factory straight to stdout, bypassing the stdlib handlers a
    ``FileHandler`` is attached to -- so without this a log file would hold uvicorn's
    traffic and none of IRIS's own reasoning, which is the half worth keeping.
    Writing the copy here rather than re-routing structlog through ``logging`` is
    what keeps the output shape identical to the console's: re-routing would put
    ``record.getMessage()`` in charge of the line and drop every extra field.
    """

    def __init__(self, stream: TextIO | None = None, color: bool | None = None,
                 sink: TextIO | None = None) -> None:
        self._stream = stream
        self._color = color
        self._sink = sink

    def __call__(self, _logger, _name, event_dict) -> str:
        color = use_color(self._stream or sys.stdout) if self._color is None else self._color
        tag = level_tag(event_dict.pop("level", "info"))
        event = event_dict.pop("event", "")
        exc_info = event_dict.pop("exc_info", None)
        # StackInfoRenderer() records a rendered traceback under this key; it is
        # already text, so it is appended rather than re-formatted.
        stack = event_dict.pop("stack_info", None)
        message = _join(event, event_dict)
        traceback_text = stack or self._format_exc(exc_info)
        if self._sink is not None:
            # Rendered twice on purpose: the file copy has to be free of escape
            # sequences whatever the console decides, and there is no way to strip
            # them afterwards without also stripping the text a firmware happens
            # to have printed in them.
            filed = format_line(tag, message, False)
            if traceback_text:
                filed = f"{filed}\n{traceback_text.rstrip()}"
            print(filed, file=self._sink, flush=True)
        line = format_line(tag, message, color)
        if traceback_text:
            line = f"{line}\n{traceback_text.rstrip()}"
        return line

    @staticmethod
    def _format_exc(exc_info) -> str:
        if exc_info is True:
            exc_info = sys.exc_info()
        if exc_info is None:
            return ""
        if isinstance(exc_info, BaseException):
            return "".join(
                traceback.format_exception(type(exc_info), exc_info, exc_info.__traceback__)
            )
        return "".join(traceback.format_exception(*exc_info))


def _join(event: str, event_dict: dict) -> str:
    """Append remaining key/value context to the message, or use it alone."""
    extras = " ".join(f"{k}={v}" for k, v in event_dict.items())
    if not extras:
        return event
    return f"{event} {extras}".rstrip()


class StreamLogger:
    """A structlog-shaped logger pinned to one stream.

    ``structlog`` renders into the global processor chain, which
    :func:`setup_logging` points at stdout. A CLI also needs two things structlog
    cannot give it: failures on stderr (``iris rules apply ... 2>/dev/null`` must
    not print them) and multi-line aligned reports (``inspect`` prints a column of
    ``label : value`` pairs, and a timestamp on every row would push each one right
    by a different amount and destroy the alignment). This class covers both over
    the same :func:`format_line`, so a message looks identical on either stream.

    The stream is resolved on every write rather than captured in ``__init__``.
    A module-level ``log = get_logger()`` is otherwise bound to whatever
    ``sys.stdout`` happened to be at import time, which in a test runner is a
    capture buffer the assertions cannot see.
    """

    #: Continuation lines of a block are indented under the header so a reader
    #: (and a grep for the header) still sees them as one record.
    INDENT = "  "

    def __init__(self, stream: TextIO | None = None, *, use_stderr: bool = False) -> None:
        self._stream = stream
        self._use_stderr = use_stderr

    @property
    def stream(self) -> TextIO:
        if self._stream is not None:
            return self._stream
        return sys.stderr if self._use_stderr else sys.stdout

    def info(self, event: str, **fields: object) -> None:
        self._emit("info", event, fields)

    def warning(self, event: str, **fields: object) -> None:
        self._emit("warn", event, fields)

    def error(self, event: str, **fields: object) -> None:
        self._emit("error", event, fields)

    def block(
        self,
        level: str,
        header: str,
        rows: Sequence[str],
        row_color: str | Sequence[str | None] | None = None,
        **fields: object,
    ) -> None:
        """One record made of a logged header plus indented, column-aligned rows.

        Prefixing every row with a timestamp would shift each row right by a
        different amount — the tag is a fixed width but the value is not — and
        the columns would no longer line up. Emitting a single record instead
        keeps one timestamp for the whole report and leaves the rows free to
        align against each other.

        ``row_color`` tints rows without tinting the header, which is what a
        table needs: the header states the level, the rows state a per-row
        verdict. Pass one SGR code to tint them all, or one per row to let each
        row carry its own.

        A tint count that does not match the row count raises: a silent mismatch
        would leave trailing rows untinted, and nobody would know the table was
        half-colored because of a typo at the call site. The check happens even
        when color is off, so a redirected run and a terminal run cannot disagree
        about whether the caller got its row counts right.
        """
        tints = self._row_tints(row_color, len(rows))
        body = [
            f"{self.INDENT}{tint}{row}{_RESET if tint else ''}"
            for row, tint in zip(rows, tints, strict=True)
        ]
        self._emit(level, _join("\n".join([_join(header, fields), *body]), {}), {})

    def _row_tints(self, row_color: str | Sequence[str | None] | None, count: int) -> list[str]:
        if row_color is None:
            return [""] * count
        if isinstance(row_color, str):
            tints: list[str | None] = [row_color] * count
        else:
            tints = list(row_color)
            if len(tints) != count:
                raise ValueError(f"row_color has {len(tints)} entries but the block has {count} rows")
        return ["" for _ in tints] if not self._color else tints

    @property
    def _color(self) -> bool:
        return use_color(self.stream)

    def _emit(self, level: str, event: str, fields: dict[str, object]) -> None:
        stream = self.stream
        line = format_line(level_tag(level), _join(event, fields), use_color(stream))
        print(line, file=stream, flush=True)


#: How much one log file may grow before it is rotated, and how many of the previous
#: ones are kept. A web service left running for a week would otherwise fill the disk
#: under ``iris_home``, and a tool that stops logging is worse than one that grows.
_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_BACKUPS = 3


def uvicorn_log_config() -> dict:
    """A ``dictConfig`` that makes uvicorn log in IRIS's shape, not its own.

    uvicorn ships ``INFO:     127.0.0.1:52344 - "GET / HTTP/1.1" 200 OK`` while
    everything IRIS emits is ``2026-10-02T02:11:00 [info] message``, and its access
    log arrives with no timestamp at all. For a service whose runs take two minutes
    and whose whole value is being able to read afterwards which step came first,
    that is a log that cannot answer the only question asked of it.

    Passed as ``log_config`` so uvicorn does not overwrite the root handlers IRIS
    installed. Its own ``LOGGING_CONFIG`` leaves ``disable_existing_loggers`` at
    False and never mentions ``root``, which is what makes this safe: the dict here
    is an addition, not a replacement.
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"iris": {"()": PlainFormatter}},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "iris",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["console"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["console"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["console"], "level": "INFO", "propagate": False},
        },
    }


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Route every channel in the process through one shape, optionally to a file.

    ``log_file`` is opt-in and defaults to nothing: the terminal is what a person
    launching a command expects to see, and writing a file nobody asked for is the
    kind of surprise that ends up filling a disk. What was missing before is not the
    capability but the discoverability -- ``iris web`` was the one long-running
    command whose output existed only in the terminal it was started from, and its
    own access log is the record you want after a failed launch.

    A file that cannot be opened does not stop the tool: a missing ``iris_home``
    must not be the reason a firmware does not get emulated, so the failure is
    reported on stderr and the console handler stays in place.
    """
    # force=True because the CLI may be re-entered in one process (tests, the
    # debug entry point); basicConfig is a no-op the second time otherwise and
    # the level would silently stay at whatever the first run chose.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )
    root = logging.getLogger()
    root.handlers[0].setFormatter(PlainFormatter(stream=sys.stdout))

    handler = (_file_handler(Path(log_file), level=level)
               if log_file is not None else None)
    if handler is not None:
        root.addHandler(handler)

    # Two sinks, not one: the stdlib records go through the root handlers above,
    # structlog's go through the renderer, and a file that only received half of
    # the lines would be worse than none because it would look complete.
    # cache_logger_on_first_use is off whenever a file is attached, because a cached
    # logger keeps the renderer it was built with -- including the old file.
    structlog.configure(
        processors=_structlog_processors(stream=sys.stdout,
                                         sink=handler.stream if handler else None),
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=handler is None,
    )


def _structlog_processors(stream: TextIO, sink: TextIO | None = None) -> list:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        PlainRenderer(stream=stream, sink=sink),
    ]


def _file_handler(log_file: Path, *, level: str) -> logging.Handler | None:
    """A rotating file handler, or None when the file cannot be opened.

    Never color: a log file read with ``less -R`` or grepped for a marker has to be
    free of escape sequences, and ``use_color`` would say yes for a plain file only
    by accident.
    """
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_file, maxBytes=_LOG_MAX_BYTES,
                                      backupCount=_LOG_BACKUPS, encoding="utf-8")
    except OSError as err:
        print(f"cannot write the log file {log_file}: {err}", file=sys.stderr)
        return None
    handler.setFormatter(PlainFormatter(color=False))
    handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    return handler


def get_logger(name: str):
    return structlog.get_logger(name)


def get_stream_logger(stream: TextIO | None = None, *, use_stderr: bool = False) -> StreamLogger:
    """A logger for CLI output, pinned to *stream* or resolved per write."""
    return StreamLogger(stream, use_stderr=use_stderr)


def get_error_logger() -> StreamLogger:
    """A logger pinned to stderr, for messages that must survive ``2>/dev/null``."""
    return StreamLogger(use_stderr=True)


class StatusLine:
    """A progress line rewritten in place, or appended when stdout is a file.

    A 120-second boot poll used to append one ``[16s] waiting...`` line every five
    seconds, burying the dozen lines that actually explained the run. Rewriting
    one line keeps the transcript readable; when the output is not a terminal the
    same calls fall back to ordinary lines, because a log file full of ``\\r``
    is unreadable and ungreppable.

    The rendered line is the same shape as every other one, tag ``[wait]`` — so
    grepping a redirected log for a level still finds the progress updates. The
    ``\\r`` only ever prefixes a full line, never wraps one, so a terminal's own
    line wrapping cannot desynchronize the erase.
    """

    #: Progress is not a log level; it gets its own tag in the same vocabulary
    #: position so the timestamp/tag prefix stays uniform.
    TAG = "wait"
    #: ``wait`` is neither an error nor a success; yellow is what reads as
    #: "in progress" without claiming a severity the state does not have.
    COLOR = LEVEL_COLORS["warn"]

    def __init__(self, stream: TextIO = sys.stdout) -> None:
        self._stream = stream
        self._width = 0
        self._open = False
        self._color = use_color(stream)

    def update(self, text: str) -> None:
        line = self._render(text)
        if not self._is_tty():
            print(line, file=self._stream, flush=True)
            return
        # Pad to the previous width: a shorter line would otherwise leave the
        # tail of the longer one behind. Both measurements are *visible* widths.
        # A terminal erases cells, not bytes: the timestamp-and-tag prefix is 27
        # columns and the SGR sequences around it are zero, so measuring the raw
        # string would leave the prefix on screen after every clear() and pad
        # every short update with 17 columns of nothing. Visible width also keeps
        # the erase identical whether or not color is on, so a run does not erase
        # a different amount of screen depending on the terminal it ran under.
        visible = _visible_width(line)
        padding = " " * max(0, self._width - visible)
        print(f"\r{line}{padding}", end="", file=self._stream, flush=True)
        self._width = visible
        self._open = True

    def _render(self, text: str) -> str:
        if not self._color:
            return f"{_stamp()} [{self.TAG}] {text}"
        return f"{_DIM}{_stamp()}{_RESET} {self.COLOR}[{self.TAG}]{_RESET} {text}"

    def clear(self) -> None:
        """Abandon an in-place line so a real log record can be printed.

        A progress line lives in the middle of a terminal row with no newline
        after it. Printing a structured record on top of that row splices the two
        together, and any leftover tail of the progress text stays on screen. The
        caller signals "I am about to log for real" by clearing first.
        """
        if self._open and self._is_tty():
            print(f"\r{' ' * self._width}\r", end="", file=self._stream, flush=True)
        self._width = 0
        self._open = False

    def close(self) -> None:
        """End the in-place line and move on; it emits nothing of its own.

        The verdict belongs to a real log record, not to the progress line: a
        progress line is transient state, and a run's outcome has to survive being
        redirected to a file. Callers that want to say something log it after this.
        """
        if self._open and self._is_tty():
            print(file=self._stream, flush=True)
        self._width = 0
        self._open = False

    def _is_tty(self) -> bool:
        return bool(getattr(self._stream, "isatty", lambda: False)())