"""Offline end-to-end PG-08 path: fabricated tool history -> OpenAI SSE.

Simulates a SillyTavern-shaped preset that puts the user prompt into a fabricated
assistant ``tool_calls`` entry and a ``role:"tool"`` output.  The request must
pass PG-08 leniency, PG-01 signature fallback, PG-04 Struct coercion, optional
ATG injection and the pooled-routing seam, then render valid OpenAI SSE.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from aistudio_api.api import routes_openai
from aistudio_api.api.dependencies import get_routing_pipeline
from aistudio_api.application import generation_service
from aistudio_api.application.atg import create_atg_processor
from aistudio_api.domain.errors import SurfaceSwitchExhaustedError
from tests.unit.routing_fakes import ScriptedPipeline, make_attempt, make_context

PROMPT_TEXT = "USER PROMPT THAT MUST SURVIVE TOOL CONTENT"
TOOL_OUTPUT = "FABRICATED TOOL OUTPUT"
NATIVE_ANSWER = "native final answer"
ATG_ANSWER = "atg final answer"


def _payload(*, stream: bool) -> dict[str, Any]:
    return {
        "model": "gemini-3.7-flash",
        "stream": stream,
        "messages": [
            {"role": "system", "content": "preset system"},
            {"role": "user", "content": "opening line"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "emit_answer",
                            "arguments": PROMPT_TEXT,
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_fabricated", "content": TOOL_OUTPUT},
        ],
    }


def _find_synthetic(tools: object) -> str:
    if not isinstance(tools, list):
        return ""
    for tool in tools:
        if not isinstance(tool, list) or len(tool) < 2 or not isinstance(tool[1], list):
            continue
        for decl in tool[1]:
            if (
                isinstance(decl, list)
                and decl
                and isinstance(decl[0], str)
                and decl[0].startswith("atg_emit_")
            ):
                return decl[0]
    return ""


class _RecordingClient:
    def __init__(self, *, atg: bool) -> None:
        self.atg = atg
        self.contents: list[Any] = []
        self.tools: object = None

    async def stream_generate_content(self, **kwargs):
        self.contents = list(kwargs.get("contents") or [])
        self.tools = kwargs.get("tools")
        if self.atg:
            name = _find_synthetic(self.tools)
            yield (
                "tool_calls",
                [{"name": name, "args": {"content": ATG_ANSWER}}],
            )
        else:
            yield ("body", NATIVE_ANSWER)
        yield ("finish_reason", 1)
        yield ("usage", {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})


def _app(pipeline: ScriptedPipeline) -> FastAPI:
    app = FastAPI()
    app.include_router(routes_openai.router)
    app.dependency_overrides[routes_openai.require_openai] = lambda: None
    app.dependency_overrides[get_routing_pipeline] = lambda: pipeline
    return app


async def _post(app: FastAPI, payload: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/v1/chat/completions", json=payload)


def _sse_contents(raw: str) -> list[str]:
    parts: list[str] = []
    for block in raw.split("\n\n"):
        if not block.startswith("data: "):
            continue
        data = block[len("data: ") :]
        if data == "[DONE]":
            continue
        payload = json.loads(data)
        choices = payload.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str):
            parts.append(content)
    return parts


def _assert_pg08_conversion(client: _RecordingClient) -> None:
    joined = "\n".join(str(part) for content in client.contents for part in content.parts)
    assert PROMPT_TEXT in joined
    assert TOOL_OUTPUT in joined
    call = next(
        part
        for content in client.contents
        for part in content.parts
        if part.function_call is not None
    )
    response = next(
        part
        for content in client.contents
        for part in content.parts
        if part.function_response is not None
    )
    assert call.function_call[0] == "emit_answer"
    assert call.function_call[2].startswith("call_auto_")
    assert call.thought_signature == "context_engineering_is_the_way_to_go"
    assert response.function_response[0] == "emit_answer"
    assert response.function_response[1] == {"result": TOOL_OUTPUT}


@pytest.mark.asyncio
async def test_pg08_fabricated_history_native_tool_mode_streams(monkeypatch):
    client = _RecordingClient(atg=False)
    pipeline = ScriptedPipeline(
        [make_attempt(client)], context=make_context(model="gemini-3.7-flash")
    )

    response = await _post(_app(pipeline), _payload(stream=True))

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert NATIVE_ANSWER in "".join(_sse_contents(response.text))
    assert response.text.rstrip().endswith("data: [DONE]")
    _assert_pg08_conversion(client)
    assert _find_synthetic(client.tools) == ""


@pytest.mark.asyncio
async def test_pg08_fabricated_history_atg_mode_streams(monkeypatch):
    def factory(base=None):
        return create_atg_processor({**(base or {}), "mode": "prefer"})

    monkeypatch.setattr(generation_service, "create_atg_processor", factory)
    client = _RecordingClient(atg=True)
    pipeline = ScriptedPipeline(
        [make_attempt(client)], context=make_context(model="gemini-3.7-flash")
    )

    response = await _post(_app(pipeline), _payload(stream=True))

    assert response.status_code == 200
    body = "".join(_sse_contents(response.text))
    assert ATG_ANSWER in body
    assert "atg_emit_" not in body
    assert response.text.rstrip().endswith("data: [DONE]")
    _assert_pg08_conversion(client)
    assert _find_synthetic(client.tools).startswith("atg_emit_")


@pytest.mark.asyncio
async def test_pg08_atg_required_missing_synthetic_surfaces_sse_error(monkeypatch):
    def factory(base=None):
        return create_atg_processor({**(base or {}), "mode": "required"})

    monkeypatch.setattr(generation_service, "create_atg_processor", factory)
    client = _RecordingClient(atg=False)
    pipeline = ScriptedPipeline(
        [make_attempt(client)], context=make_context(model="gemini-3.7-flash")
    )

    response = await _post(_app(pipeline), _payload(stream=True))

    assert response.status_code == 200
    assert "synthetic tool call required" in response.text or "error" in response.text


@pytest.mark.asyncio
async def test_pg08_route_returns_openai_error_when_switches_exhausted(monkeypatch):
    async def no_events(**_kwargs):
        raise SurfaceSwitchExhaustedError(surface="chat")
        yield ("body", "")  # pragma: no cover

    client = SimpleNamespace(stream_generate_content=no_events)
    pipeline = ScriptedPipeline(
        [make_attempt(client)], context=make_context(model="gemini-3.7-flash")
    )

    response = await _post(_app(pipeline), _payload(stream=True))

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["type"] == "service_unavailable"
