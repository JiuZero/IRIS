from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: How long a guest gets to answer on its web port before the run is called failed,
#: in seconds. One number for the API, the CLI and the workbench, because three
#: hand-kept literals had already drifted apart (120 in the API and the CLI, 200 in
#: the workbench) and neither pair of users could tell the difference.
#:
#: The floor used to be 120, which is below what the slow firmwares actually take:
#: measured successful boots on this corpus are 52s (DIR-868L, armel), 130s (RP3),
#: 179s (G1) and 257s / 334s / 420s (TES7002, arm64). A default under the slowest
#: observed success is not patience, it is a wrong answer delivered on a timer: the
#: guest would have come up, and IRIS reported that it did not. 600 leaves roughly
#: 43% over the slowest success measured here, so an ordinary slow boot no longer
#: has to be re-run by hand with a bigger number.
#:
#: This is a *default*, not a ceiling: every entry point still accepts an explicit
#: timeout, and the API keeps its own upper bound.
DEFAULT_BOOT_TIMEOUT_SEC = 600


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

    #: Also write the log to ``<iris_home>/logs/iris.log``. Off by default: a
    #: command that writes a file nobody asked for is a surprise that eventually
    #: fills a disk, and the terminal is what someone launching a command expects.
    #: ``iris web`` is the case that wants it -- a long-running service whose only
    #: record was the terminal it was started in -- so it is a setting rather than a
    #: special case, and it rotates so "leave it running" is survivable.
    log_to_file: bool = False

    @property
    def log_file(self) -> Path:
        """Where ``log_to_file`` writes. Derived, so it follows ``iris_home``."""
        return self.iris_home / "logs" / "iris.log"

    download_mirror: str = "https://gh-proxy.com/"

    #: Shared secret for the orchestration API. Empty means "local mode": every
    #: caller is `iris.api.auth.LOCAL_CLIENT`, which is only safe because
    #: ``iris serve start`` refuses to bind a non-loopback address without one.
    api_token: str = ""
    #: Cap on an uploaded firmware image. `/api/v1/pipeline` used to read the
    #: whole body into memory with no limit, so a single request could exhaust
    #: the host.
    api_max_upload_mb: int = 64

    #: OpenAI-compatible LLM endpoint for the guardian's long-tail attribution.
    #: Empty means the whole LLM layer is disabled: the deterministic rule engine
    #: keeps working unchanged, and nothing ever waits on a model that is not
    #: configured. Accepts a local endpoint (ollama, vLLM) as readily as a hosted
    #: one -- a judge running offline must be able to point this at localhost.
    llm_base_url: str = ""
    #: Never logged, never echoed back by any endpoint -- the same bargain
    #: ``api_token`` keeps ("configured or not", never the value).
    llm_api_key: str = ""
    llm_model: str = ""
    #: One diagnosis is one round trip with a bounded serial-log tail in the
    #: prompt. A timeout above this would leave a web request hanging on a model
    #: that is not going to answer; retries stay at 1 because a diagnosis is
    #: re-triggerable by hand and a slow endpoint should be visible, not retried
    #: into invisibility.
    llm_timeout_sec: float = 60.0
    llm_max_retries: int = 1
    #: Ceiling on the serial-log tail fed into one prompt. Real logs run to
    #: 1.35 MB here; sending one whole would make a single diagnosis cost more
    #: than the run it diagnoses. The tail plus the crash context is what carries
    #: the signal -- the first 90% of a boot log is the same every run.
    llm_max_context_chars: int = 12000

    @property
    def llm_enabled(self) -> bool:
        """Both an endpoint and a model name are needed; one without the other
        is a half-configuration that would fail on every request."""
        return bool(self.llm_base_url.strip()) and bool(self.llm_model.strip())

    @property
    def llm_draft_dir(self) -> Path:
        """Where LLM-authored rule drafts wait for a human decision.

        Kept apart from ``plugin_dir`` for the same reason ``plugin_dir`` is kept
        out of ``rules_dir``: the engine loads everything in the plugin directory
        on the next run, and a hallucinated document must never get that far
        without a person accepting it first.
        """
        return self.iris_home / "ai-drafts"

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
