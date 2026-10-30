"""Gemini routes must surface domain tier/routing errors before streaming."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.api.dependencies import get_routing_pipeline
from aistudio_api.application.atg.extractor import SyntheticRequiredError
from aistudio_api.config import settings
from aistudio_api.domain.errors import (
    RequestError,
    SurfaceCapacityError,
    TierRequiredError,
)
from aistudio_api.domain.models import Candidate, ModelOutput
from tests.unit.routing_fakes import (
    FakeHandle,
    ScriptedPipeline,
    make_attempt,
    make_context,
)

_HEADERS = {"x-goog-api-key": "test-key"}
_BODY = {
    "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
}
_PG_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "pg"


def _pg_fixture(name: str) -> dict[str, Any]:
    return json.loads((_PG_FIXTURES / name).read_text(encoding="utf-8"))


class _RaisingPipeline:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def begin(self, *, model_path: str, requested_surface: str | None = None):
        del model_path, requested_surface
        raise self._exc


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"test-key"}))
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_tier_required_maps_to_http_403_with_fields() -> None:
    app.dependency_overrides[get_routing_pipeline] = lambda: _RaisingPipeline(
        TierRequiredError(
            model="gemini-4-argon",
            tier="Pro",
            required_tier="Pro",
            current_tier="Free",
        )
    )
    async with _client() as client:
        response = await client.post(
            "/v1beta/gemini-4-argon:generateContent",
            headers=_HEADERS,
            json=_BODY,
        )
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["status"] == "PERMISSION_DENIED"
    assert error["tier"] == "Pro"
    assert error["required_tier"] == "Pro"
    assert error["current_tier"] == "Free"
    assert error["model"] == "gemini-4-argon"


@pytest.mark.asyncio
async def test_capacity_maps_to_http_503_with_code() -> None:
    app.dependency_overrides[get_routing_pipeline] = lambda: _RaisingPipeline(
        SurfaceCapacityError(surface="chat")
    )
    async with _client() as client:
        response = await client.post(
            "/v1beta/gemini-3.7-flash:generateContent",
            headers=_HEADERS,
            json=_BODY,
        )
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["status"] == "UNAVAILABLE"
    assert error["type"] == "surface_quota_exceeded"


@pytest.mark.asyncio
async def test_synthetic_required_non_stream_maps_to_http_502_json() -> None:
    app.dependency_overrides[get_routing_pipeline] = lambda: _RaisingPipeline(
        SyntheticRequiredError("synthetic tool call required")
    )
    async with _client() as client:
        response = await client.post(
            "/v1beta/gemini-3.7-flash:generateContent",
            headers=_HEADERS,
            json=_BODY,
        )
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == 502
    assert error["status"] == "UNAVAILABLE"
    assert "synthetic tool call required" in error["message"]


@pytest.mark.asyncio
async def test_synthetic_required_pre_stream_maps_to_http_502_json() -> None:
    app.dependency_overrides[get_routing_pipeline] = lambda: _RaisingPipeline(
        SyntheticRequiredError("synthetic tool call required")
    )
    async with _client() as client:
        response = await client.post(
            "/v1beta/gemini-3.7-flash:streamGenerateContent",
            headers=_HEADERS,
            json=_BODY,
        )
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == 502
    assert error["status"] == "UNAVAILABLE"
    assert "synthetic tool call required" in error["message"]


@pytest.mark.asyncio
async def test_non_streaming_success_uses_pipeline_instance() -> None:
    fake_client = MagicMock()
    fake_client.generate_content = AsyncMock(
        return_value=ModelOutput(
            candidates=[Candidate(text="hello from instance")],
            usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        )
    )
    attempt = make_attempt(fake_client)
    pipeline = ScriptedPipeline([attempt], context=make_context())
    app.dependency_overrides[get_routing_pipeline] = lambda: pipeline

    async with _client() as client:
        response = await client.post(
            "/v1beta/gemini-3.7-flash:generateContent",
            headers=_HEADERS,
            json=_BODY,
        )
    assert response.status_code == 200
    data = response.json()
    assert data["candidates"][0]["content"]["parts"][0]["text"] == "hello from instance"
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True


@pytest.mark.asyncio
async def test_streaming_success_uses_pipeline_instance() -> None:
    async def fake_stream(*_args, **_kwargs):
        yield ("body", "streamed")
        yield ("finish_reason", 1)
        yield ("usage", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    fake_client = MagicMock()
    fake_client.stream_generate_content = fake_stream
    attempt = make_attempt(fake_client)
    pipeline = ScriptedPipeline([attempt], context=make_context())
    app.dependency_overrides[get_routing_pipeline] = lambda: pipeline

    async with _client() as client:
        response = await client.post(
            "/v1beta/gemini-3.7-flash:streamGenerateContent",
            headers=_HEADERS,
            json=_BODY,
        )
    assert response.status_code == 200
    assert "streamed" in response.text
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True



@pytest.mark.asyncio
async def test_pg02_prestream_failure_returns_real_http_status_not_sse() -> None:
    fixture = _pg_fixture("f02_prestream_400.json")
    upstream = fixture["upstream"]
    expected = fixture["expected"]
    assert isinstance(upstream, dict)
    assert isinstance(expected, dict)
    error_body = upstream["error"]
    assert isinstance(error_body, dict)

    async def failing_stream(*_args, **_kwargs):
        raise RequestError(int(upstream["status"]), str(error_body["message"]))
        yield ("body", "never")  # pragma: no cover

    fake_client = MagicMock()
    fake_client.stream_generate_content = failing_stream
    attempt = make_attempt(fake_client)
    pipeline = ScriptedPipeline([attempt], context=make_context())
    app.dependency_overrides[get_routing_pipeline] = lambda: pipeline

    async with _client() as client:
        response = await client.post(
            "/v1beta/gemini-3.7-flash:streamGenerateContent",
            headers=_HEADERS,
            json=_BODY,
        )

    assert response.status_code == expected["http_status"]
    content_type = response.headers["content-type"]
    assert content_type.startswith(expected["content_type"])
    assert "text/event-stream" not in content_type
    assert expected["sse"] is False
    error = response.json()["error"]
    assert error["code"] == expected["error_code"]
    assert error["status"] == expected["error_status"]
    assert str(error_body["message"]) in error["message"]
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True
