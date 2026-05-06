from __future__ import annotations

from pathlib import Path

import pytest

from bb_backup.config import Config, ConfigError, find_config_file, load_config


def _write_config(dir: Path, content: str, cookies: bool = True) -> Path:
    cfg = dir / "config.toml"
    cfg.write_text(content, encoding="utf-8")
    if cookies:
        (dir / "cookies.txt").write_text("# placeholder\n", encoding="utf-8")
    return cfg


@pytest.fixture
def chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_load_valid_config(chdir):
    _write_config(
        chdir,
        """
[blackboard]
base_url = "https://elearning.example.com"
cookies_file = "cookies.txt"
""",
    )
    cfg = load_config()
    assert isinstance(cfg, Config)
    assert cfg.blackboard.base_url == "https://elearning.example.com"
    assert cfg.download.request_delay_ms == 200
    assert cfg.tui.default_select_all is True
    assert cfg.logging.level == "INFO"


def test_missing_config_raises(chdir):
    with pytest.raises(ConfigError, match="config.toml nenalezen"):
        load_config()


def test_empty_base_url_rejected(chdir):
    _write_config(chdir, """[blackboard]\nbase_url = ""\n""")
    with pytest.raises(ConfigError, match="base_url"):
        load_config()


def test_non_https_base_url_rejected(chdir):
    _write_config(chdir, """[blackboard]\nbase_url = "http://insecure.example.com"\n""")
    with pytest.raises(ConfigError, match="https://"):
        load_config()


def test_missing_cookies_file_rejected(chdir):
    _write_config(
        chdir,
        """[blackboard]\nbase_url = "https://x.example.com"\ncookies_file = "missing.txt"\n""",
        cookies=False,
    )
    with pytest.raises(ConfigError, match="cookies"):
        load_config()


def test_base_url_trailing_slash_stripped(chdir):
    _write_config(chdir, """[blackboard]\nbase_url = "https://x.example.com/"\n""")
    cfg = load_config()
    assert cfg.blackboard.base_url == "https://x.example.com"


def test_invalid_request_delay_rejected(chdir):
    _write_config(
        chdir,
        """
[blackboard]
base_url = "https://x.example.com"
[download]
request_delay_ms = 99999
""",
    )
    with pytest.raises(ConfigError):
        load_config()


def test_unknown_field_rejected(chdir):
    _write_config(
        chdir,
        """
[blackboard]
base_url = "https://x.example.com"
unknown_field = "x"
""",
    )
    with pytest.raises(ConfigError):
        load_config()


def test_fallback_to_home_config(tmp_path, monkeypatch):
    workdir = tmp_path / "work"
    workdir.mkdir()
    home = tmp_path / "home"
    (home / ".config" / "bb-backup").mkdir(parents=True)
    (home / ".config" / "bb-backup" / "config.toml").write_text(
        """[blackboard]\nbase_url = "https://x.example.com"\ncookies_file = "cookies.txt"\n""",
        encoding="utf-8",
    )
    (home / ".config" / "bb-backup" / "cookies.txt").write_text("x", encoding="utf-8")

    monkeypatch.chdir(workdir)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    found = find_config_file()
    assert found is not None
    assert found.parent.name == "bb-backup"

    cfg = load_config()
    assert cfg.blackboard.base_url == "https://x.example.com"


def test_resolve_paths_relative_to_config(chdir):
    _write_config(
        chdir,
        """
[blackboard]
base_url = "https://x.example.com"
[paths]
output_dir = "out"
state_dir = "st"
log_dir = "lg"
""",
    )
    cfg = load_config()
    assert cfg.output_dir == chdir / "out"
    assert cfg.state_dir == chdir / "st"
    assert cfg.log_dir == chdir / "lg"
