"""SSE keepalive / bounded first-event behavior for both streaming faces."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

from aistudio_api.api import routes_gemini, routes_openai
from aistudio_api.api.dependencies import get_routing_pipeline, get_runtime_state
from aistudio_api.api.error_mapping import error_payload
from aistudio_api.api.gemini_service import handle_gemini_generate_content
from aistudio_api.api.sse_keepalive import (
    DEFAULT_KEEPALIVE_INTERVAL_SEC,
    KEEPALIVE_ENV,
    KEEPALIVE_FRAME,
    KEEPALIVE_TIMEOUT,
    KeepaliveIterator,
    keepalive_interval_sec,
    next_with_timeout,
)
from aistudio_api.application.contracts.gemini import (
    GeminiContent,
    GeminiGenerateContentRequest,
    GeminiPart,
)
from aistudio_api.application.generation_service import StreamEvent
from aistudio_api.domain.errors import (
    AistudioError,
    BrowserTransportError,
    RequestError,
)
from tests.unit.routing_fakes import ScriptedPipeline, make_attempt, make_context

FAST_INTERVAL = "0.02"


@pytest.fixture(autouse=True)
def _fast_keepalive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEEPALIVE_ENV, FAST_INTERVAL)


def _gemini_request() -> GeminiGenerateContentRequest:
    return GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hello")])]
    )


def _sse_payloads(raw: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block.startswith("data: "):
            continue
        data = block[len("data: ") :]
        if data == "[DONE]":
            continue
        payloads.append(cast("dict[str, Any]", json.loads(data)))
    return payloads


class _TrackingPipeline(ScriptedPipeline):
    """Scripted pipeline that exposes the live active-request counter."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.active_requests = 0

    async def acquire(self, context):
        attempt = await super().acquire(context)
        if attempt is not None:
            self.active_requests += 1
        return attempt

    async def release(self, attempt) -> None:
        await super().release(attempt)
        self.active_requests = max(0, self.active_requests - 1)


def _tracking_pipeline(stream: Any) -> _TrackingPipeline:
    client = SimpleNamespace(stream_generate_content=stream)
    attempt = make_attempt(client)
    return _TrackingPipeline([attempt], context=make_context())


def _delayed_stream(delay: float, events: list[tuple[str, object | None]]):
    async def stream(**_kwargs: Any) -> AsyncIterator[tuple[str, object | None]]:
        await asyncio.sleep(delay)
        for event in events:
            yield event

    return stream


def _failing_stream(exc: Exception):
    async def stream(**_kwargs: Any) -> AsyncIterator[tuple[str, object | None]]:
        raise exc
        if False:  # pragma: no cover - makes this an async generator
            yield ("body", "")

    return stream


def _empty_stream(**kwargs: Any) -> AsyncIterator[StreamEvent]:
    del kwargs

    async def generator() -> AsyncIterator[StreamEvent]:
        if False:  # pragma: no cover
            yield StreamEvent(kind="body", payload="")

    return generator()


def _slow_empty_stream(**kwargs: Any) -> AsyncIterator[StreamEvent]:
    del kwargs

    async def generator() -> AsyncIterator[StreamEvent]:
        await asyncio.sleep(0.06)
        if False:  # pragma: no cover
            yield StreamEvent(kind="body", payload="")

    return generator()


# ========== helpers ==========


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, DEFAULT_KEEPALIVE_INTERVAL_SEC),
        ("", DEFAULT_KEEPALIVE_INTERVAL_SEC),
        ("0", DEFAULT_KEEPALIVE_INTERVAL_SEC),
        ("-1", DEFAULT_KEEPALIVE_INTERVAL_SEC),
        ("nan", DEFAULT_KEEPALIVE_INTERVAL_SEC),
        ("inf", DEFAULT_KEEPALIVE_INTERVAL_SEC),
        ("not-a-number", DEFAULT_KEEPALIVE_INTERVAL_SEC),
        ("0.02", 0.02),
    ],
)
def test_keepalive_interval_env_parsing(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: float
) -> None:
    if raw is None:
        monkeypatch.delenv(KEEPALIVE_ENV, raising=False)
    else:
        monkeypatch.setenv(KEEPALIVE_ENV, raw)
    assert keepalive_interval_sec() == expected


@pytest.mark.asyncio
async def test_next_with_timeout_preserves_pending_task() -> None:
    async def source() -> AsyncIterator[str]:
        await asyncio.sleep(0.06)
        yield "value"

    iterator = source()
    pending: list[asyncio.Task[str]] = []
    first = await next_with_timeout(iterator, 0.01, pending=pending)
    assert first is KEEPALIVE_TIMEOUT
    assert pending and not pending[0].done()

    second = await next_with_timeout(iterator, 0.5, pending=pending)
    assert second == "value"
    assert pending == []


@pytest.mark.asyncio
async def test_keepalive_iterator_emits_comment_then_preserves_event() -> None:
    async def source() -> AsyncIterator[str]:
        await asyncio.sleep(0.06)
        yield "data"

    iterator = KeepaliveIterator(source(), interval=0.02)
    assert await iterator.__anext__() == KEEPALIVE_FRAME
    seen: list[str] = []
    for _ in range(50):
        item = await iterator.__anext__()
        seen.append(cast("str", item))
        if item == "data":
            break
    assert "data" in seen
    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()
    await iterator.aclose()


@pytest.mark.asyncio
async def test_keepalive_iterator_close_cancels_pending_and_closes_source() -> None:
    closed = asyncio.Event()

    async def source() -> AsyncIterator[str]:
        try:
            await asyncio.sleep(10)
            yield "never"
        finally:
            closed.set()

    iterator = KeepaliveIterator(source(), interval=0.02)
    assert await iterator.__anext__() == KEEPALIVE_FRAME
    await iterator.aclose()
    await asyncio.sleep(0)
    assert closed.is_set()


# ========== Gemini ASGI/httpx ==========


def _gemini_app(pipeline: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(routes_gemini.router)
    app.dependency_overrides[get_routing_pipeline] = lambda: pipeline

    async def _handle(_request, exc: Exception) -> JSONResponse:
        status, body = error_payload(exc, "gemini")
        return JSONResponse(status_code=status, content=body)

    app.add_exception_handler(AistudioError, _handle)
    return app


def _openai_app(pipeline: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(routes_openai.router)
    app.dependency_overrides[routes_openai.require_openai] = lambda: None
    app.dependency_overrides[routes_openai.require_api_key] = lambda: None
    app.dependency_overrides[get_runtime_state] = lambda: SimpleNamespace(client=None)
    app.dependency_overrides[get_routing_pipeline] = lambda: pipeline
    return app


async def _post(app: FastAPI, path: str, body: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(path, json=body)


def _openai_body() -> dict[str, Any]:
    return {
        "model": "gemini-3.7-flash",
        "stream": True,
        "messages": [{"role": "user", "content": "hello"}],
    }


@pytest.mark.asyncio
async def test_gemini_stream_keepalive_then_tool_call_and_usage() -> None:
    pipeline = _tracking_pipeline(
        _delayed_stream(
            0.06,
            [
                (
                    "tool_calls",
                    [{"id": "call_1", "name": "lookup", "args": {"q": "x"}}],
                ),
                ("finish_reason", 1),
                (
                    "usage",
                    {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                ),
            ],
        )
    )
    response = await _post(
        _gemini_app(pipeline),
        "/v1beta/gemini-3.7-flash:streamGenerateContent?alt=sse",
        _gemini_request().model_dump(mode="json", exclude_none=True),
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    raw = response.text
    assert ": keepalive" in raw
    payloads = _sse_payloads(raw)
    function_call = next(
        part["functionCall"]
        for payload in payloads
        for part in payload["candidates"][0]["content"]["parts"]
        if "functionCall" in part
    )
    assert function_call["name"] == "lookup"
    assert function_call["args"] == {"q": "x"}
    assert any(
        payload["candidates"][0].get("finishReason") == "STOP"
        for payload in payloads
        if payload["candidates"][0].get("finishReason")
    )
    assert any(
        payload.get("usageMetadata", {}).get("totalTokenCount") == 5
        for payload in payloads
    )
    assert pipeline.active_requests == 0


@pytest.mark.asyncio
async def test_openai_stream_keepalive_then_tool_call_and_usage() -> None:
    pipeline = _tracking_pipeline(
        _delayed_stream(
            0.06,
            [
                (
                    "tool_calls",
                    [{"id": "call_1", "name": "lookup", "args": {"q": "x"}}],
                ),
                ("finish_reason", 1),
                (
                    "usage",
                    {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                ),
            ],
        )
    )
    response = await _post(
        _openai_app(pipeline), "/v1/chat/completions", _openai_body()
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    raw = response.text
    assert ": keepalive" in raw
    assert raw.rstrip().endswith("data: [DONE]")
    payloads = _sse_payloads(raw)
    tool_calls = [
        call
        for payload in payloads
        for choice in payload["choices"]
        for call in choice["delta"].get("tool_calls", [])
    ]
    assert tool_calls[0]["function"]["name"] == "lookup"
    assert json.loads(tool_calls[0]["function"]["arguments"]) == {"q": "x"}
    assert any(
        choice.get("finish_reason") == "tool_calls"
        for payload in payloads
        for choice in payload["choices"]
    )
    assert any(
        payload.get("usage", {}).get("total_tokens") == 5 for payload in payloads
    )
    assert pipeline.active_requests == 0


@pytest.mark.asyncio
async def test_gemini_returns_headers_before_slow_first_event() -> None:
    pipeline = _tracking_pipeline(_delayed_stream(0.3, [("body", "late")]))
    loop = asyncio.get_running_loop()
    started = loop.time()
    response = await handle_gemini_generate_content(
        "gemini-3.7-flash", _gemini_request(), stream=True, pipeline=pipeline
    )
    elapsed = loop.time() - started

    assert isinstance(response, StreamingResponse)
    assert elapsed < 0.1
    iterator = cast("AsyncIterator[str]", response.body_iterator)
    assert await iterator.__anext__() == KEEPALIVE_FRAME
    await cast("Any", iterator).aclose()
    assert pipeline.active_requests == 0


@pytest.mark.asyncio
async def test_openai_returns_headers_before_slow_first_event() -> None:
    pipeline = _tracking_pipeline(_delayed_stream(0.3, [("body", "late")]))

    class _Request:
        async def json(self) -> dict[str, Any]:
            return _openai_body()

    loop = asyncio.get_running_loop()
    started = loop.time()
    response = await routes_openai.chat_completions(
        cast("Any", _Request()), pipeline=cast("Any", pipeline)
    )
    elapsed = loop.time() - started

    assert isinstance(response, StreamingResponse)
    assert elapsed < 0.1
    iterator = cast("AsyncIterator[str]", response.body_iterator)
    assert await iterator.__anext__() == KEEPALIVE_FRAME
    await cast("Any", iterator).aclose()
    assert pipeline.active_requests == 0


# ========== PG-02 before headers ==========


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RequestError(400, "bad request"), 400),
        (RequestError(403, "forbidden"), 403),
        (BrowserTransportError("upstream down"), 502),
    ],
)
@pytest.mark.asyncio
async def test_gemini_pre_header_errors_are_real_non_200(
    exc: Exception, expected: int
) -> None:
    pipeline = _tracking_pipeline(_failing_stream(exc))
    response = await _post(
        _gemini_app(pipeline),
        "/v1beta/gemini-3.7-flash:streamGenerateContent?alt=sse",
        _gemini_request().model_dump(mode="json", exclude_none=True),
    )

    assert response.status_code == expected
    assert "text/event-stream" not in response.headers["content-type"]
    assert "error" in response.json()
    assert pipeline.active_requests == 0


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RequestError(400, "bad request"), 400),
        (RequestError(403, "forbidden"), 403),
        (BrowserTransportError("upstream down"), 502),
    ],
)
@pytest.mark.asyncio
async def test_openai_pre_header_errors_are_real_non_200(
    exc: Exception, expected: int
) -> None:
    pipeline = _tracking_pipeline(_failing_stream(exc))
    response = await _post(
        _openai_app(pipeline), "/v1/chat/completions", _openai_body()
    )

    assert response.status_code == expected
    assert "text/event-stream" not in response.headers["content-type"]
    assert "error" in response.json()
    assert pipeline.active_requests == 0


@pytest.mark.asyncio
async def test_gemini_empty_stream_within_window_is_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aistudio_api.api.gemini_service.generation_service.stream_generation",
        _empty_stream,
    )
    response = await _post(
        _gemini_app(object()),
        "/v1beta/gemini-3.7-flash:streamGenerateContent?alt=sse",
        _gemini_request().model_dump(mode="json", exclude_none=True),
    )

    assert response.status_code == 502
    assert "text/event-stream" not in response.headers["content-type"]


@pytest.mark.asyncio
async def test_openai_empty_stream_within_window_is_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes_openai, "stream_generation", _empty_stream)
    response = await _post(
        _openai_app(object()), "/v1/chat/completions", _openai_body()
    )

    assert response.status_code == 500
    assert "text/event-stream" not in response.headers["content-type"]


@pytest.mark.asyncio
async def test_gemini_empty_stream_after_deadline_is_keepalive_plus_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aistudio_api.api.gemini_service.generation_service.stream_generation",
        _slow_empty_stream,
    )
    response = await _post(
        _gemini_app(object()),
        "/v1beta/gemini-3.7-flash:streamGenerateContent?alt=sse",
        _gemini_request().model_dump(mode="json", exclude_none=True),
    )

    assert response.status_code == 200
    assert ": keepalive" in response.text
    assert _sse_payloads(response.text)[-1]["error"]["code"] == 502


@pytest.mark.asyncio
async def test_openai_empty_stream_after_deadline_is_keepalive_plus_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes_openai, "stream_generation", _slow_empty_stream)
    response = await _post(
        _openai_app(object()), "/v1/chat/completions", _openai_body()
    )

    assert response.status_code == 200
    assert ": keepalive" in response.text
    payloads = _sse_payloads(response.text)
    assert payloads[-1]["error"]["type"] == "api_error"
