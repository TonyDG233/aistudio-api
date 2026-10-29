"""API Key 与 Web 管理页鉴权分离（D-14）。"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request

from aistudio_api.api.dependencies import require_api_key, require_web_auth
from aistudio_api.config import settings


def _build_api_client() -> httpx.AsyncClient:
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(require_api_key)])
    async def protected():
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
