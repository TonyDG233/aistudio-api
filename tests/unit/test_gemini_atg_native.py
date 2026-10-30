"""Native Gemini ATG coverage (B5b B-02).

These tests drive ``handle_gemini_generate_content`` through the
protocol-neutral ``generation_service`` with an offline scripted pipeline, the
same seam OpenAI uses.  They prove ATG is not OpenAI-only:

* ``prefer`` injects the synthetic transport and unwraps it (non-stream/stream);
* ``required`` fails with the same domain error / SSE shape as the reference.

No browser, network, or real account is used.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.responses import StreamingResponse

from aistudio_api.api.response_models import GeminiGenerateContentResponse
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application import generation_service
from aistudio_api.application.api_service_gemini import (
    handle_gemini_generate_content,
)
from aistudio_api.application.atg import create_atg_processor
from aistudio_api.application.atg.extractor import SyntheticRequiredError
from aistudio_api.domain.errors import RequestError
from aistudio_api.domain.models import Candidate, ModelOutput
from tests.unit.routing_fakes import (
    FakeHandle,
    ScriptedPipeline,
    make_attempt,
    make_context,
)

MODEL = "gemini-3.7-flash"
CONTROL_MARKER = "IMPORTANT: Regular chat responses are unreliable"


def _enable_atg(monkeypatch, mode: str = "prefer", **overrides: Any) -> None:
    def factory(base=None):
        return create_atg_processor({**(base or {}), "mode": mode, **overrides})

    monkeypatch.setattr(generation_service, "create_atg_processor", factory)


def _request() -> GeminiGenerateContentRequest:
    return GeminiGenerateContentRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "Hi"}]}]}
    )


def _pipeline(client: Any) -> ScriptedPipeline:
    return ScriptedPipeline([make_attempt(client)], context=make_context())


def _wire_names(tools: object) -> list[str]:
    names: list[str] = []
    if not isinstance(tools, list):
        return names
    for tool in tools:
        if isinstance(tool, list) and len(tool) > 1 and isinstance(tool[1], list):
            for decl in tool[1]:
                if isinstance(decl, list) and decl and isinstance(decl[0], str):
                    names.append(decl[0])
    return names


def _synthetic_name(tools: object) -> str:
    for name in _wire_names(tools):
        if name.startswith("atg_emit_"):
            return name
    return ""


def _contents_text(contents: object) -> str:
    parts: list[str] = []
    if not isinstance(contents, list):
        return ""
    for content in contents:
        for part in getattr(content, "parts", ()):
            text = getattr(part, "text", None)
            if text:
                parts.append(str(text))
    return "\n".join(parts)


@pytest.mark.asyncio
async def test_native_gemini_non_stream_atg_prefer_injects_and_unwraps(
    monkeypatch,
) -> None:
    _enable_atg(monkeypatch)
    client = MagicMock()
    captured: dict[str, Any] = {}

    async def fake_generate(**kwargs: Any) -> ModelOutput:
        captured["tools"] = kwargs.get("tools")
        captured["contents"] = kwargs.get("contents")
        name = _synthetic_name(kwargs.get("tools"))
        assert name.startswith("atg_emit_")
        return ModelOutput(
            candidates=[
                Candidate(
                    function_calls=[{"name": name, "args": {"content": "FINAL"}}]
                )
            ],
            usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            response_id="resp-native",
        )

    client.generate_content = AsyncMock(side_effect=fake_generate)
    attempt = make_attempt(client)
    pipeline = ScriptedPipeline([attempt], context=make_context())

    response = await handle_gemini_generate_content(
        MODEL, _request(), stream=False, pipeline=pipeline
    )
    assert isinstance(response, GeminiGenerateContentResponse)

    parts = response.candidates[0].content.parts
    assert response.candidates[0].finishReason == "STOP"
    assert response.responseId == "resp-native"
    assert response.modelVersion == "models/" + MODEL
    assert len(parts) == 1
    assert parts[0].text == "FINAL"
    assert parts[0].functionCall is None
    assert _synthetic_name(captured["tools"]).startswith("atg_emit_")
    assert CONTROL_MARKER in _contents_text(captured["contents"])
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True


@pytest.mark.asyncio
async def test_native_gemini_non_stream_atg_purges_stale_synthetic_history(
    monkeypatch,
) -> None:
    _enable_atg(monkeypatch)
    client = MagicMock()
    captured: dict[str, Any] = {}

    async def fake_generate(**kwargs: Any) -> ModelOutput:
        captured["tools"] = kwargs.get("tools")
        captured["contents"] = kwargs.get("contents")
        name = _synthetic_name(kwargs.get("tools"))
        return ModelOutput(
            candidates=[
                Candidate(
                    function_calls=[{"name": name, "args": {"content": "FRESH"}}]
                )
            ],
            usage={},
        )

    client.generate_content = AsyncMock(side_effect=fake_generate)
    pipeline = _pipeline(client)
    req = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [
                {"role": "user", "parts": [{"text": "keep me"}]},
                {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "name": "atg_emit_stale",
                                "args": {"content": "old"},
                            }
                        }
                    ],
                },
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": "atg_emit_stale",
                                "response": {"content": "old"},
                            }
                        }
                    ],
                },
            ]
        }
    )

    response = await handle_gemini_generate_content(
        MODEL, req, stream=False, pipeline=pipeline
    )
    assert isinstance(response, GeminiGenerateContentResponse)
    assert response.candidates[0].content.parts[0].text == "FRESH"

    contents = captured["contents"]
    assert "keep me" in _contents_text(contents)
    assert CONTROL_MARKER in _contents_text(contents)
    assert not [
        part
        for content in contents
        for part in content.parts
        if getattr(part, "function_call", None) is not None
    ]
    assert _synthetic_name(captured["tools"]).startswith("atg_emit_")


@pytest.mark.asyncio
async def test_native_gemini_stream_atg_prefer_injects_and_unwraps(
    monkeypatch,
) -> None:
    _enable_atg(monkeypatch, side_buffer_bytes=4096)
    client = MagicMock()
    captured: dict[str, Any] = {}

    async def fake_stream(**kwargs: Any):
        captured["tools"] = kwargs.get("tools")
        captured["contents"] = kwargs.get("contents")
        name = _synthetic_name(kwargs.get("tools"))
        assert name.startswith("atg_emit_")
        yield ("body", "preamble")
        yield ("tool_calls", [{"name": name, "args": {"content": "STREAMED"}}])
        yield ("finish_reason", 1)
        yield (
            "usage",
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    client.stream_generate_content = fake_stream
    attempt = make_attempt(client)
    pipeline = ScriptedPipeline([attempt], context=make_context())

    response = await handle_gemini_generate_content(
        MODEL, _request(), stream=True, pipeline=pipeline
    )
    assert isinstance(response, StreamingResponse)
    chunks = [
        chunk
        async for chunk in cast(AsyncIterator[str], response.body_iterator)
    ]
    raw = "".join(chunks)

    assert "STREAMED" in raw
    assert "preamble" not in raw
    assert "atg_emit_" not in raw
    assert '"finishReason": "STOP"' in raw
    assert _synthetic_name(captured["tools"]).startswith("atg_emit_")
    assert CONTROL_MARKER in _contents_text(captured["contents"])
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True


@pytest.mark.asyncio
async def test_native_gemini_non_stream_atg_required_missing_raises(
    monkeypatch,
) -> None:
    """Non-stream ``required`` uses the reference domain error.

    ``SyntheticRequiredError`` maps to HTTP 502 in the protocol adapters
    (REV-B5b N-16/Q-08); native Gemini shares the same domain contract.
    """

    _enable_atg(monkeypatch, mode="required")
    client = MagicMock()

    async def fake_generate(**_kwargs: Any) -> ModelOutput:
        return ModelOutput(candidates=[Candidate(text="only text")], usage={})

    client.generate_content = AsyncMock(side_effect=fake_generate)
    attempt = make_attempt(client)
    pipeline = ScriptedPipeline([attempt], context=make_context())

    with pytest.raises(RequestError) as exc_info:
        await handle_gemini_generate_content(
            MODEL, _request(), stream=False, pipeline=pipeline
        )
    assert exc_info.value.status == 502
    assert isinstance(exc_info.value.__cause__, SyntheticRequiredError)
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True


@pytest.mark.asyncio
async def test_native_gemini_stream_atg_required_missing_surfaces_sse_error(
    monkeypatch,
) -> None:
    _enable_atg(monkeypatch, mode="required")
    client = MagicMock()

    async def fake_stream(**_kwargs: Any):
        yield ("body", "partial")
        yield ("finish_reason", 1)
        yield (
            "usage",
            {"prompt_tokens": 1, "completion_tokens": 1},
        )

    client.stream_generate_content = fake_stream
    attempt = make_attempt(client)
    pipeline = ScriptedPipeline([attempt], context=make_context())

    response = await handle_gemini_generate_content(
        MODEL, _request(), stream=True, pipeline=pipeline
    )
    assert isinstance(response, StreamingResponse)
    chunks = [
        chunk
        async for chunk in cast(AsyncIterator[str], response.body_iterator)
    ]
    raw = "".join(chunks)

    assert "partial" in raw
    assert "synthetic tool call required" in raw
    assert '"code": 502' in raw
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True
