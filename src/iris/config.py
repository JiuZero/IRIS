from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IRIS_", env_file=".env", extra="ignore")

    iris_home: Path = Path("iris-home")
    database_url: str = "sqlite:///iris-home/iris.db"
    log_level: str = "INFO"

    timeout_initial: int = 240
    timeout_check: int = 360
    check_timeout: int = 360

    download_mirror: str = "https://gh-proxy.com/"

    @property
    def home(self) -> Path:
        return self.iris_home

    @property
    def rules_dir(self) -> Path:
        for base in [Path.cwd().resolve(), *Path.cwd().resolve().parents]:
            if (base / "rules").is_dir() and (base / "src").is_dir():
                return base / "rules"
        return Path("rules")

    @property
    def corpus_dir(self) -> Path:
        return self.iris_home / "corpus"

    @property
    def images_dir(self) -> Path:
        return self.iris_home / "images"

    @property
    def scratch_dir(self) -> Path:
        return self.iris_home / "scratch"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None