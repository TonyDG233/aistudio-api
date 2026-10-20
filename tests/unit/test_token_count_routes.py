"""Route contracts for WP11 token-counting endpoints."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from aistudio_api.api import (
    format_errors,
    routes_claude,
    routes_gemini,
    routes_responses,
)
from aistudio_api.api.dependencies import get_client
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application.token_counter import TokenCountOutcome
from aistudio_api.domain.errors import RequestError

MODEL = "gemini-3.8-flash"


def _app(*routers) -> FastAPI:
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    return app


async def _post(app: FastAPI, path: str, payload: dict[str, object]):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(path, json=payload)


def _enable_formats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(format_errors, "format_enabled", lambda _format: True)


async def test_gemini_count_tokens_route_returns_total_tokens(monkeypatch):
    _enable_formats(monkeypatch)
    captured: dict[str, object] = {}

    async def fake_count(**kwargs):
        captured.update(kwargs)
        return TokenCountOutcome(model=MODEL, total_tokens=17, surface="chat")

    monkeypatch.setattr(routes_gemini, "count_tokens", fake_count)
    app = _app(routes_gemini.router)
    app.dependency_overrides[get_client] = lambda: object()

    response = await _post(
        app,
        f"/v1beta/{MODEL}:countTokens",
        {
            "contents": [{"parts": [{"text": "hello"}]}],
            "generationConfig": {"temperature": 0.9},
        },
    )

    assert response.status_code == 200
    assert response.json() == {"totalTokens": 17}
    assert captured["api_format"] == "gemini"
    assert captured["model_path"] == MODEL


async def test_claude_count_tokens_route_returns_input_tokens(monkeypatch):
    _enable_formats(monkeypatch)
    monkeypatch.setattr(routes_claude, "get_client", lambda: object())
    captured: dict[str, object] = {}

    async def fake_count(**kwargs):
        captured.update(kwargs)
        return TokenCountOutcome(model=MODEL, total_tokens=23, surface="chat")

    monkeypatch.setattr(routes_claude, "count_prompt_tokens", fake_count)
    app = _app(routes_claude.router)

    response = await _post(
        app,
        "/v1/messages/count_tokens",
        {
            "model": "claude-3-5-sonnet",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"input_tokens": 23}
    assert captured["api_format"] == "claude"
    request = captured["req"]
    assert isinstance(request, GeminiGenerateContentRequest)
    assert request.contents[0].parts[0].text == "hello"


async def test_claude_count_tokens_route_surfaces_upstream_error(monkeypatch):
    _enable_formats(monkeypatch)
    monkeypatch.setattr(routes_claude, "get_client", lambda: object())

    async def failing_count(**kwargs):
        raise RequestError(400, "upstream rejected prompt")

    monkeypatch.setattr(routes_claude, "count_prompt_tokens", failing_count)
    app = _app(routes_claude.router)

    response = await _post(
        app,
        "/v1/messages/count_tokens",
        {
            "model": "claude-3-5-sonnet",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "error"
    assert "upstream rejected prompt" in body["error"]["message"]


async def test_responses_input_tokens_route_returns_input_tokens(monkeypatch):
    _enable_formats(monkeypatch)
    captured: dict[str, object] = {}

    async def fake_count(**kwargs):
        captured.update(kwargs)
        return TokenCountOutcome(model=MODEL, total_tokens=31, surface="chat")

    monkeypatch.setattr(routes_responses, "count_prompt_tokens", fake_count)
    app = _app(routes_responses.router)
    app.dependency_overrides[get_client] = lambda: object()

    response = await _post(
        app,
        "/v1/responses/input_tokens",
        {"model": MODEL, "input": "hello"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "object": "response.input_tokens",
        "input_tokens": 31,
    }
    assert captured["api_format"] == "responses"
    request = captured["req"]
    assert isinstance(request, GeminiGenerateContentRequest)
    assert request.contents[0].parts[0].text == "hello"
