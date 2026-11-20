"""Batch B: bare probe recipes must carry the site-wide Firefox identity.

These tests never touch the network; they record the headers handed to the
httpx layer by every bare probe (email map / benefit tier / account list) and
by the apps-page HTML fetch.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from aistudio_api.infrastructure.account.account_info_refresher import (
    ACCEPT_LANGUAGE,
    USER_AGENT,
    AccountInfoRefresher,
    TransportResponse,
)
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.account.cookie_parser import (
    DEFAULT_ACCEPT_LANGUAGE,
    DEFAULT_BROWSER_USER_AGENT,
    probe_account_benefit_tier,
    probe_account_emails_map,
    probe_google_accounts_infinite,
)

_FIREFOX_152 = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
)
_COOKIES = {"SID": "sid", "SAPISID": "sapisid"}
_STORAGE_STATE = {
    "cookies": [
        {"name": "SID", "value": "sid", "domain": ".google.com"},
        {"name": "SAPISID", "value": "sapisid", "domain": ".google.com"},
    ],
    "origins": [],
}


class _RecordingResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _RecordingClient:
    def __init__(
        self,
        *,
        get_bodies: list[str] | None = None,
        post_statuses: list[int] | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self._get_bodies = list(get_bodies or [])
        self._post_statuses = list(post_statuses or [])

    async def __aenter__(self) -> _RecordingClient:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    def _record(self, method: str, url: object, kwargs: dict[str, Any]) -> None:
        self.calls.append((method, str(url), dict(kwargs.get("headers") or {})))

    async def get(self, url: object, **kwargs: Any) -> _RecordingResponse:
        self._record("GET", url, kwargs)
        body = self._get_bodies.pop(0) if self._get_bodies else ""
        return _RecordingResponse(200, body)

    async def post(self, url: object, **kwargs: Any) -> _RecordingResponse:
        self._record("POST", url, kwargs)
        status = self._post_statuses.pop(0) if self._post_statuses else 200
        return _RecordingResponse(status, "[]")


def _assert_firefox_identity(headers: dict[str, str]) -> None:
    assert headers["User-Agent"] == _FIREFOX_152
    assert headers["accept-language"] == "en-US,en;q=0.9"


def test_shared_identity_constants_are_firefox_152() -> None:
    assert DEFAULT_BROWSER_USER_AGENT == _FIREFOX_152
    assert DEFAULT_ACCEPT_LANGUAGE == "en-US,en;q=0.9"
    assert USER_AGENT == _FIREFOX_152
    assert ACCEPT_LANGUAGE == "en-US,en;q=0.9"


@pytest.mark.asyncio
async def test_probe_account_emails_map_uses_firefox_identity() -> None:
    client = _RecordingClient()

    await probe_account_emails_map(_COOKIES, auth_user="0", client=cast(Any, client))

    assert client.calls
    for _method, _url, headers in client.calls:
        _assert_firefox_identity(headers)
        assert "Chrome/" not in headers["User-Agent"]


@pytest.mark.asyncio
async def test_probe_account_benefit_tier_sends_firefox_identity(monkeypatch) -> None:
    client = _RecordingClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client)

    assert await probe_account_benefit_tier(_COOKIES, auth_user="0") == "Free"

    assert len(client.calls) == 1
    method, _url, headers = client.calls[0]
    assert method == "POST"
    _assert_firefox_identity(headers)
    assert "python-httpx" not in headers["User-Agent"]


@pytest.mark.asyncio
async def test_probe_google_accounts_infinite_sends_firefox_identity(
    monkeypatch,
) -> None:
    client = _RecordingClient(
        post_statuses=[
            200,  # ListModels: authenticated
            200,  # BenefitTier
            401,  # next ListModels: no such user
        ]
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client)

    accounts = await probe_google_accounts_infinite(_STORAGE_STATE)

    assert len(accounts) == 1
    assert client.calls
    for _method, _url, headers in client.calls:
        _assert_firefox_identity(headers)


class _RecordingTransport:
    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        content: str = "",
    ) -> TransportResponse:
        self.headers.append(dict(headers))
        return TransportResponse(status=200, body="<html></html>")


@pytest.mark.asyncio
async def test_fetch_app_html_uses_firefox_identity(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts")
    transport = _RecordingTransport()
    refresher = AccountInfoRefresher(store, transport=transport)

    await refresher._fetch_app_html(
        cast(Any, SimpleNamespace(header="SID=sid")), auth_user="0"
    )

    assert len(transport.headers) == 1
    headers = transport.headers[0]
    assert headers["user-agent"] == _FIREFOX_152
    assert headers["accept-language"] == "en-US,en;q=0.9"
