"""Integration tests: ATG wired into the Gemini service with a mocked client.

Covers non-stream and stream unwrap, required explicit failure, empty retries,
the client-synthetic guard skip, and the E5 whitelist survival path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from aistudio_api.api.response_models import GeminiGenerateContentResponse
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application import generation_service
from aistudio_api.application.api_service_gemini import handle_gemini_generate_content
from aistudio_api.application.atg import create_atg_processor
from aistudio_api.domain.models import Candidate, ModelOutput
from aistudio_api.infrastructure.gateway.wire_codec import AistudioWireCodec

MODEL = "gemini-3.7-flash"


def _non_stream(response: object) -> GeminiGenerateContentResponse:
    return cast("GeminiGenerateContentResponse", response)


def _detail(exc: HTTPException) -> dict[str, Any]:
    return cast("dict[str, Any]", exc.detail)


def _enable_atg(monkeypatch, mode="prefer", **overrides):
    def factory(base=None):
        return create_atg_processor({**(base or {}), "mode": mode, **overrides})

    monkeypatch.setattr(generation_service, "create_atg_processor", factory)


def _req(*, tools=None, tool_config=None):
    body = {"contents": [{"role": "user", "parts": [{"text": "Hi"}]}]}
    if tools is not None:
        body["tools"] = tools
    if tool_config is not None:
        body["toolConfig"] = tool_config
    return GeminiGenerateContentRequest.model_validate(body)


def _wire_names(tools):
    names = []
    for tool in tools or []:
        if isinstance(tool, list) and len(tool) > 1 and isinstance(tool[1], list):
            for decl in tool[1]:
                if isinstance(decl, list) and decl and isinstance(decl[0], str):
                    names.append(decl[0])
    return names


def _synthetic_name(tools):
    for name in _wire_names(tools):
        if name.startswith("atg_emit_"):
            return name
    return ""


async def _collect(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
    return "".join(chunks)


# ========== Non-streaming ==========


@pytest.mark.asyncio
async def test_non_stream_prefer_unwraps_synthetic(monkeypatch):
    _enable_atg(monkeypatch)
    client = MagicMock()

    async def fake_generate(**kwargs):
        name = _synthetic_name(kwargs.get("tools"))
        return ModelOutput(
            candidates=[
                Candidate(function_calls=[{"name": name, "args": {"content": "FINAL"}}])
            ],
            usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        )

    client.generate_content = AsyncMock(side_effect=fake_generate)
    resp = _non_stream(
        await handle_gemini_generate_content(MODEL, _req(), client, stream=False)
    )
    parts = resp.candidates[0].content.parts
    assert parts[0].text == "FINAL"
    assert all(part.functionCall is None for part in parts)


@pytest.mark.asyncio
async def test_non_stream_required_text_only_raises(monkeypatch):
    _enable_atg(monkeypatch, mode="required")
    client = MagicMock()

    async def fake_generate(**_kwargs):
        return ModelOutput(candidates=[Candidate(text="just text")], usage={})

    client.generate_content = AsyncMock(side_effect=fake_generate)
    with pytest.raises(HTTPException) as exc:
        await handle_gemini_generate_content(MODEL, _req(), client, stream=False)
    assert exc.value.status_code == 500
    assert "synthetic tool call required" in _detail(exc.value)["message"]


@pytest.mark.asyncio
async def test_non_stream_empty_retry_then_success(monkeypatch):
    _enable_atg(monkeypatch, empty_retries=2)
    client = MagicMock()
    calls = {"n": 0}

    async def fake_generate(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            return ModelOutput(candidates=[Candidate()], usage={})
        name = _synthetic_name(kwargs.get("tools"))
        return ModelOutput(
            candidates=[
                Candidate(function_calls=[{"name": name, "args": {"content": "OK"}}])
            ],
            usage={},
        )

    client.generate_content = AsyncMock(side_effect=fake_generate)
    resp = _non_stream(
        await handle_gemini_generate_content(MODEL, _req(), client, stream=False)
    )
    assert calls["n"] == 3
    assert resp.candidates[0].content.parts[0].text == "OK"


@pytest.mark.asyncio
async def test_non_stream_empty_exhausted_prefer_returns_empty(monkeypatch):
    _enable_atg(monkeypatch, empty_retries=1)
    client = MagicMock()
    calls = {"n": 0}

    async def fake_generate(**_kwargs):
        calls["n"] += 1
        return ModelOutput(candidates=[Candidate()], usage={})

    client.generate_content = AsyncMock(side_effect=fake_generate)
    resp = _non_stream(
        await handle_gemini_generate_content(MODEL, _req(), client, stream=False)
    )
    assert calls["n"] == 2
    assert resp.candidates[0].content.parts[0].text == ""


@pytest.mark.asyncio
async def test_non_stream_empty_exhausted_required_raises(monkeypatch):
    _enable_atg(monkeypatch, mode="required", empty_retries=1)
    client = MagicMock()

    async def fake_generate(**_kwargs):
        return ModelOutput(candidates=[Candidate()], usage={})

    client.generate_content = AsyncMock(side_effect=fake_generate)
    with pytest.raises(HTTPException) as exc:
        await handle_gemini_generate_content(MODEL, _req(), client, stream=False)
    assert exc.value.status_code == 500
    assert "synthetic tool call required" in _detail(exc.value)["message"]


@pytest.mark.asyncio
async def test_non_stream_guard_skips_injection(monkeypatch):
    _enable_atg(monkeypatch)
    client = MagicMock()
    captured: dict[str, Any] = {}

    async def fake_generate(**kwargs):
        captured["contents"] = kwargs.get("contents")
        return ModelOutput(candidates=[Candidate(text="client-handled")], usage={})

    client.generate_content = AsyncMock(side_effect=fake_generate)
    req = _req(tools=[{"functionDeclarations": [{"name": "atg_emit_client"}]}])
    resp = _non_stream(
        await handle_gemini_generate_content(MODEL, req, client, stream=False)
    )
    assert resp.candidates[0].content.parts[0].text == "client-handled"
    # No synthetic control turn was appended (guard skipped the local wrap).
    assert len(captured["contents"]) == 1


@pytest.mark.asyncio
async def test_non_stream_any_pin_keeps_synthetic_in_wire(monkeypatch):
    _enable_atg(monkeypatch)
    client = MagicMock()
    captured: dict[str, Any] = {}

    async def fake_generate(**kwargs):
        captured["tools"] = kwargs.get("tools")
        captured["tool_config"] = kwargs.get("tool_config")
        name = _synthetic_name(kwargs.get("tools"))
        return ModelOutput(
            candidates=[
                Candidate(function_calls=[{"name": name, "args": {"content": "OK"}}])
            ],
            usage={},
        )

    client.generate_content = AsyncMock(side_effect=fake_generate)
    req = _req(
        tools=[
            {
                "functionDeclarations": [
                    {
                        "name": "real_tool",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {"x": {"type": "STRING"}},
                        },
                    }
                ]
            }
        ],
        tool_config={
            "functionCallingConfig": {
                "mode": "ANY",
                "allowedFunctionNames": ["real_tool"],
            }
        },
    )
    resp = _non_stream(
        await handle_gemini_generate_content(MODEL, req, client, stream=False)
    )
    assert resp.candidates[0].content.parts[0].text == "OK"

    wire_names = _wire_names(captured["tools"])
    assert "real_tool" in wire_names
    assert any(name.startswith("atg_emit_") for name in wire_names)

    allowed = captured["tool_config"][1][1]
    assert "real_tool" in allowed
    assert any(name.startswith("atg_emit_") for name in allowed)


# ========== Streaming ==========


@pytest.mark.asyncio
async def test_stream_prefer_unwraps_and_suppresses_preamble(monkeypatch):
    _enable_atg(monkeypatch, side_buffer_bytes=4096)
    client = MagicMock()

    async def fake_stream(**kwargs):
        name = _synthetic_name(kwargs.get("tools"))
        yield ("body", "preamble")
        yield ("tool_calls", [{"name": name, "args": {"content": "STREAMED"}}])
        yield ("finish_reason", 1)
        yield ("usage", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        yield ("done", None)

    client.stream_generate_content = fake_stream
    resp = await handle_gemini_generate_content(MODEL, _req(), client, stream=True)
    text = await _collect(resp)
    assert "STREAMED" in text
    assert "preamble" not in text
    assert "atg_emit_" not in text
    assert "STOP" in text


@pytest.mark.asyncio
async def test_stream_required_missing_yields_error(monkeypatch):
    _enable_atg(monkeypatch, mode="required")
    client = MagicMock()

    async def fake_stream(**_kwargs):
        yield ("body", "hello")
        yield ("done", None)

    client.stream_generate_content = fake_stream
    resp = await handle_gemini_generate_content(MODEL, _req(), client, stream=True)
    text = await _collect(resp)
    assert "synthetic tool call required" in text


@pytest.mark.asyncio
async def test_stream_empty_retry_then_success(monkeypatch):
    _enable_atg(monkeypatch, empty_retries=2)
    client = MagicMock()
    calls = {"n": 0}

    async def fake_stream(**kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            yield ("usage", {"prompt_tokens": 0, "completion_tokens": 0})
            yield ("done", None)
            return
        name = _synthetic_name(kwargs.get("tools"))
        yield ("tool_calls", [{"name": name, "args": {"content": "OK"}}])
        yield ("usage", {"prompt_tokens": 1, "completion_tokens": 1})
        yield ("done", None)

    client.stream_generate_content = fake_stream
    resp = await handle_gemini_generate_content(MODEL, _req(), client, stream=True)
    text = await _collect(resp)
    assert calls["n"] == 2
    assert "OK" in text


# ========== E5 whitelist: wire codec filtering (pitfall + fix) ==========


def _wire_names_from_json(encoded: str, tools_index: int) -> list[str]:
    body = json.loads(encoded)
    tools = body[tools_index] if len(body) > tools_index else None
    names: list[str] = []
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, list) and len(tool) > 1 and isinstance(tool[1], list):
                for decl in tool[1]:
                    if isinstance(decl, list) and decl:
                        names.append(str(decl[0]))
    return names


def test_e5_wire_whitelist_filters_then_fix_keeps_synthetic():
    codec = AistudioWireCodec()
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "test_input.json"
    original_body = fixture.read_text(encoding="utf-8")
    tools = [[None, [["real_tool", None], ["atg_emit_synth", None]]]]

    pit: list[object] = [None, [2, ["real_tool"]]]
    out_pit = codec.rewrite(
        original_body=original_body, model=MODEL, tools=tools, tool_config=pit
    )
    assert _wire_names_from_json(out_pit, codec.TOOLS_INDEX) == ["real_tool"]

    fix: list[object] = [None, [2, ["real_tool", "atg_emit_synth"]]]
    out_fix = codec.rewrite(
        original_body=original_body, model=MODEL, tools=tools, tool_config=fix
    )
    assert set(_wire_names_from_json(out_fix, codec.TOOLS_INDEX)) >= {
        "real_tool",
        "atg_emit_synth",
    }
