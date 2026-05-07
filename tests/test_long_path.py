r"""Testy pro long_path() helper a chování s cestami delšími než MAX_PATH=260.

Kritické na Windows: bez `\\?\` prefixu Win32 API selže s ENOENT pro cesty
≥ 260 znaků. Reprodukuje se typickou Blackboard strukturou kurzů s českou
diakritikou (Obsah\<dlouhá kapitola>\<dlouhá podkapitola>\<dlouhý PDF>.pdf).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from bb_backup.utils import long_path


def test_long_path_passthrough_on_posix():
    if sys.platform == "win32":
        pytest.skip("only relevant on POSIX")
    p = Path("/tmp/foo/bar.txt")
    assert long_path(p) == "/tmp/foo/bar.txt"


def test_long_path_adds_prefix_on_windows():
    if sys.platform != "win32":
        pytest.skip("Windows-only")
    p = Path(r"C:\Users\foo\bar.txt")
    result = long_path(p)
    assert result.startswith("\\\\?\\")
    assert "C:\\Users\\foo\\bar.txt" in result


def test_long_path_idempotent():
    """Aplikování dvakrát nemá zdvojit prefix."""
    if sys.platform != "win32":
        pytest.skip("Windows-only")
    p = r"\\?\C:\already\prefixed.txt"
    assert long_path(p) == p


def test_long_path_normalizes_relative():
    r"""Relativní cesta se musí absolutizovat (\\?\ vyžaduje absolute)."""
    if sys.platform != "win32":
        pytest.skip("Windows-only")
    result = long_path("relative/path.txt")
    assert result.startswith("\\\\?\\")
    # Žádné dot komponenty
    assert "\\..\\" not in result
    assert not result.endswith("\\.")


def test_open_with_very_long_path(tmp_path):
    """End-to-end: vytvoř cestu > 260 znaků a ověř, že open() projde
    přes long_path() obal. Na non-Windows tohle samozřejmě prošlo už dřív
    (bez limitu), tady jen kontrolujeme, že náš helper nic nerozbije."""
    # Postavíme vnořený strom hluboké cesty s unicode jmény (typická
    # Blackboard struktura kurzu). Cílíme nad 260 znaků celkové délky.
    parent = tmp_path
    for chunk in [
        "Úvod do objektového modelování",
        "Návrhový model tříd I - Datové typy",
        "Zavedení návrhových prvků do modelu tříd I - datové typy",
    ]:
        parent = parent / chunk
    filename = "C07 - Zavedení návrhových prvků do modelu tříd I.pdf"
    full_path = parent / filename

    # Tenhle assert je jádro testu — bez něj test ztrácí smysl.
    assert len(str(full_path)) > 260, (
        f"Test path není dost dlouhá ({len(str(full_path))} znaků), "
        f"upravit chunks aby překonaly MAX_PATH"
    )

    os.makedirs(long_path(parent), exist_ok=True)
    with open(long_path(full_path), "wb") as f:
        f.write(b"hello")

    assert os.path.isfile(long_path(full_path))
    assert os.stat(long_path(full_path)).st_size == 5

    # Cleanup pomocí long_path, aby pytest tmp_path teardown zvládl smazat.
    os.remove(long_path(full_path))
