"""Cookie persistence must keep account-scoped auth cookies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from aistudio_api.infrastructure.gateway.session_auth import (
    load_cookies_to_page,
    save_browser_cookies,
)


def _cookie(
    name: str,
    value: str,
    domain: str,
    *,
    path: str = "/",
    expires: int = 4102444800,
) -> dict[str, object]:
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": path,
        "expires": expires,
        "httpOnly": True,
        "secure": True,
        "sameSite": "None",
    }


class FakeCDP:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def send(self, method: str, params: object = None) -> None:
        self.calls.append(method)


class FakePage:
    def __init__(self, cookies: list[dict[str, object]] | None = None) -> None:
        self.cdp = FakeCDP()
        self._cookies = cookies or []
        self.injected: list[list[dict[str, object]]] = []

    async def set_cookies(self, cookies: list[dict[str, object]]) -> None:
        self.injected.append(cookies)

    async def get_cookies(self) -> list[dict[str, object]]:
        return self._cookies

    def is_closed(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_load_cookies_keeps_account_and_osid_cookies(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    cookies = [
        _cookie("SID", "root", ".google.com"),
        _cookie("SAPISID", "sapisid", ".google.com"),
        _cookie("LSID", "lsid", "accounts.google.com"),
        _cookie("ACCOUNT_CHOOSER", "chooser", "accounts.google.com"),
        _cookie("OSID", "osid", "console.cloud.google.com"),
        _cookie("__Secure-OSID", "secure-osid", "console.cloud.google.com"),
        _cookie("SIDCC", "sidcc", ".google.com"),
    ]
    auth_file.write_text(json.dumps({"cookies": cookies}), encoding="utf-8")
    page: Any = FakePage()

    assert await load_cookies_to_page(page, str(auth_file)) is True

    injected = page.injected[-1]
    names = {cookie["name"] for cookie in injected}
    assert names == {cookie["name"] for cookie in cookies}
    assert {"LSID", "ACCOUNT_CHOOSER", "OSID", "__Secure-OSID", "SIDCC"} <= names
    assert "Network.clearBrowserCookies" in page.cdp.calls


@pytest.mark.asyncio
async def test_save_cookies_merges_missing_account_cookies(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "cookies": [
                    _cookie("SID", "old", ".google.com"),
                    _cookie("LSID", "lsid", "accounts.google.com"),
                    _cookie("OSID", "osid", "console.cloud.google.com"),
                    _cookie("STALE", "gone", ".google.com", expires=1),
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )
    page: Any = FakePage(
        cookies=[
            _cookie("SID", "new", ".google.com"),
            _cookie("SAPISID", "sapisid", ".google.com"),
        ]
    )

    assert await save_browser_cookies(page, str(auth_file)) is True

    stored = json.loads(auth_file.read_text(encoding="utf-8"))
    by_name = {
        cookie["name"]: cookie["value"]
        for cookie in stored["cookies"]
        if isinstance(cookie, dict)
    }
    assert by_name["SID"] == "new"
    assert by_name["SAPISID"] == "sapisid"
    assert by_name["LSID"] == "lsid"
    assert by_name["OSID"] == "osid"
    assert "STALE" not in by_name


@pytest.mark.asyncio
async def test_save_cookies_preserves_cross_domain_twins(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "cookies": [
                    _cookie("LSID", "account", "accounts.google.com"),
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )
    page: Any = FakePage(
        cookies=[
            _cookie("LSID", "root", ".google.com"),
        ]
    )

    assert await save_browser_cookies(page, str(auth_file)) is True

    stored = json.loads(auth_file.read_text(encoding="utf-8"))
    values = {
        (cookie["name"], cookie["domain"]): cookie["value"]
        for cookie in stored["cookies"]
        if isinstance(cookie, dict)
    }
    assert values[("LSID", "accounts.google.com")] == "account"
    assert values[("LSID", ".google.com")] == "root"
