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

def test_the_api_advertises_the_package_version():
    """A third copy of the version used to live in the FastAPI app.

    It read ``0.1.0`` for every release from 0.1.x onwards, so ``/api/v1/health``
    and the OpenAPI schema told clients they were talking to something a year out
    of date -- which is the one number a client pins its compatibility checks to.
    """
    from iris.api.server import HealthResponse, app

    assert app.version == iris.__version__
    assert HealthResponse(status="ok", version=iris.__version__).version == iris.__version__


def test_no_module_hardcodes_a_version_string():
    """Catches the next copy, wherever it lands.

    A literal like ``version="0.1.0"`` in a module is invisible to the two-file
    check above, which is exactly how that one survived every release.
    ``__version__ = "..."`` in ``iris/__init__.py`` is the declaration itself and
    is exempt; any *other* module naming a version is the bug.
    """
    offenders: list[str] = []
    for path in sorted((PROJECT / "src").rglob("*.py")):
        if path.name == "__init__.py" and path.parent.name == "iris":
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'''["'](\d+\.\d+\.\d+)["']''', text):
            snippet = text[max(0, match.start() - 60):match.start()]
            # Only flag version-shaped literals; a dependency pin inside a URL or
            # a schema constant is not this bug.
            if "version" in snippet.lower() or "fastapi" in snippet.lower():
                offenders.append(f"{path.relative_to(PROJECT)}: {match.group(1)}")
                break
    assert not offenders, f"hardcoded version literals: {offenders}"