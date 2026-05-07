from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_FORBIDDEN = re.compile(r'[/\\:*?"<>|\x00-\x1f]')
_MAX_LEN = 200


def long_path(p: Path | str) -> str:
    r"""Vrátí cestu vhodnou pro Win32 API i nad MAX_PATH=260 znaků.

    Na Windows bez `\\?\` prefixu selžou `open()`, `os.stat()`, `os.replace()`
    apod. s ENOENT pro cesty ≥ 260 znaků (typicky pro hluboké zanoření v
    output/ s českou diakritikou v názvech). Tento helper předponu doplní
    a zároveň cestu absolutizuje + normalizuje (`\\?\` nesnese `..` ani `.`).

    Na non-Windows platformách vrátí jen `str(p)` (žádný no-op overhead).
    Použij u všech `open()`, `os.makedirs()`, `os.stat()`, `os.replace()`
    voláních, jejichž cíl může být v hluboké output cestě.
    """
    s = str(p)
    if sys.platform != "win32":
        return s
    if s.startswith("\\\\?\\") or s.startswith("\\\\.\\"):
        return s
    abs_s = os.path.abspath(s)
    if abs_s.startswith("\\\\"):
        # UNC: \\server\share -> \\?\UNC\server\share
        return "\\\\?\\UNC\\" + abs_s[2:]
    return "\\\\?\\" + abs_s

# Vzor pro Blackboard interní path k souborům (`/bbcswebdav/.../xid-xxx`).
BBCSWEBDAV_RE = re.compile(r'/bbcswebdav/[^\s"\'<>]+')


def clean_url(url: str) -> str:
    """Z URL ořeže query string, fragment a trailing slash.

    Používá se pro deduplikaci a porovnání odkazů, kde varianty jako
    `?xythos-download=true` a `#anchor` jsou pro identitu nepodstatné.
    """
    return url.split("?", 1)[0].split("#", 1)[0].rstrip("/")


def iso_now() -> str:
    """UTC timestamp ve tvaru `YYYY-MM-DDTHH:MM:SSZ` (jednotný formát napříč projektem)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_size(b: int | None) -> str:
    """Lidsky čitelná velikost: 1024 → '1.0 KB', 1234567 → '1.2 MB'."""
    if not b:
        return "0 B"
    f = float(b)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024:
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"


def sanitize_filename(s: str) -> str:
    """Sanitizuje řetězec na bezpečný název souboru/složky.

    Pravidla:
    - Nahradí `/`, `\\`, `:`, `*`, `?`, `"`, `<`, `>`, `|` a control chars za `_`.
    - Trimne whitespace a tečky na konci (Windows neumí `name.`).
    - Trimne na max 200 znaků.
    - Prázdný výsledek → `untitled`.
    - Diakritiku zachovává.
    """
    if not isinstance(s, str):
        s = str(s)
    cleaned = _FORBIDDEN.sub("_", s)
    cleaned = cleaned.strip()
    cleaned = cleaned.rstrip(". ")
    if len(cleaned) > _MAX_LEN:
        cleaned = cleaned[:_MAX_LEN].rstrip(". ")
    if not cleaned:
        cleaned = "untitled"
    return cleaned


def unique_path(directory: Path, name: str) -> Path:
    """Vrátí cestu, která v `directory` neexistuje. Při kolizi přidá `_2`, `_3`, ..."""
    base = Path(name)
    candidate = directory / base
    if not os.path.exists(long_path(candidate)):
        return candidate

    stem = base.stem
    suffix = base.suffix
    i = 2
    while True:
        candidate = directory / f"{stem}_{i}{suffix}"
        if not os.path.exists(long_path(candidate)):
            return candidate
        i += 1
