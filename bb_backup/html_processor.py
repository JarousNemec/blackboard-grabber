from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from .client import BlackboardClient, BlackboardError
from .utils import clean_url, sanitize_filename, unique_path

logger = logging.getLogger("bb_backup.html_processor")

# Blackboard placeholder, který frontend nahrazuje na klient straně. V API
# odpovědi přichází literálně, my ho nahradíme za base_url.
PLACEHOLDER = "@X@EmbeddedFile.requestUrlStub@X@"

# Z `/bbcswebdav/.../xid-12345_1` vyextrahuje `12345_1`.
_XID_RE = re.compile(r"/bbcswebdav/(?:.*?/)?xid-([\w-]+)")

# Atributy elementů, které zkoumáme.
URL_ATTRS = {
    "img": ("src",),
    "a": ("href",),
    "source": ("src",),
    "video": ("src", "poster"),
    "audio": ("src",),
    "iframe": ("src",),
    "link": ("href",),
    "object": ("data",),
    "embed": ("src",),
}


def _filename_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    last = parsed.path.rsplit("/", 1)[-1]
    last = unquote(last)
    if not last or last.startswith("xid-"):
        return None
    return last


def _filename_from_response(resp_headers: dict) -> str | None:
    cd = resp_headers.get("Content-Disposition") or resp_headers.get("content-disposition")
    if not cd:
        return None
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, flags=re.IGNORECASE)
    if not m:
        return None
    return unquote(m.group(1))


def _resolve_asset_filename(url: str, fallback: str) -> str:
    name = _filename_from_url(url)
    if name:
        return sanitize_filename(name)
    return sanitize_filename(fallback)


def _download_asset(
    client: BlackboardClient,
    url: str,
    assets_dir: Path,
    *,
    seen: dict[str, Path],
) -> Path | None:
    """Stáhne asset z URL/path do assets_dir. Vrátí cestu nebo None při chybě."""
    if url in seen:
        return seen[url]

    xid_match = _XID_RE.search(url)
    fallback = f"asset-{xid_match.group(1)}" if xid_match else "asset"
    proposed = _resolve_asset_filename(url, fallback)
    assets_dir.mkdir(parents=True, exist_ok=True)
    dest = unique_path(assets_dir, proposed)

    try:
        client.download_url(url, dest)
    except BlackboardError as e:
        logger.warning("nelze stáhnout asset %s: %s", url, e)
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("neočekávaná chyba stahování %s: %s", url, e)
        return None

    seen[url] = dest
    return dest


def _replace_placeholders(body: str, base_url: str) -> str:
    return body.replace(PLACEHOLDER, base_url.rstrip("/"))


def process_html(
    body: str,
    client: BlackboardClient,
    course_id: str,
    content_id: str,
    dest_dir: Path,
    *,
    skip_urls: dict[str, str] | None = None,
) -> str:
    """Zpracuje HTML body: stáhne embedované assety, přepíše odkazy na lokální.

    `skip_urls` je {bbcswebdav_url: local_filename} mapa souborů, které už jsou
    stažené jinde (typicky downloaderem jako attachment top-level). Pro tyto URL
    pouze přepíšeme odkaz na relativní cestu, znovu nestahujeme.

    Vrátí upravený HTML string. `dest_dir` je složka, kde leží `index.html`;
    nezachycené assety se ukládají do `dest_dir/_assets/`.
    """
    skip_urls = skip_urls or {}

    # 1. Substituce Blackboard placeholderu za base URL.
    rendered = _replace_placeholders(body, client.base_url)

    # 2. Najdi a zpracuj odkazy.
    soup = BeautifulSoup(rendered, "html.parser")
    assets_dir = dest_dir / "_assets"
    seen: dict[str, Path] = {}

    for tag_name, attrs in URL_ATTRS.items():
        for tag in soup.find_all(tag_name):
            for attr in attrs:
                value = tag.get(attr)
                if not value or not isinstance(value, str):
                    continue
                if "/bbcswebdav/" not in value:
                    continue

                # Pokud je to relativní path (po placeholder replace by neměl, ale jistota).
                if not value.startswith(("http://", "https://")):
                    value_full = client.base_url + (value if value.startswith("/") else "/" + value)
                else:
                    value_full = value

                # Pokud už je soubor stažený jako attachment, jen přepiš link.
                cleaned = clean_url(value_full)
                hit = None
                for skip_url, local_name in skip_urls.items():
                    if skip_url == cleaned or cleaned.startswith(skip_url):
                        hit = local_name
                        break
                if hit is not None:
                    tag[attr] = hit
                    continue

                # Jinak stáhni do _assets/.
                local = _download_asset(client, value_full, assets_dir, seen=seen)
                if local is None:
                    continue
                rel = f"_assets/{local.name}"
                tag[attr] = rel

    return str(soup)
