"""Route contracts for the Gemini countTokens endpoint (P5 B5b)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from aistudio_api.api import routes_gemini
from aistudio_api.api.dependencies import get_routing_pipeline
from aistudio_api.application.token_counter import TokenCountOutcome
from tests.unit.routing_fakes import (
    FakeHandle,
    ScriptedPipeline,
    make_attempt,
    make_context,
)

MODEL = "gemini-3.8-flash"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_gemini.router)
    return app


async def _post(app: FastAPI, path: str, payload: dict[str, object]):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(path, json=payload)


async def test_gemini_count_tokens_route_returns_total_tokens(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_count(**kwargs):
        captured.update(kwargs)
        return TokenCountOutcome(model=MODEL, total_tokens=17, surface="chat")

    monkeypatch.setattr(routes_gemini, "count_tokens", fake_count)
    app = _app()
    app.dependency_overrides[get_routing_pipeline] = lambda: object()

    response = await _post(
        app,
        f"/v1beta/{MODEL}:countTokens",
        {
            "contents": [{"parts": [{"text": "hello"}]}],
            "generationConfig": {"temperature": 0.9},
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"totalTokens": 17}
    assert captured["api_format"] == "gemini"
    assert captured["model_path"] == MODEL


async def test_gemini_count_tokens_uses_pooled_client_and_returns_positive_json():
    client = MagicMock()
    client.count_tokens = AsyncMock(return_value=11)
    attempt = make_attempt(client, account_id="free-a", tier="Free")
    pipeline = ScriptedPipeline([attempt], context=make_context())
    app = _app()
    app.dependency_overrides[get_routing_pipeline] = lambda: pipeline

    response = await _post(
        app,
        f"/v1beta/models/{MODEL}:countTokens",
        {"contents": [{"parts": [{"text": "B6 count tokens"}]}]},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["totalTokens"] > 0
    assert client.count_tokens.await_count == 1
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True


async def test_gemini_count_tokens_route_is_post_only():
    app = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/v1beta/{MODEL}:countTokens")
    assert response.status_code in (404, 405)


@pytest.mark.parametrize(
    "model_path",
    ["gemini-3.8-flash", "pg/gemini-3.8-flash", "models/gemini-3.8-flash"],
)
async def test_gemini_count_tokens_route_accepts_model_paths(monkeypatch, model_path):
    seen: list[str] = []

    async def fake_count(**kwargs):
        seen.append(str(kwargs["model_path"]))
        return TokenCountOutcome(model=MODEL, total_tokens=3, surface="chat")

    monkeypatch.setattr(routes_gemini, "count_tokens", fake_count)
    app = _app()
    app.dependency_overrides[get_routing_pipeline] = lambda: object()

    response = await _post(app, f"/v1beta/{model_path}:countTokens", {"contents": []})

    assert response.status_code == 200
    assert response.json() == {"totalTokens": 3}
    assert seen == [model_path]
