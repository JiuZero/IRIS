"""Tests for the root debug entrypoint ``iris.py``.

The file deliberately shares its name with the ``src/iris`` package, which
makes it a loaded gun: whenever the repository root precedes ``src`` on
``sys.path`` — ``python -m iris.cli``, ``python -c`` from the root, pytest's
prepend import mode — ``import iris`` resolves to the debug script instead of
the package. These tests pin the two guarantees that keep that harmless:

* running it as a script forwards to the CLI, and
* importing it as ``iris`` leaves the real submodules reachable.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRY = REPO_ROOT / "iris.py"
SRC = REPO_ROOT / "src"

#: Every log line starts with the wall clock. Two processes cannot produce the
#: same one, so comparing their output byte for byte would fail on any run that
#: straddles a second — which says nothing about whether the entry points agree.
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} ", re.MULTILINE)


def _run(*argv: str) -> subprocess.CompletedProcess:
    # Explicit UTF-8: the CLI renders box-drawing characters that a GBK locale
    # (the Windows default) cannot decode, and the decode happens on this side.
    return subprocess.run(
        [sys.executable, *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def _without_timestamps(text: str) -> str:
    return TIMESTAMP_RE.sub("", text)


class TestRunsAsScript:
    def test_help_lists_every_command_group(self):
        proc = _run(str(ENTRY), "--help")
        assert proc.returncode == 0, proc.stderr
        for group in ("db", "extract", "corpus", "emulate", "rules", "serve"):
            assert group in proc.stdout

    def test_matches_the_module_entrypoint(self):
        """`python iris.py X` and `python -m iris.cli X` must behave identically."""
        script = _run(str(ENTRY), "rules", "list")
        module = _run("-m", "iris.cli", "rules", "list")
        assert script.returncode == module.returncode == 0, module.stderr
        assert _without_timestamps(script.stdout) == _without_timestamps(module.stdout)
        # The timestamps are the only thing allowed to differ, and they are the
        # part that proves each process really rendered its own log line.
        assert TIMESTAMP_RE.search(script.stdout)
        assert TIMESTAMP_RE.search(module.stdout)


class TestImpersonatesThePackage:
    """cwd lands on sys.path[0] for `python -c`, so `iris` resolves to this file."""

    def test_submodules_still_resolve_to_the_real_package(self):
        proc = _run("-c", "import iris, iris.cli; print(iris.cli.__file__)")
        assert proc.returncode == 0, proc.stderr
        assert Path(proc.stdout.strip()) == SRC / "iris" / "cli.py"

    def test_package_attributes_come_from_the_real_init(self):
        proc = _run("-c", "import iris; print(iris.__version__)")
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip()

    @pytest.mark.parametrize("argv", [("-m", "iris.cli", "--help"), ("-m", "iris.cli", "rules", "list")])
    def test_dash_m_entrypoint_is_not_hijacked(self, argv):
        proc = _run(*argv)
        assert proc.returncode == 0, proc.stderr


class TestSrcIsFirstOnSysPath:
    def test_src_is_not_appended_behind_the_repo_root(self):
        """Editable installs already put src on sys.path; appending would be too late."""
        proc = _run(
            "-c",
            "import pathlib, runpy, sys;"
            "runpy.run_path('iris.py', run_name='probe');"
            "print(pathlib.Path(sys.path[0]).resolve() == pathlib.Path('src').resolve())",
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "True"
