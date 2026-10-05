from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IRIS_", env_file=".env", extra="ignore")

    iris_home: Path = Path("iris-home")
    #: Empty rather than a literal path, because the default is *derived* from
    #: ``iris_home`` below. Hardcoding ``sqlite:///iris-home/iris.db`` here is what
    #: made ``IRIS_HOME=<temp>`` half-isolate: the corpus and the scratch area moved,
    #: the database did not, and a run against the throwaway home still wrote into the
    #: real corpus.
    database_url: str = ""

    @model_validator(mode="after")
    def _database_url_follows_iris_home(self) -> "Settings":
        """Put the database inside ``iris_home`` unless told otherwise.

        Two settings that name the same store but are read independently is the
        worst shape a default can have: pointing one at a temporary directory looks
        like full isolation and is not, so whatever the next command writes is
        written to the real thing. An explicit ``IRIS_DATABASE_URL`` -- which is
        what points at PostgreSQL -- still wins, because only an empty value gets
        derived.
        """
        if not self.database_url:
            self.database_url = f"sqlite:///{(self.iris_home / 'iris.db').as_posix()}"
        return self

    log_level: str = "INFO"

    download_mirror: str = "https://gh-proxy.com/"

    #: Shared secret for the orchestration API. Empty means "local mode": every
    #: caller is `iris.api.auth.LOCAL_CLIENT`, which is only safe because
    #: ``iris serve start`` refuses to bind a non-loopback address without one.
    api_token: str = ""
    #: Cap on an uploaded firmware image. `/api/v1/pipeline` used to read the
    #: whole body into memory with no limit, so a single request could exhaust
    #: the host.
    api_max_upload_mb: int = 64

    @property
    def rules_dir(self) -> Path:
        for base in [Path.cwd().resolve(), *Path.cwd().resolve().parents]:
            if (base / "rules").is_dir() and (base / "src").is_dir():
                return base / "rules"
        return Path("rules")

    #: Third-party rule plugins, kept out of ``rules_dir`` on purpose.
    #:
    #: ``rules_dir`` is IRIS's own source tree -- in a checkout it is a git-tracked
    #: directory, and in a wheel install it is next to the package or absent. Writing
    #: an operator's plugin there would either dirty the repository or fail outright,
    #: so external plugins get their own directory under ``iris_home``, which is the
    #: one place in this project that is meant to hold data the user produced.
    #: It also means ``IRIS_IRIS_HOME`` moves the plugin set with everything else,
    #: which is what makes an isolated verification run isolated.
    @property
    def plugin_dir(self) -> Path:
        return self.iris_home / "plugins"

    @property
    def effective_rules_dirs(self) -> list[Path]:
        """Every rule directory to load, IRIS's own first.

        Order is the precedence order. A plugin whose ``id`` collides with a
        built-in rule is refused at upload time, so the only way to reach a collision
        here is to drop a file into the directory by hand; the loader keeps the first
        and says so in the log rather than silently letting the second one win.
        """
        return [self.rules_dir, self.plugin_dir]

    @property
    def corpus_dir(self) -> Path:
        return self.iris_home / "corpus"

    @property
    def scratch_dir(self) -> Path:
        return self.iris_home / "scratch"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
