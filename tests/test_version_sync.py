"""The package version lives in two places, so it drifts.

``pyproject.toml`` is what gets installed; ``iris.__version__`` is what scripts
and ``iris --version`` read. They had already drifted to 0.3.5 vs 0.3.3, which
means neither number could be trusted as "the current version" -- and a stale one
is worse than none when someone is trying to work out whether a fix is in their
build. Asserting they match makes the bump a two-line edit instead of a habit.
"""

from __future__ import annotations

import re
from pathlib import Path

import iris

PROJECT = Path(__file__).resolve().parents[1]
PYPROJECT = PROJECT / "pyproject.toml"

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _declared_project_version() -> str:
    match = re.search(r'^version = "([^"]+)"', PYPROJECT.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, "pyproject.toml no longer declares [project] version on its own line"
    return match.group(1)


def test_the_two_declarations_agree():
    assert iris.__version__ == _declared_project_version(), (
        f"iris.__version__={iris.__version__} but pyproject says {_declared_project_version()}"
    )


def test_the_version_is_plain_semver():
    """No local/dev suffixes: the guard above compares strings, so a suffix on one
    side would pass a human check and fail this one forever."""
    assert _SEMVER.match(iris.__version__), iris.__version__


def test_the_changelog_documents_the_current_version():
    changelog = (PROJECT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{iris.__version__}]" in changelog, (
        f"CHANGELOG.md has no section for {iris.__version__}"
    )