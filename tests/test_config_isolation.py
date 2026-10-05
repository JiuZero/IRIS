"""Pointing ``iris_home`` somewhere else has to move *everything* that lives in it.

``database_url`` used to carry its own literal default, ``sqlite:///iris-home/iris.db``,
read independently of ``iris_home``. Setting ``IRIS_HOME`` to a temporary directory
therefore moved the corpus and the scratch area and left the metadata database exactly
where it was -- an isolation that isolated most of what it appeared to. Verifying the
history-clearing endpoints under that belief deleted 81 recorded runs from the real
corpus; one of them could not be restored.

The tests here pin the coupling in both directions: the default has to stay what the
docs promise, and a relocated home has to take the database with it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from iris.config import Settings

PROJECT = Path(__file__).resolve().parents[1]


class TestTheDefaultIsUnchanged:
    """Changing the default's *shape* would silently repoint every install, so the
    literal the documentation quotes is itself part of the contract."""

    def test_the_shipped_default_is_the_documented_path(self) -> None:
        assert Settings().database_url == "sqlite:///iris-home/iris.db"

    def test_the_home_is_still_relative_to_the_working_directory(self) -> None:
        """An absolute default would make the same checkout behave differently
        depending on where it was launched from."""

        assert Settings().iris_home == Path("iris-home")


class TestTheDatabaseFollowsTheHome:
    def test_a_relocated_home_moves_the_database(self, tmp_path) -> None:
        assert Settings(iris_home=str(tmp_path)).database_url == \
            f"sqlite:///{(tmp_path / 'iris.db').as_posix()}"


    def test_the_scratch_area_and_the_database_agree(self, tmp_path) -> None:
        """The two settings name the same store, so a home that isolates one and not
        the other is the bug this file exists for."""

        settings = Settings(iris_home=str(tmp_path))
        assert settings.database_url == f"sqlite:///{(settings.iris_home / 'iris.db').as_posix()}"
        assert settings.scratch_dir == tmp_path / "scratch"


class TestAnExplicitUrlStillWins:
    """Deriving the default must not take PostgreSQL -- or any deliberate choice --
    away from whoever set one."""

    def test_a_postgres_url_is_left_alone(self) -> None:
        assert Settings(database_url="postgresql://iris@db/iris").database_url == \
            "postgresql://iris@db/iris"

    def test_an_explicit_sqlite_path_beats_the_derived_one(self, tmp_path) -> None:
        assert Settings(iris_home=str(tmp_path),
                         database_url="sqlite:///elsewhere.db").database_url == \
            "sqlite:///elsewhere.db"


class TestTheCliRespectsItEndToEnd:
    """The unit tests above construct ``Settings`` directly; this one goes through a
    real process, because ``IRIS_HOME`` reaching the application is what actually
    failed -- a library call cannot tell whether the environment was honoured."""

    def _run(self, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PYTHONPATH": str(PROJECT / "src"),
            "NO_COLOR": "1",
            # The whole point: home only, no IRIS_DATABASE_URL alongside it.
            "IRIS_IRIS_HOME": str(home),
        }
        env.pop("IRIS_DATABASE_URL", None)
        return subprocess.run(
            [sys.executable, "-m", "iris.cli", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=PROJECT, env=env, timeout=120, check=False,
        )

    def test_db_init_creates_the_database_under_the_relocated_home(self, tmp_path) -> None:
        home = tmp_path / "elsewhere"

        result = self._run(home, "db", "init")

        assert result.returncode == 0, result.stderr
        assert (home / "iris.db").is_file(), (
            "IRIS_HOME moved the home but not the database; the file the CLI "
            "reported is not the file it created"
        )

    def test_the_reported_path_is_the_created_one(self, tmp_path) -> None:
        """``iris db init`` prints the URL it used, so an operator can see which
        store they just created without guessing -- and an operator who set only
        ``IRIS_HOME`` can confirm the isolation took effect."""

        home = tmp_path / "elsewhere"

        result = self._run(home, "db", "init")

        assert (home / "iris.db").as_posix() in result.stdout, result.stdout