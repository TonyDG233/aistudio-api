"""API Key 与 Web 管理页鉴权分离（D-14）。"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request

from aistudio_api.api import dependencies as dependencies_module
from aistudio_api.api.dependencies import (
    require_api_key,
    require_api_key_or_web_session,
    require_web_auth,
)
from aistudio_api.config import settings


def _build_api_client() -> httpx.AsyncClient:
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(require_api_key)])
    async def protected():
        return {"ok": True}

    @app.get("/read", dependencies=[Depends(require_api_key_or_web_session)])
    async def read():
        return {"ok": True}

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _mock_request(*, headers: dict[str, str] | None = None) -> MagicMock:
    request = MagicMock(spec=Request)
    request.method = "GET"
    request.headers = headers or {}
    request.query_params = {}
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    return request


@pytest.mark.asyncio
async def test_empty_api_keys_and_anonymous_disabled_returns_401(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    async with _build_api_client() as client:
        response = await client.get("/protected")

    assert response.status_code == 401
    assert "未配置 API Key" in response.json()["detail"]["message"]
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_read_only_dependency_empty_api_keys_and_no_web_session_returns_401(
    monkeypatch,
):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    monkeypatch.setattr(settings, "web_password", None)

    async with _build_api_client() as client:
        response = await client.get("/read")

    assert response.status_code == 401
    assert "未配置 API Key" in response.json()["detail"]["message"]


@pytest.mark.asyncio
async def test_read_only_dependency_accepts_web_session(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    monkeypatch.setattr(settings, "web_password", "admin-pass")

    async with _build_api_client() as client:
        response = await client.get(
            "/read", headers={"Authorization": "Bearer admin-pass"}
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_read_only_dependency_rejects_invalid_web_session(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"secret-token"}))
    monkeypatch.setattr(settings, "web_password", "admin-pass")

    async with _build_api_client() as client:
        response = await client.get(
            "/read", headers={"Authorization": "Bearer not-the-password"}
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_read_only_dependency_still_accepts_api_key(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"secret-token"}))
    monkeypatch.setattr(settings, "web_password", "admin-pass")

    async with _build_api_client() as client:
        response = await client.get(
            "/read", headers={"Authorization": "Bearer secret-token"}
        )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_anonymous_switch_allows_request_without_key(monkeypatch):
    monkeypatch.setattr(
        "aistudio_api.infrastructure.gateway.model_defaults.get_configured_security_settings",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(settings, "api_keys", frozenset())

    async with _build_api_client() as client:
        response = await client.get("/protected")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"headers": {"Authorization": "Bearer secret-token"}},
        {"headers": {"X-API-Key": "secret-token"}},
        {"headers": {"x-goog-api-key": "secret-token"}},
    ],
)
async def test_valid_api_key_is_accepted(monkeypatch, kwargs):
    monkeypatch.setattr(settings, "api_keys", frozenset({"secret-token"}))
    async with _build_api_client() as client:
        response = await client.get("/protected", **kwargs)

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_query_param_key_is_accepted(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"secret-token"}))
    async with _build_api_client() as client:
        response = await client.get("/protected?key=secret-token")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_missing_api_key_returns_401(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"secret-token"}))
    async with _build_api_client() as client:
        response = await client.get("/protected")

    assert response.status_code == 401
    assert response.json()["detail"]["type"] == "authentication_error"


@pytest.mark.asyncio
async def test_invalid_api_key_returns_401(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"secret-token"}))
    async with _build_api_client() as client:
        response = await client.get("/protected?key=wrong-token")

    assert response.status_code == 401


def test_web_password_is_not_accepted_as_api_key(monkeypatch):
    monkeypatch.setattr(settings, "web_password", "super_secret_admin_pass")
    monkeypatch.setattr(settings, "api_keys", frozenset({"key123"}))

    request = _mock_request(headers={"x-api-key": "super_secret_admin_pass"})
    with pytest.raises(HTTPException) as exc_info:
        require_api_key(request)
    assert exc_info.value.status_code == 401


def test_api_key_cannot_unlock_web_console(monkeypatch):
    monkeypatch.setattr(settings, "web_password", "super_secret_admin_pass")
    monkeypatch.setattr(settings, "api_keys", frozenset({"key123"}))

    request = _mock_request(headers={"x-api-key": "key123"})
    with pytest.raises(HTTPException) as exc_info:
        require_web_auth(request)
    assert exc_info.value.status_code == 401


def test_web_console_empty_password_stays_open(monkeypatch):
    monkeypatch.setattr(settings, "web_password", "")

    require_web_auth(_mock_request())


def test_web_console_accepts_only_its_own_password(monkeypatch):
    monkeypatch.setattr(settings, "web_password", "super_secret_admin_pass")

    require_web_auth(
        _mock_request(headers={"authorization": "Bearer super_secret_admin_pass"})
    )

    with pytest.raises(HTTPException) as exc_info:
        require_web_auth(_mock_request(headers={"authorization": "Bearer wrong"}))
    assert exc_info.value.status_code == 401


def test_require_web_auth_rejects_html_accept_spoofing(monkeypatch):
    monkeypatch.setattr(settings, "web_password", "super_secret_admin_pass")

    request = _mock_request()
    request.headers = {"accept": "text/html,application/xhtml+xml"}
    with pytest.raises(HTTPException) as exc_info:
        require_web_auth(request)
    assert exc_info.value.status_code == 401


def _dedup_request(
    *,
    client_ip: str = "127.0.0.1",
    path: str = "/v1beta/models",
    user_agent: str = "test-agent",
    referer: str = "https://example.com/page",
) -> MagicMock:
    request = MagicMock(spec=Request)
    request.method = "GET"
    request.headers = {"user-agent": user_agent, "referer": referer}
    request.query_params = {}
    request.client = MagicMock()
    request.client.host = client_ip
    request.scope = {"path": path}
    return request


@pytest.fixture
def auth_dedup_state(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(dependencies_module, "_missing_key_warn_last", {})
    monkeypatch.setattr(dependencies_module, "_missing_key_warn_suppressed", {})
    monkeypatch.setattr(dependencies_module, "_monotonic", lambda: clock["now"])
    monkeypatch.setattr(settings, "api_keys", frozenset({"secret-token"}))
    monkeypatch.setattr(settings, "web_password", None)
    return clock


def _auth_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "aistudio.auth"]


def test_read_only_missing_key_warning_logs_sanitized_ua_and_referer(
    caplog, auth_dedup_state
):
    caplog.set_level(logging.DEBUG, logger="aistudio.auth")
    long_ua = "U" * 100
    request = _dedup_request(
        user_agent=long_ua,
        referer="https://user:pass@example.com/admin?api_key=topsecret#fragment",
    )

    with pytest.raises(HTTPException) as exc_info:
        require_api_key_or_web_session(request)

    assert exc_info.value.status_code == 401
    records = _auth_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    assert "U" * 80 in message
    assert "U" * 81 not in message
    assert "https://example.com/admin" in message
    assert "topsecret" not in message
    assert "user:pass" not in message
    assert "fragment" not in message


def test_read_only_missing_key_repeats_are_debug_within_window(
    caplog, auth_dedup_state
):
    caplog.set_level(logging.DEBUG, logger="aistudio.auth")
    request = _dedup_request()

    for _ in range(3):
        with pytest.raises(HTTPException):
            require_api_key_or_web_session(request)

    records = _auth_records(caplog)
    warnings = [r for r in records if r.levelno == logging.WARNING]
    debugs = [r for r in records if r.levelno == logging.DEBUG]
    assert len(warnings) == 1
    assert len(debugs) == 2
    assert all("已抑制" in record.getMessage() for record in debugs)


def test_read_only_missing_key_warns_again_after_window(caplog, auth_dedup_state):
    caplog.set_level(logging.DEBUG, logger="aistudio.auth")
    request = _dedup_request()

    with pytest.raises(HTTPException):
        require_api_key_or_web_session(request)
    auth_dedup_state["now"] += 299
    with pytest.raises(HTTPException):
        require_api_key_or_web_session(request)
    auth_dedup_state["now"] += 2
    with pytest.raises(HTTPException):
        require_api_key_or_web_session(request)

    warnings = [
        r for r in _auth_records(caplog) if r.levelno == logging.WARNING
    ]
    assert len(warnings) == 2


def test_read_only_missing_key_dedup_is_per_ip_and_path(caplog, auth_dedup_state):
    caplog.set_level(logging.DEBUG, logger="aistudio.auth")

    first = _dedup_request(client_ip="10.0.0.1", path="/v1beta/models")
    same_path_other_ip = _dedup_request(client_ip="10.0.0.2", path="/v1beta/models")
    other_path_same_ip = _dedup_request(client_ip="10.0.0.1", path="/v1/models")

    for request in (first, first, same_path_other_ip, other_path_same_ip):
        with pytest.raises(HTTPException):
            require_api_key_or_web_session(request)

    warnings = [
        r for r in _auth_records(caplog) if r.levelno == logging.WARNING
    ]
    assert len(warnings) == 3


def test_missing_key_state_hard_cap_evicts_oldest_entries(auth_dedup_state):
    """1024 上限是硬上限：窗口内大量唯一键按最旧时间戳确定性淘汰。"""
    for index in range(1100):
        auth_dedup_state["now"] = 1000.0 + index * 0.01
        assert dependencies_module._missing_key_warning_allowed(
            f"10.0.0.{index}", "/v1/models"
        )

    state = dependencies_module._missing_key_warn_last
    assert len(state) == dependencies_module._MISSING_KEY_STATE_LIMIT
    assert ("10.0.0.0", "/v1/models") not in state
    assert ("10.0.0.75", "/v1/models") not in state
    assert ("10.0.0.76", "/v1/models") in state
    assert ("10.0.0.1099", "/v1/models") in state


def test_generation_endpoint_missing_key_is_not_deduplicated(
    caplog, auth_dedup_state
):
    caplog.set_level(logging.DEBUG, logger="aistudio.auth")
    request = _dedup_request(path="/v1beta/models/gemini-3.7-flash:generateContent")

    for _ in range(2):
        with pytest.raises(HTTPException):
            require_api_key(request)

    warnings = [
        r for r in _auth_records(caplog) if r.levelno == logging.WARNING
    ]
    assert len(warnings) == 2


def test_invalid_key_warning_logs_context_without_dedup(caplog, auth_dedup_state):
    caplog.set_level(logging.DEBUG, logger="aistudio.auth")
    request = _dedup_request(user_agent="invalid-key-agent")
    request.headers["x-api-key"] = "wrong-token"

    for _ in range(2):
        with pytest.raises(HTTPException):
            require_api_key_or_web_session(request)

    warnings = [
        r for r in _auth_records(caplog) if r.levelno == logging.WARNING
    ]
    assert len(warnings) == 2
    message = warnings[0].getMessage()
    assert "invalid-key-agent" in message
    assert "wrong-token" not in message
