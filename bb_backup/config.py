from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConfigError(Exception):
    """Raised when config file is missing or invalid."""


class BlackboardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    cookies_file: str = "cookies.txt"

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("base_url je prázdné — vyplň URL Blackboard instance")
        if not v.startswith("https://"):
            raise ValueError("base_url musí začínat https://")
        return v.rstrip("/")


class PathsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_dir: str = "output"
    state_dir: str = "state"
    log_dir: str = "logs"


class DownloadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_delay_ms: Annotated[int, Field(ge=0, le=10000)] = 200
    max_retries: Annotated[int, Field(ge=1, le=10)] = 3
    http_timeout_s: Annotated[int, Field(ge=1, le=600)] = 30
    verify_size: bool = True


class TuiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_select_all: bool = True


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    console: bool = True


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blackboard: BlackboardConfig
    paths: PathsConfig = Field(default_factory=PathsConfig)
    download: DownloadConfig = Field(default_factory=DownloadConfig)
    tui: TuiConfig = Field(default_factory=TuiConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    config_path: Path | None = None

    def cookies_path(self) -> Path:
        p = Path(self.blackboard.cookies_file)
        if not p.is_absolute() and self.config_path is not None:
            p = self.config_path.parent / p
        return p

    def resolve_path(self, p: str) -> Path:
        path = Path(p)
        if not path.is_absolute() and self.config_path is not None:
            path = self.config_path.parent / path
        return path

    @property
    def output_dir(self) -> Path:
        return self.resolve_path(self.paths.output_dir)

    @property
    def state_dir(self) -> Path:
        return self.resolve_path(self.paths.state_dir)

    @property
    def log_dir(self) -> Path:
        return self.resolve_path(self.paths.log_dir)


def save_blackboard_settings(path: Path, base_url: str, cookies_file: str) -> None:
    """Zapíše base_url a cookies_file do config.toml. Soubor vytvoří pokud
    neexistuje. Pokud existuje, ostatní sekce ([paths], [download], ...)
    zůstanou zachované, ale komentáře se ztratí (tomli-w je round-trip
    neumí). Atomic rename pattern stejný jako jinde v kódu."""
    import tomli_w  # lazy — používá se jen z wizardu

    if path.is_file():
        with path.open("rb") as f:
            data = tomllib.load(f)
    else:
        data = {}
    data.setdefault("blackboard", {})
    data["blackboard"]["base_url"] = base_url
    data["blackboard"]["cookies_file"] = cookies_file

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as f:
        tomli_w.dump(data, f)
    tmp.replace(path)


def find_config_file() -> Path | None:
    cwd = Path.cwd() / "config.toml"
    if cwd.is_file():
        return cwd
    fallback = Path.home() / ".config" / "bb-backup" / "config.toml"
    if fallback.is_file():
        return fallback
    return None


def load_config(path: Path | None = None) -> Config:
    cfg_path = path if path is not None else find_config_file()
    if cfg_path is None or not cfg_path.is_file():
        raise ConfigError(
            "config.toml nenalezen. Spusť `bb-backup init` "
            "a vyplň config.example.toml jako config.toml."
        )

    try:
        with cfg_path.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"config.toml má neplatný TOML syntax: {e}") from e

    try:
        cfg = Config(**raw)
    except Exception as e:
        raise ConfigError(f"config.toml je neplatný: {e}") from e

    cfg.config_path = cfg_path.resolve()

    cookies = cfg.cookies_path()
    if not cookies.is_file():
        raise ConfigError(
            f"Soubor s cookies neexistuje: {cookies}\n"
            "Vyexportuj cookies z prohlížeče přes rozšíření 'Get cookies.txt LOCALLY' "
            "a ulož je do tohoto souboru."
        )

    return cfg
