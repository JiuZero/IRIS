"""One output shape for the whole tool: ``[info] message``.

Two channels feed the terminal and they used to disagree. ``structlog`` rendered
through ``ConsoleRenderer``, which pads the level name to a fixed width
(``[info     ]``) and colors it — the padding is a column layout meant for a
table, and on a non-color terminal it is just a run of trailing spaces. Every
other library in the process (uvicorn, FastAPI, the ``logging`` root logger)
printed the bare message with no level at all. So a single run mixed ``[info   ]``
lines with unlabelled ones.

The level is now rendered as a fixed ``[info]`` / ``[warn]`` / ``[error]`` tag by
both channels, so a line can be identified by eye and grepped by pattern
regardless of which library emitted it. ``structlog`` also spells the level
``warning``; it is normalized to ``warn`` so the vocabulary is closed.

``status()`` covers the third kind of output: a single line that is rewritten in
place while a long operation is in flight, instead of appending a new line every
few seconds. It degrades to plain line output when stdout is not a terminal, so
a redirected log never ends up full of carriage returns.
"""

from __future__ import annotations

import logging
import sys
import traceback
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


def level_tag(level: str) -> str:
    """Normalized bracketed tag for a level name, e.g. ``warning`` -> ``warn``."""
    return LEVEL_TAGS.get(str(level).lower(), str(level).lower())


class PlainFormatter(logging.Formatter):
    """Format stdlib records the same way structlog ones are rendered."""

    def format(self, record: logging.LogRecord) -> str:
        return f"[{level_tag(record.levelname)}] {record.getMessage()}"


class PlainRenderer:
    """``structlog`` processor emitting ``[level] event`` with no padding.

    Replaces ``ConsoleRenderer``, whose column-aligned output was the reason the
    level tag appeared as ``[info     ]``. Exceptions are kept as a trailing
    traceback rather than being folded into the message, because a boot log is
    read by scrolling, not by parsing.
    """

    def __call__(self, _logger, _name, event_dict) -> str:
        level = level_tag(event_dict.pop("level", "info"))
        event = event_dict.pop("event", "")
        exc_info = event_dict.pop("exc_info", None)
        # StackInfoRenderer() records a rendered traceback under this key; it is
        # already text, so it is appended rather than re-formatted.
        stack = event_dict.pop("stack_info", None)
        extras = " ".join(f"{k}={v}" for k, v in event_dict.items())
        line = f"[{level}] {event}"
        if extras:
            line = f"{line} {extras}"
        traceback_text = stack or self._format_exc(exc_info)
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


def setup_logging(level: str = "INFO") -> None:
    # force=True because the CLI may be re-entered in one process (tests, the
    # debug entry point); basicConfig is a no-op the second time otherwise and
    # the level would silently stay at whatever the first run chose.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )
    logging.getLogger().handlers[0].setFormatter(PlainFormatter())

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            PlainRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str):
    return structlog.get_logger(name)


class StatusLine:
    """A progress line rewritten in place, or appended when stdout is a file.

    A 120-second boot poll used to append one ``[16s] waiting...`` line every five
    seconds, burying the dozen lines that actually explained the run. Rewriting
    one line keeps the transcript readable; when the output is not a terminal the
    same calls fall back to ordinary lines, because a log file full of ``\\r``
    is unreadable and ungreppable.
    """

    def __init__(self, stream: TextIO = sys.stdout) -> None:
        self._stream = stream
        self._width = 0
        self._open = False

    def update(self, text: str) -> None:
        if not self._is_tty():
            print(f"[wait] {text}", file=self._stream, flush=True)
            return
        # Pad to the previous width: a shorter line would otherwise leave the
        # tail of the longer one behind.
        padding = " " * max(0, self._width - len(text))
        print(f"\r{text}{padding}", end="", file=self._stream, flush=True)
        self._width = len(text)
        self._open = True

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