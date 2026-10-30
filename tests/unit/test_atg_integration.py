"""Integration tests: ATG wired into the protocol-neutral generation service.

Covers non-stream and stream unwrap, required explicit failure, empty retries,
the client-synthetic guard skip, and the E5 whitelist survival path.  All
requests run through an offline scripted routing pipeline; no browser/network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application import generation_service
from aistudio_api.application.atg import create_atg_processor
from aistudio_api.application.atg.extractor import SyntheticRequiredError
from aistudio_api.domain.models import Candidate, ModelOutput
from aistudio_api.infrastructure.gateway.wire_codec import AistudioWireCodec
from tests.unit.routing_fakes import ScriptedPipeline, make_attempt, make_context

MODEL = "gemini-3.7-flash"


def _enable_atg(monkeypatch, mode="prefer", **overrides):
    def factory(base=None):
        return create_atg_processor({**(base or {}), "mode": mode, **overrides})

    monkeypatch.setattr(generation_service, "create_atg_processor", factory)


def _req(*, tools=None, tool_config=None):
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": "Hi"}]}]
    }
    if tools is not None:
        body["tools"] = tools
    if tool_config is not None:
        body["toolConfig"] = tool_config
    return GeminiGenerateContentRequest.model_validate(body)


def _pipeline(client) -> ScriptedPipeline:
    return ScriptedPipeline([make_attempt(client)], context=make_context())


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


async def _collect(stream) -> list[Any]:
    return [event async for event in stream]


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
    outcome = await generation_service.generate(
        model_path=MODEL, req=_req(), pipeline=_pipeline(client), api_format="gemini"
    )
    assert outcome.output.candidates[0].text == "FINAL"
    assert outcome.output.function_calls == []


@pytest.mark.asyncio
async def test_non_stream_required_text_only_raises(monkeypatch):
    _enable_atg(monkeypatch, mode="required")
    client = MagicMock()

    async def fake_generate(**_kwargs):
        return ModelOutput(candidates=[Candidate(text="just text")], usage={})

    client.generate_content = AsyncMock(side_effect=fake_generate)
    with pytest.raises(SyntheticRequiredError) as exc:
        await generation_service.generate(
            model_path=MODEL,
            req=_req(),
            pipeline=_pipeline(client),
            api_format="gemini",
        )
    assert "synthetic tool call required" in str(exc.value)


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
    outcome = await generation_service.generate(
        model_path=MODEL, req=_req(), pipeline=_pipeline(client), api_format="gemini"
    )
    assert calls["n"] == 3
    assert outcome.output.candidates[0].text == "OK"


@pytest.mark.asyncio
async def test_non_stream_empty_exhausted_prefer_returns_empty(monkeypatch):
    _enable_atg(monkeypatch, empty_retries=1)
    client = MagicMock()
    calls = {"n": 0}

    async def fake_generate(**_kwargs):
        calls["n"] += 1
        return ModelOutput(candidates=[Candidate()], usage={})

    client.generate_content = AsyncMock(side_effect=fake_generate)
    outcome = await generation_service.generate(
        model_path=MODEL, req=_req(), pipeline=_pipeline(client), api_format="gemini"
    )
    assert calls["n"] == 2
    assert outcome.output.candidates[0].text == ""


@pytest.mark.asyncio
async def test_non_stream_empty_exhausted_required_raises(monkeypatch):
    _enable_atg(monkeypatch, mode="required", empty_retries=1)
    client = MagicMock()

    async def fake_generate(**_kwargs):
        return ModelOutput(candidates=[Candidate()], usage={})

    client.generate_content = AsyncMock(side_effect=fake_generate)
    with pytest.raises(SyntheticRequiredError):
        await generation_service.generate(
            model_path=MODEL,
            req=_req(),
            pipeline=_pipeline(client),
            api_format="gemini",
        )


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
    outcome = await generation_service.generate(
        model_path=MODEL, req=req, pipeline=_pipeline(client), api_format="gemini"
    )
    assert outcome.output.candidates[0].text == "client-handled"
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
    outcome = await generation_service.generate(
        model_path=MODEL, req=req, pipeline=_pipeline(client), api_format="gemini"
    )
    assert outcome.output.candidates[0].text == "OK"

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
    events = await _collect(
        generation_service.stream_generation(
            model_path=MODEL,
            req=_req(),
            pipeline=_pipeline(client),
            api_format="gemini",
        )
    )
    kinds = [event.kind for event in events]
    payloads = [str(event.payload) for event in events]
    assert "body" in kinds
    assert any("STREAMED" in payload for payload in payloads)
    assert not any("preamble" in payload for payload in payloads)
    assert not any("atg_emit_" in payload for payload in payloads)
    assert events[-1].kind == "usage"
    assert events[-1].finish_reason == "STOP"


@pytest.mark.asyncio
async def test_stream_required_missing_raises(monkeypatch):
    _enable_atg(monkeypatch, mode="required")
    client = MagicMock()

    async def fake_stream(**_kwargs):
        yield ("body", "hello")
        yield ("done", None)

    client.stream_generate_content = fake_stream
    with pytest.raises(SyntheticRequiredError):
        await _collect(
            generation_service.stream_generation(
                model_path=MODEL,
                req=_req(),
                pipeline=_pipeline(client),
                api_format="gemini",
            )
        )


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
    events = await _collect(
        generation_service.stream_generation(
            model_path=MODEL,
            req=_req(),
            pipeline=_pipeline(client),
            api_format="gemini",
        )
    )
    assert calls["n"] == 2
    assert any("OK" in str(event.payload) for event in events)


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
