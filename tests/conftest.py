"""Keep the test session out of the real metadata database.

``iris-home/iris.db`` is the corpus: 10 registered firmware, the evaluated
numbers in ``docs/eval-log.md``, nothing regenerable. It is also the default
``IRIS_DATABASE_URL``, so any test that reached the database would write into it --
and ``emulate_firmware`` now records every run, which turns "a test called the
orchestrator" into "a fake firmware is now in the corpus, with a web reach rate".

Redirecting the whole session at a temp file is stronger than an opt-out flag on
the recorder: it also covers the CLI and API layers, which have no such flag, and
it means a future write path is isolated by default rather than by remembering.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Set before any test module imports, so a module-level ``get_settings()`` in an
# imported module cannot cache the real URL first.
_SESSION_DB = Path(os.environ.get("IRIS_TEST_DB", "")) if os.environ.get("IRIS_TEST_DB") else None


@pytest.fixture(scope="session", autouse=True)
def _isolated_database(tmp_path_factory: pytest.TempPathFactory):
    """Point ``get_settings().database_url`` at a throwaway SQLite file."""
    from iris import config

    db_path = (_SESSION_DB or (tmp_path_factory.mktemp("db") / "iris-test.db")).resolve()
    original = config._settings
    if original is not None:
        config._settings = original.model_copy(update={"database_url": f"sqlite:///{db_path.as_posix()}"})
    else:
        config._settings = config.Settings(database_url=f"sqlite:///{db_path.as_posix()}")
    yield db_path
    config._settings = original