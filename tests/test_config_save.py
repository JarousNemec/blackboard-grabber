"""Testy pro save_blackboard_settings — wizard helper, který zapisuje
URL a cestu ke cookies do config.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from bb_backup.config import save_blackboard_settings


def _read_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def test_save_creates_new_file_when_missing(tmp_path):
    cfg = tmp_path / "config.toml"
    assert not cfg.exists()

    save_blackboard_settings(cfg, "https://example.com", "cookies.txt")

    assert cfg.is_file()
    data = _read_toml(cfg)
    assert data["blackboard"]["base_url"] == "https://example.com"
    assert data["blackboard"]["cookies_file"] == "cookies.txt"


def test_save_preserves_other_sections(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        """
[blackboard]
base_url = "https://old.example.com"
cookies_file = "old.txt"

[paths]
output_dir = "my_output"
state_dir = "my_state"

[download]
request_delay_ms = 500
max_retries = 5
""",
        encoding="utf-8",
    )

    save_blackboard_settings(cfg, "https://new.example.com", "new_cookies.txt")

    data = _read_toml(cfg)
    assert data["blackboard"]["base_url"] == "https://new.example.com"
    assert data["blackboard"]["cookies_file"] == "new_cookies.txt"
    # Ostatní sekce musí zůstat zachované.
    assert data["paths"]["output_dir"] == "my_output"
    assert data["paths"]["state_dir"] == "my_state"
    assert data["download"]["request_delay_ms"] == 500
    assert data["download"]["max_retries"] == 5


def test_save_creates_parent_directory(tmp_path):
    nested = tmp_path / "a" / "b" / "config.toml"
    save_blackboard_settings(nested, "https://example.com", "cookies.txt")
    assert nested.is_file()


def test_save_atomic_no_tmp_leftover(tmp_path):
    cfg = tmp_path / "config.toml"
    save_blackboard_settings(cfg, "https://example.com", "cookies.txt")

    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], f"Tmp soubor zůstal: {leftovers}"


def test_save_overwrites_only_blackboard_keys(tmp_path):
    """Pokud má [blackboard] custom klíč navíc (např. budoucí pole),
    nesmíme ho omylem smazat."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        """
[blackboard]
base_url = "https://old.example.com"
cookies_file = "old.txt"
custom_field = "should_survive"
""",
        encoding="utf-8",
    )

    save_blackboard_settings(cfg, "https://new.example.com", "new.txt")

    data = _read_toml(cfg)
    assert data["blackboard"]["base_url"] == "https://new.example.com"
    assert data["blackboard"]["cookies_file"] == "new.txt"
    assert data["blackboard"].get("custom_field") == "should_survive"
