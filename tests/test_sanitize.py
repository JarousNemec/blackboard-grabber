from __future__ import annotations

from pathlib import Path

import pytest

from bb_backup.utils import sanitize_filename, unique_path


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("hello", "hello"),
        ("a/b\\c", "a_b_c"),
        ('a:b*c?d"e<f>g|h', "a_b_c_d_e_f_g_h"),
        ("  spaced  ", "spaced"),
        ("trailing dots...", "trailing dots"),
        ("trailing space.   ", "trailing space"),
        ("", "untitled"),
        ("   ", "untitled"),
        ("....", "untitled"),
        ("Týden 1 — Úvod", "Týden 1 — Úvod"),
        ("příliš žluťoučký kůň úpěl ďábelské ódy", "příliš žluťoučký kůň úpěl ďábelské ódy"),
    ],
)
def test_sanitize_basic(raw: str, expected: str):
    assert sanitize_filename(raw) == expected


def test_sanitize_long_name_truncated_to_200():
    name = "a" * 500
    out = sanitize_filename(name)
    assert len(out) <= 200
    assert out == "a" * 200


def test_sanitize_long_name_with_trailing_dot_no_dot_at_end():
    name = "a" * 199 + "..."
    out = sanitize_filename(name)
    assert len(out) <= 200
    assert not out.endswith(".")


def test_sanitize_control_chars_replaced():
    out = sanitize_filename("a\x00b\x01c")
    assert "\x00" not in out
    assert out == "a_b_c"


def test_sanitize_diacritics_byte_for_byte():
    """diakritika musí zůstat zachovaná (AC-S2)."""
    s = "Týden 1 — Úvod"
    assert sanitize_filename(s) == s


def test_unique_path_no_collision(tmp_path: Path):
    p = unique_path(tmp_path, "novy.txt")
    assert p == tmp_path / "novy.txt"


def test_unique_path_one_collision(tmp_path: Path):
    (tmp_path / "novy.txt").write_text("x")
    p = unique_path(tmp_path, "novy.txt")
    assert p == tmp_path / "novy_2.txt"


def test_unique_path_many_collisions(tmp_path: Path):
    (tmp_path / "novy.txt").write_text("x")
    (tmp_path / "novy_2.txt").write_text("x")
    (tmp_path / "novy_3.txt").write_text("x")
    p = unique_path(tmp_path, "novy.txt")
    assert p == tmp_path / "novy_4.txt"


def test_unique_path_extension_stem_split(tmp_path: Path):
    (tmp_path / "soubor.tar.gz").write_text("x")
    p = unique_path(tmp_path, "soubor.tar.gz")
    # Path.stem split jen poslední příponu — to je akceptovatelné
    assert p.parent == tmp_path
    assert p.name.endswith(".gz")
    assert not p.exists()
