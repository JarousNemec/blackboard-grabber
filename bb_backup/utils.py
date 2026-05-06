from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

_FORBIDDEN = re.compile(r'[/\\:*?"<>|\x00-\x1f]')
_MAX_LEN = 200

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
    if not candidate.exists():
        return candidate

    stem = base.stem
    suffix = base.suffix
    i = 2
    while True:
        candidate = directory / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1
