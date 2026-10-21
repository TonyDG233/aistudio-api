"""storage-state 导入不得压平跨域 cookie。"""

from __future__ import annotations

from aistudio_api.infrastructure.account.cookie_parser import (
    cookies_for_google_rpc,
    parse_cookie_string,
)


def _cookie(name: str, value: str, domain: str, path: str = "/") -> dict[str, object]:
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": path,
        "expires": 4102444800,
        "httpOnly": True,
        "secure": True,
        "sameSite": "None",
    }


def test_parse_cookie_string_preserves_cross_domain_cookies():
    raw = {
        "cookies": [
            _cookie("SID", "root-value", ".google.com"),
            _cookie("SID", "account-value", "accounts.google.com"),
            _cookie("SAPISID", "sapisid", ".google.com"),
        ],
        "origins": [],
    }

    state = parse_cookie_string(raw)

    cookies = state["cookies"]
    assert isinstance(cookies, list)
    assert len(cookies) == 3
    sid_values = {
        cookie["value"] for cookie in cookies if cookie["name"] == "SID"
    }
    assert sid_values == {"root-value", "account-value"}


def test_rpc_cookies_only_use_primary_google_domain():
    raw = {
        "cookies": [
            _cookie("SID", "root-value", ".google.com"),
            _cookie("SID", "account-value", "accounts.google.com"),
            _cookie("SAPISID", "sapisid", ".google.com"),
            _cookie("OSID", "studio", ".aistudio.google.com"),
        ]
    }

    jar = cookies_for_google_rpc(raw)

    assert jar == {"SID": "root-value", "SAPISID": "sapisid"}
