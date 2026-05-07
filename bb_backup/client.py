from __future__ import annotations

import http.cookiejar
import logging
import os
import time
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import requests

from .config import Config
from .models import (
    Attachment,
    ContentItem,
    Course,
    Membership,
    PagedResponse,
    User,
)
from .utils import long_path

logger = logging.getLogger("bb_backup.client")

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36 bb-backup/0.1"
)
_STREAM_CHUNK_SIZE = 8 * 1024


class BlackboardError(Exception):
    """Obecná chyba z Blackboard API."""


class AuthError(BlackboardError):
    """401/403 — cookies expirovaly nebo nejsou platné pro tento endpoint."""


class BlackboardClient:
    """Wrapper kolem requests.Session pro Blackboard interní API."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.base_url = config.blackboard.base_url
        self._delay_s = config.download.request_delay_ms / 1000.0
        self._max_retries = config.download.max_retries
        self._timeout = config.download.http_timeout_s

        cookies_path = config.cookies_path()
        jar = http.cookiejar.MozillaCookieJar(str(cookies_path))
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except (http.cookiejar.LoadError, OSError) as e:
            raise BlackboardError(
                f"Nepodařilo se načíst cookies z {cookies_path}: {e}\n"
                "Zkontroluj, že soubor je v Netscape formátu (export přes browser extension)."
            ) from e

        # KRITICKÝ fix: Netscape cookies.txt zapisuje session cookies (BbRouter,
        # JSESSIONID, ...) s expires=0. MozillaCookieJar to nechá v poli `expires`
        # jako 0, takže `is_expired()` vrátí True (`0 <= now`) a cookielib je
        # při sestavení Cookie hlavičky odfiltruje. Bez vynulování by chyběly
        # ty nejdůležitější auth cookies a každý request končil 401.
        for cookie in jar:
            cookie.expires = None
            cookie.discard = False

        self.session = requests.Session()
        self.session.cookies = jar
        # Minimum hlaviček (jako curl -b). Žádný Origin/Referer — některé WAF je
        # vyhodnocují přísně a flagují náš request jako podezřelý.
        self.session.headers.update(
            {
                "User-Agent": _DEFAULT_UA,
                "Accept": "application/json, text/plain, */*",
            }
        )

        # Snapshot pro reset před každým requestem.
        self._frozen_cookies = list(self.session.cookies)
        self.cookies_path = cookies_path
        self._diagnose_cookies()

    def _restore_cookies(self) -> None:
        """Reset cookie jar na původní stav z cookies.txt.

        Server při každém requestu vrací Set-Cookie hlavičky s novými JSESSIONIDy,
        každou s jiným `Path` atributem. requests je hromadí v session.cookies
        a při dalším requestu pošle všechny — server pak najde konflikt a vrátí
        401. Reset před requestem (ze snapshotu při init) tomu předchází.
        """
        self.session.cookies.clear()
        for c in self._frozen_cookies:
            self.session.cookies.set_cookie(c)

    def _diagnose_cookies(self) -> None:
        """Log info o načtených cookies — pomáhá při Auth selháních."""
        base_host = urlparse(self.base_url).netloc
        domains: dict[str, int] = {}
        for c in self.session.cookies:
            domains[c.domain] = domains.get(c.domain, 0) + 1
        total = sum(domains.values())
        match_count = sum(
            n for d, n in domains.items() if base_host in d or d.lstrip(".") in base_host
        )
        logger.info(
            "Cookies: %d v souboru, %d shoduje se s %s. Domény: %s",
            total,
            match_count,
            base_host,
            ", ".join(f"{d}({n})" for d, n in domains.items()) or "(žádné)",
        )
        self._cookie_total = total
        self._cookie_match = match_count
        self._cookie_domains = domains

    def cookies_for_base(self) -> list[str]:
        """Jména cookies, která se odešlou na base_url (pro probe --debug)."""
        host = urlparse(self.base_url).netloc
        return sorted(
            c.name
            for c in self.session.cookies
            if c.domain == host
            or c.domain == "." + host
            or (c.domain.startswith(".") and host.endswith(c.domain.lstrip(".")))
        )

    def cookie_diagnostics(self) -> str:
        """Human-readable diagnostika pro CLI výpis."""
        base_host = urlparse(self.base_url).netloc
        if self._cookie_total == 0:
            return (
                f"Soubor cookies.txt obsahuje 0 cookies. "
                f"Buď je soubor prázdný, nebo má špatný (ne-Netscape) formát.\n"
                f"  Cesta: {self.cookies_path}"
            )
        if self._cookie_match == 0:
            doms = ", ".join(self._cookie_domains.keys())
            return (
                f"Soubor cookies.txt má {self._cookie_total} cookies, ale ŽÁDNÁ není pro doménu {base_host}.\n"
                f"  Domény v souboru: {doms}\n"
                f"  Buď je base_url v configu jiný host, nebo jsi exportoval cookies "
                f"z jiné stránky než z Blackboardu."
            )
        return (
            f"Cookies vypadají v pořádku ({self._cookie_match}/{self._cookie_total} pro "
            f"{base_host}), ale Blackboard přesto vrátil 401/403.\n"
            f"  Pravděpodobně cookies expirovaly — re-exportuj z čerstvě obnovené Blackboard stránky."
        )

    # -----------------------------
    # Low-level request handling
    # -----------------------------

    def _full_url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            # Pokud je to URL na stejnou instanci, použij ji.
            host = urlparse(path_or_url).netloc
            base_host = urlparse(self.base_url).netloc
            if host == base_host:
                return path_or_url
            return path_or_url  # let caller handle cross-origin
        if not path_or_url.startswith("/"):
            path_or_url = "/" + path_or_url
        return self.base_url + path_or_url

    def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        stream: bool = False,
        allow_redirects: bool = True,
    ) -> requests.Response:
        url = self._full_url(path_or_url)
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            if self._delay_s > 0:
                time.sleep(self._delay_s)
            try:
                # Reset cookies na původní stav z file před každým requestem.
                self._restore_cookies()
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    timeout=self._timeout,
                    stream=stream,
                    allow_redirects=allow_redirects,
                )
            except requests.RequestException as e:
                last_exc = e
                wait = 2**attempt
                logger.warning(
                    "%s %s selhalo (%s), retry %d/%d za %ds",
                    method,
                    url,
                    e,
                    attempt + 1,
                    self._max_retries,
                    wait,
                )
                time.sleep(wait)
                continue

            if resp.status_code in (401, 403):
                body = resp.text[:400].replace("\n", " ")
                raise AuthError(
                    f"Autentizace selhala (HTTP {resp.status_code} na {url}).\n"
                    f"Tělo odpovědi: {body!r}"
                )
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                wait = 2**attempt
                logger.warning(
                    "%s %s vrátil %d, retry %d/%d za %ds",
                    method,
                    url,
                    resp.status_code,
                    attempt + 1,
                    self._max_retries,
                    wait,
                )
                last_exc = BlackboardError(f"HTTP {resp.status_code}")
                time.sleep(wait)
                continue

            if not resp.ok:
                raise BlackboardError(
                    f"{method} {url} vrátil HTTP {resp.status_code}: {resp.text[:200]}"
                )
            return resp

        assert last_exc is not None
        raise BlackboardError(
            f"{method} {url} selhalo po {self._max_retries} pokusech: {last_exc}"
        )

    def _get_json(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self._request("GET", path_or_url, params=params)
        try:
            return resp.json()
        except ValueError as e:
            raise BlackboardError(
                f"GET {path_or_url} nevrátil JSON. "
                f"Tělo začíná: {resp.text[:200]!r}"
            ) from e

    def paginate(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Generic generator přes všechny stránky listing endpointu."""
        next_url: str | None = path
        next_params = dict(params) if params else None
        while next_url:
            data = self._get_json(next_url, params=next_params)
            page = PagedResponse.model_validate(data)
            for item in page.results:
                yield item
            # Po prvním requestu nepoužívej původní params (nextPage je má v URL).
            next_params = None
            if page.paging and page.paging.nextPage:
                next_url = page.paging.nextPage
            else:
                next_url = None

    # -----------------------------
    # High-level API calls
    # -----------------------------

    def get_me(self) -> User:
        data = self._get_json("/learn/api/v1/users/me")
        return User.model_validate(data)

    def get_my_courses(self) -> list[Course]:
        me = self.get_me()
        courses: list[Course] = []
        for raw in self.paginate(
            f"/learn/api/v1/users/{me.id}/memberships",
            params={"expand": "course", "limit": 100},
        ):
            membership = Membership.model_validate(raw)
            if membership.course is not None:
                courses.append(membership.course)
        return courses

    def get_memberships_with_role(self) -> list[tuple[Membership, Course | None]]:
        me = self.get_me()
        out: list[tuple[Membership, Course | None]] = []
        for raw in self.paginate(
            f"/learn/api/v1/users/{me.id}/memberships",
            params={"expand": "course", "limit": 100},
        ):
            m = Membership.model_validate(raw)
            out.append((m, m.course))
        return out

    # Blackboard Ultra schovává přílohy za ?expand=fileAttachments,
    # bez něj zůstávají v listingu i detailu skryté.
    _CONTENT_EXPAND = "fileAttachments"

    def get_course_contents(self, course_id: str) -> list[ContentItem]:
        items: list[ContentItem] = []
        for raw in self.paginate(
            f"/learn/api/v1/courses/{course_id}/contents",
            params={"expand": self._CONTENT_EXPAND},
        ):
            items.append(ContentItem.model_validate(raw))
        return items

    def get_children(self, course_id: str, content_id: str) -> list[ContentItem]:
        items: list[ContentItem] = []
        for raw in self.paginate(
            f"/learn/api/v1/courses/{course_id}/contents/{content_id}/children",
            params={"expand": self._CONTENT_EXPAND},
        ):
            items.append(ContentItem.model_validate(raw))
        return items

    def get_attachments(self, course_id: str, content_id: str) -> list[Attachment]:
        items: list[Attachment] = []
        try:
            for raw in self.paginate(
                f"/learn/api/v1/courses/{course_id}/contents/{content_id}/attachments"
            ):
                items.append(Attachment.model_validate(raw))
        except BlackboardError as e:
            logger.debug("attachments pro %s vrátilo chybu: %s", content_id, e)
        return items

    def get_content_item(self, course_id: str, content_id: str) -> ContentItem:
        data = self._get_json(
            f"/learn/api/v1/courses/{course_id}/contents/{content_id}",
            params={"expand": self._CONTENT_EXPAND},
        )
        return ContentItem.model_validate(data)

    # -----------------------------
    # File downloads
    # -----------------------------

    def download_attachment(
        self, course_id: str, content_id: str, attachment_id: str, dest: Path
    ) -> int:
        """Stáhne přílohu do `dest`. Vrací počet bajtů."""
        path = (
            f"/learn/api/v1/courses/{course_id}/contents/{content_id}"
            f"/attachments/{attachment_id}/download"
        )
        return self._stream_to_file(path, dest)

    def download_url(self, url_or_path: str, dest: Path) -> int:
        """Stáhne libovolnou URL/path do `dest`."""
        return self._stream_to_file(url_or_path, dest)

    def _stream_to_file(self, path_or_url: str, dest: Path) -> int:
        r"""Streamuje URL do `dest` přes mezisoubor a atomické přejmenování.

        Cesty obalujeme `long_path()` — na Windows se `dest` v hlubokých
        output stromech s českou diakritikou snadno dostane přes MAX_PATH=260,
        a bez `\\?\` prefixu by `open()` skončil ENOENT (FileNotFoundError).
        """
        os.makedirs(long_path(dest.parent), exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        resp = self._request("GET", path_or_url, stream=True, allow_redirects=True)
        total = 0
        try:
            with open(long_path(tmp), "wb") as f:
                for chunk in resp.iter_content(chunk_size=_STREAM_CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
                        total += len(chunk)
        finally:
            resp.close()
        os.replace(long_path(tmp), long_path(dest))
        return total
