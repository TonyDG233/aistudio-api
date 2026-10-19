"""App-level error-shape routing for the OpenAI / Claude format faces."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from aistudio_api.api import format_errors
from aistudio_api.api.app import app
from aistudio_api.api.state import runtime_state
from aistudio_api.config import settings


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.fixture(autouse=True)
def _runtime_client(monkeypatch):
    monkeypatch.setattr(runtime_state, "client", MagicMock())


@pytest.mark.asyncio
async def test_openai_missing_key_uses_openai_error_shape(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"secret"}))
    async with _client() as client:
        response = await client.post("/v1/chat/completions", json={})
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["type"] == "authentication_error"
    assert "error" in body


@pytest.mark.asyncio
async def test_claude_missing_key_uses_claude_error_shape(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"secret"}))
    async with _client() as client:
        response = await client.post("/v1/messages", json={})
    assert response.status_code == 401
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "authentication_error"


@pytest.mark.asyncio
async def test_gemini_missing_key_keeps_gemini_error_shape(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"secret"}))
    async with _client() as client:
        response = await client.get("/v1beta/models")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == 401
    assert body["error"]["status"] == "UNAUTHENTICATED"


@pytest.mark.asyncio
async def test_openai_disabled_format_returns_403_shape(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    monkeypatch.setattr(format_errors, "format_enabled", lambda _fmt: False)
    async with _client() as client:
        response = await client.post("/v1/chat/completions", json={})
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["type"] == "permission_error"
    assert "features.formats.openai" in body["error"]["message"]


@pytest.mark.asyncio
async def test_claude_disabled_format_returns_403_shape(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    monkeypatch.setattr(format_errors, "format_enabled", lambda _fmt: False)
    async with _client() as client:
        response = await client.post("/v1/messages", json={})
    assert response.status_code == 403
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "permission_error"
    assert "features.formats.claude" in body["error"]["message"]


@pytest.mark.asyncio
async def test_openai_validation_error_returns_400_shape(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    monkeypatch.setattr(format_errors, "format_enabled", lambda _fmt: True)
    async with _client() as client:
        response = await client.post("/v1/chat/completions", json={})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["param"] is not None


@pytest.mark.asyncio
async def test_claude_validation_error_returns_400_shape(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    monkeypatch.setattr(format_errors, "format_enabled", lambda _fmt: True)
    async with _client() as client:
        response = await client.post("/v1/messages", json={})
    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
