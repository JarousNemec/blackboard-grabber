from __future__ import annotations

from pathlib import Path

import pytest
import responses

from bb_backup.client import AuthError, BlackboardClient, BlackboardError
from bb_backup.config import Config


def _make_config(tmp_path: Path) -> Config:
    cookies_file = tmp_path / "cookies.txt"
    # Minimální Netscape cookies header so MozillaCookieJar.load() succeeds.
    cookies_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f"""
[blackboard]
base_url = "https://bb.example.com"
cookies_file = "{cookies_file.name}"
[download]
request_delay_ms = 0
max_retries = 2
http_timeout_s = 5
""",
        encoding="utf-8",
    )
    from bb_backup.config import load_config

    import os

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        return load_config()
    finally:
        os.chdir(cwd)


@responses.activate
def test_get_me(tmp_path):
    cfg = _make_config(tmp_path)
    responses.add(
        responses.GET,
        "https://bb.example.com/learn/api/v1/users/me",
        json={"id": "_42_1", "userName": "testuser"},
        status=200,
    )
    client = BlackboardClient(cfg)
    me = client.get_me()
    assert me.id == "_42_1"
    assert me.userName == "testuser"


@responses.activate
def test_paginate_two_pages(tmp_path):
    cfg = _make_config(tmp_path)
    responses.add(
        responses.GET,
        "https://bb.example.com/learn/api/v1/users/me",
        json={"id": "_42_1"},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://bb.example.com/learn/api/v1/users/_42_1/memberships",
        json={
            "results": [
                {"id": "m1", "courseId": "_1_1", "course": {"id": "_1_1", "courseId": "AI", "name": "Aplikovaná informatika"}},
            ],
            "paging": {"nextPage": "/learn/api/v1/users/_42_1/memberships?offset=1"},
        },
        status=200,
    )
    responses.add(
        responses.GET,
        "https://bb.example.com/learn/api/v1/users/_42_1/memberships",
        json={
            "results": [
                {"id": "m2", "courseId": "_2_1", "course": {"id": "_2_1", "courseId": "DB", "name": "Databáze"}},
            ],
            "paging": {"nextPage": None},
        },
        status=200,
    )
    client = BlackboardClient(cfg)
    courses = client.get_my_courses()
    assert [c.courseId for c in courses] == ["AI", "DB"]
    assert courses[1].name == "Databáze"


@responses.activate
def test_auth_error_on_401(tmp_path):
    cfg = _make_config(tmp_path)
    responses.add(
        responses.GET,
        "https://bb.example.com/learn/api/v1/users/me",
        json={"error": "unauthorized"},
        status=401,
    )
    client = BlackboardClient(cfg)
    with pytest.raises(AuthError):
        client.get_me()


@responses.activate
def test_500_retried_then_succeeds(tmp_path):
    cfg = _make_config(tmp_path)
    responses.add(
        responses.GET,
        "https://bb.example.com/learn/api/v1/users/me",
        json={"oops": True},
        status=500,
    )
    responses.add(
        responses.GET,
        "https://bb.example.com/learn/api/v1/users/me",
        json={"id": "_42_1"},
        status=200,
    )
    client = BlackboardClient(cfg)
    me = client.get_me()
    assert me.id == "_42_1"


@responses.activate
def test_500_retries_exhausted(tmp_path):
    cfg = _make_config(tmp_path)
    responses.add(
        responses.GET,
        "https://bb.example.com/learn/api/v1/users/me",
        status=500,
    )
    responses.add(
        responses.GET,
        "https://bb.example.com/learn/api/v1/users/me",
        status=500,
    )
    client = BlackboardClient(cfg)
    with pytest.raises(BlackboardError):
        client.get_me()


@responses.activate
def test_paginate_empty(tmp_path):
    cfg = _make_config(tmp_path)
    responses.add(
        responses.GET,
        "https://bb.example.com/learn/api/v1/users/me",
        json={"id": "_42_1"},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://bb.example.com/learn/api/v1/users/_42_1/memberships",
        json={"results": [], "paging": {"nextPage": None}},
        status=200,
    )
    client = BlackboardClient(cfg)
    assert client.get_my_courses() == []


@responses.activate
def test_paginate_relative_next_page(tmp_path):
    """nextPage může být relativní path bez hostu — paginate musí přidat base_url."""
    cfg = _make_config(tmp_path)
    responses.add(
        responses.GET,
        "https://bb.example.com/learn/api/v1/users/me",
        json={"id": "_42_1"},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://bb.example.com/learn/api/v1/users/_42_1/memberships",
        json={
            "results": [{"id": "m1", "courseId": "_1_1"}],
            "paging": {"nextPage": "/learn/api/v1/users/_42_1/memberships?offset=1"},
        },
        status=200,
    )
    responses.add(
        responses.GET,
        "https://bb.example.com/learn/api/v1/users/_42_1/memberships",
        json={"results": [], "paging": {"nextPage": None}},
        status=200,
    )
    client = BlackboardClient(cfg)
    items = list(
        client.paginate(
            f"/learn/api/v1/users/_42_1/memberships",
            params={"limit": 1},
        )
    )
    assert len(items) == 1
    assert items[0]["courseId"] == "_1_1"
