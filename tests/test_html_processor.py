from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bb_backup.html_processor import (
    PLACEHOLDER,
    _filename_from_url,
    _replace_placeholders,
    process_html,
)


class FakeClient:
    """Minimal stub klient pro testy bez sítě."""

    def __init__(self, base_url: str = "https://bb.example.com") -> None:
        self.base_url = base_url
        self.calls: list[str] = []
        # Mapování url -> bytes (None == 404)
        self.payloads: dict[str, bytes | None] = {}

    def download_url(self, url: str, dest: Path) -> int:
        self.calls.append(url)
        payload = self.payloads.get(url)
        if payload is None:
            from bb_backup.client import BlackboardError

            raise BlackboardError(f"404 na {url}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return len(payload)


def test_replace_placeholders():
    body = f'<img src="{PLACEHOLDER}/bbcswebdav/xid-1_1">'
    out = _replace_placeholders(body, "https://bb.example.com")
    assert PLACEHOLDER not in out
    assert "https://bb.example.com/bbcswebdav/xid-1_1" in out


def test_replace_placeholders_strips_trailing_slash():
    out = _replace_placeholders(f"{PLACEHOLDER}/x", "https://bb.example.com/")
    assert out == "https://bb.example.com/x"


def test_filename_from_url_simple():
    assert _filename_from_url("https://bb/x/abc/myfile.pdf") == "myfile.pdf"


def test_filename_from_url_xid_returns_none():
    """xid-12345 není smysluplné jméno, fallback potřeba."""
    assert _filename_from_url("https://bb/bbcswebdav/xid-12345_1") is None


def test_filename_from_url_encoded():
    assert _filename_from_url("https://bb/x/p%C5%99edn%C3%A1%C5%A1ka.pdf") == "přednáška.pdf"


def test_process_html_embedded_image(tmp_path: Path):
    body = f'<p>Tady obrázek:</p><img src="{PLACEHOLDER}/bbcswebdav/xid-99_1" alt="Diagram">'
    client = FakeClient()
    client.payloads["https://bb.example.com/bbcswebdav/xid-99_1"] = b"\x89PNG\r\n\x1a\n"

    out = process_html(body, client, "_c_1", "_n_1", tmp_path)

    # Asset stažen
    assets = list((tmp_path / "_assets").iterdir())
    assert len(assets) == 1

    # src je přepsaný na relativní cestu
    assert "_assets/" in out
    assert "@X@" not in out
    assert "xid-99_1" not in out  # už není odkaz na původní URL


def test_process_html_multiple_attachments_same_url_dedupe(tmp_path: Path):
    body = (
        f'<img src="{PLACEHOLDER}/bbcswebdav/xid-99_1">'
        f'<a href="{PLACEHOLDER}/bbcswebdav/xid-99_1">link</a>'
    )
    client = FakeClient()
    client.payloads["https://bb.example.com/bbcswebdav/xid-99_1"] = b"data"

    process_html(body, client, "_c_1", "_n_1", tmp_path)

    # Jen jeden download i když je odkaz na 2 místech
    assert len(client.calls) == 1
    assert len(list((tmp_path / "_assets").iterdir())) == 1


def test_process_html_404_keeps_html_intact(tmp_path: Path):
    body = f'<img src="{PLACEHOLDER}/bbcswebdav/xid-missing">'
    client = FakeClient()
    # No payload registered → BlackboardError při downloadu

    out = process_html(body, client, "_c_1", "_n_1", tmp_path)

    # Žádný asset nestažen, ale HTML vrátí (nepadá)
    assets_dir = tmp_path / "_assets"
    if assets_dir.exists():
        assert list(assets_dir.iterdir()) == []
    # src zůstává původní (po placeholder replace) — broken link, ale ne crash
    assert "https://bb.example.com/bbcswebdav/xid-missing" in out


def test_process_html_anchor_with_filename(tmp_path: Path):
    body = (
        f'<a href="{PLACEHOLDER}/bbcswebdav/courses/c1/content/lecture.pdf">PDF</a>'
    )
    client = FakeClient()
    client.payloads[
        "https://bb.example.com/bbcswebdav/courses/c1/content/lecture.pdf"
    ] = b"%PDF-1.4"

    out = process_html(body, client, "_c_1", "_n_1", tmp_path)

    assets = list((tmp_path / "_assets").iterdir())
    assert len(assets) == 1
    assert assets[0].name == "lecture.pdf"
    assert "_assets/lecture.pdf" in out


def test_process_html_no_embeds_passthrough(tmp_path: Path):
    body = "<p>Pure text bez embedů.</p>"
    client = FakeClient()
    out = process_html(body, client, "_c_1", "_n_1", tmp_path)
    assert "<p>Pure text bez embedů.</p>" in out
    assert client.calls == []


def test_process_html_filename_collision(tmp_path: Path):
    body = (
        f'<img src="{PLACEHOLDER}/bbcswebdav/a/img.png">'
        f'<img src="{PLACEHOLDER}/bbcswebdav/b/img.png">'
    )
    client = FakeClient()
    client.payloads["https://bb.example.com/bbcswebdav/a/img.png"] = b"A"
    client.payloads["https://bb.example.com/bbcswebdav/b/img.png"] = b"B"

    process_html(body, client, "_c_1", "_n_1", tmp_path)

    names = sorted(p.name for p in (tmp_path / "_assets").iterdir())
    assert "img.png" in names
    assert "img_2.png" in names


def test_process_html_video_source_tag(tmp_path: Path):
    body = (
        f'<video><source src="{PLACEHOLDER}/bbcswebdav/xid-vid_1"></video>'
    )
    client = FakeClient()
    client.payloads["https://bb.example.com/bbcswebdav/xid-vid_1"] = b"VID"
    out = process_html(body, client, "_c_1", "_n_1", tmp_path)
    assert "_assets/" in out
