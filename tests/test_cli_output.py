"""Every line the CLI writes goes through the shared log shape, on the right stream.

Two regressions are guarded here:

* ``iris emulate run`` printed ``extracting rootfs from ...`` and
  ``L3 rules matched: ...`` through ``typer.echo`` while the lines around them
  went through the logger, so a single run mixed three formats on one screen;
* failures used to reach stdout as well as stderr, so ``iris ... 2>/dev/null``
  swallowed them and a redirected report could not be parsed.

The commands are run as real subprocesses rather than through a Click runner.
That is the only way to get stdout and stderr back as two separate streams
without depending on the runner's capture flags, and it exercises the same entry
point (``python -m iris.cli``) an operator types, including ``main()``'s
``setup_logging`` call.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

#: The one shape every line must have, whatever produced it.
LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} \[[a-z]+\] ",
    re.MULTILINE,
)
SGR_RE = re.compile(r"\033\[[0-9;]*m")

#: Modules that write to the terminal. Typer's own echo is banned outright: it
#: is the third formatting path this module exists to have removed.
OUTPUT_MODULES = ["cli.py", "corpus/manifest.py"]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the CLI the way an operator would, with a predictable environment."""
    env = {
        **os.environ,
        "PYTHONPATH": str(SRC),
        # A subprocess pipe is not a terminal, so color is off by default anyway;
        # pinning it makes that explicit rather than incidental.
        "NO_COLOR": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "iris.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        env=env,
        timeout=120,
        check=False,
    )


def body_lines(stdout: str) -> list[str]:
    """Every non-empty stdout line with the log shape stripped off."""
    return [SGR_RE.sub("", ln) for ln in stdout.splitlines() if ln.strip()]


def log_records(stdout: str) -> list[tuple[str, str]]:
    """(tag, message) per record, with a block's indented rows kept in the message.

    A block is one record printed across several physical lines, so a
    continuation line is folded back into the record above it rather than being
    treated as an unlabelled line of its own.
    """
    records: list[list[str]] = []
    for raw in body_lines(stdout):
        m = re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} \[([a-z]+)\] (.*)$", raw)
        if m:
            records.append([m.group(1), m.group(2)])
        elif records:
            records[-1][1] += "\n" + raw
    return [(tag, msg) for tag, msg in records]


# --------------------------------------------------------------------------
# static guard: no third formatting path
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relative", OUTPUT_MODULES)
def test_no_module_writes_with_typer_echo(relative):
    # typer.echo is unstyled, un-timestamped and stream-ambiguous. Any new use is
    # a line that will look like it belongs to a different tool.
    path = SRC / "iris" / relative
    offenders = [
        f"{i}: {line.strip()}"
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\btyper\.(echo|secho)\b", line)
    ]
    assert not offenders, f"{relative} writes outside the log shape:\n" + "\n".join(offenders)


@pytest.mark.parametrize("relative", OUTPUT_MODULES)
def test_no_module_prints_bare_with_print(relative):
    # A bare print() is the same defect with a different spelling.
    path = SRC / "iris" / relative
    offenders = [
        f"{i}: {line.strip()}"
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.match(r"^\s*print\(", line) and "file=" not in line
    ]
    assert not offenders, f"{relative} uses bare print():\n" + "\n".join(offenders)


# --------------------------------------------------------------------------
# real command output
# --------------------------------------------------------------------------


def test_every_stdout_line_carries_the_log_shape():
    result = run_cli("rules", "list")
    assert result.returncode == 0, result.stderr
    lines = body_lines(result.stdout)
    assert lines, "rules list produced no output"
    # Continuation lines of a block are indented and carry no prefix of their
    # own, so the guard is "the first line of every record is a log line".
    record_starts = [ln for ln in lines if not ln.startswith(" ")]
    assert record_starts, "no record headers found"
    assert all(LINE_RE.match(ln) for ln in record_starts), record_starts


def test_rule_list_is_one_timestamped_block_with_aligned_rows():
    result = run_cli("rules", "list")
    assert result.returncode == 0, result.stderr
    records = log_records(result.stdout)
    assert len(records) == 1, f"expected a single block record, got {records}"
    tag, msg = records[0]
    assert tag == "info"
    body = msg.splitlines()
    assert body[0] == "L3 boot-fix rules"
    assert any("vendor-watchdog-monitor" in ln for ln in body[1:])
    # Every rule row starts at the same offset, which is the alignment the
    # block form exists to preserve.
    assert len({len(ln) - len(ln.lstrip()) for ln in body[1:]}) == 1


def test_corpus_list_is_a_block_not_a_bare_line():
    result = run_cli("corpus", "list")
    assert result.returncode == 0, result.stderr
    records = log_records(result.stdout)
    assert len(records) == 1
    tag, msg = records[0]
    assert tag == "info"
    assert msg.startswith("manifest: ")
    assert len(msg.splitlines()) > 1


def test_emulate_run_progress_lines_go_through_the_logger():
    """The two lines the report named: bare ``typer.echo`` between log lines.

    ``emulate run`` is not exercised end to end here — it needs a container and a
    guest — so this asserts the call site instead: the literal is unreachable
    through any writer but the logger if the line it sits on is an ``out.info``.
    """
    source = (SRC / "iris" / "cli.py").read_text(encoding="utf-8")
    for literal in ("extracting rootfs from", "L3 rules matched"):
        assert literal in source
        line = source[: source.index(literal)].rsplit("\n", 1)[-1]
        assert "out.info(" in line, f"{literal!r} is emitted by {line.strip()!r}"


def test_failures_go_to_stderr_and_not_to_stdout():
    result = run_cli("extract", "inspect", "definitely-not-here.bin")
    assert result.returncode == 1
    assert result.stdout == "", f"failure leaked to stdout: {result.stdout!r}"
    assert "archive not found" in result.stderr
    tag = re.match(r"^\S+ \[([a-z]+)\] ", result.stderr).group(1)
    assert tag == "error"


def test_a_refused_zip_exits_nonzero_with_an_error_record():
    result = run_cli("extract", "rootfs", "iris-home/corpus/DIR-868L_fw_revB_2-05b02_eu_multi_20161117.zip")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "[error] refusing zip container" in result.stderr


def test_rules_apply_emits_bare_json_for_machine_consumers():
    # The single deliberate exception to "everything is a log line": this output
    # is a report meant for `jq`, and a timestamp in front of it would make the
    # command unusable in a pipeline.
    result = run_cli("rules", "apply", "iris-home")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert not LINE_RE.match(result.stdout.strip()), "JSON output must not carry a log prefix"
    assert result.stderr == ""


def test_rules_apply_keeps_failures_off_the_json_stream():
    result = run_cli("rules", "apply", "iris-home")
    assert result.stdout.strip().startswith(("[", "{")), result.stdout[:200]


def test_no_escape_sequences_when_color_is_disabled():
    result = run_cli("rules", "list")
    assert "\033" not in result.stdout
    assert "\033" not in result.stderr