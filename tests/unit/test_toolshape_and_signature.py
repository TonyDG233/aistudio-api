"""Toolshape log gating and signature-cache producer contracts (P5 B5b)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application import generation_service
from aistudio_api.application.chat_service import normalize_gemini_request
from aistudio_api.application.signature_cache import signature_cache
from aistudio_api.domain.errors import UsageLimitExceeded
from aistudio_api.domain.models import Candidate, ModelOutput
from tests.unit.routing_fakes import ScriptedPipeline, make_attempt, make_context


def _plain_request() -> GeminiGenerateContentRequest:
    return GeminiGenerateContentRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "hello"}]}]}
    )


def _tool_request() -> GeminiGenerateContentRequest:
    return GeminiGenerateContentRequest.model_validate(
        {
            "contents": [{"role": "user", "parts": [{"text": "use the tool"}]}],
            "tools": [{"functionDeclarations": [{"name": "lookup"}]}],
        }
    )


def _toolshape_logs(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if "[toolshape]" in record.getMessage()
    ]


def test_pure_text_request_emits_no_toolshape(caplog):
    caplog.set_level("INFO")
    normalize_gemini_request(_plain_request(), "gemini-3.7-flash")
    assert _toolshape_logs(caplog) == []


def test_tool_request_emits_toolshape_and_redacts_text(caplog):
    caplog.set_level("INFO")
    secret = "SECRET_ARGUMENT_VALUE"
    req = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [
                {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "name": "lookup",
                                "args": {"content": secret},
                            }
                        }
                    ],
                }
            ],
            "tools": [{"functionDeclarations": [{"name": "lookup"}]}],
        }
    )
    normalize_gemini_request(req, "gemini-3.7-flash")
    logs = _toolshape_logs(caplog)
    assert len(logs) == 1
    assert secret not in logs[0]


@pytest.mark.asyncio
async def test_retry_does_not_duplicate_toolshape(caplog):
    caplog.set_level("INFO")
    calls = 0

    async def fake_generate(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise UsageLimitExceeded("429")
        return ModelOutput(candidates=[Candidate(text="ok")], usage={})

    client = MagicMock()
    client.generate_content = AsyncMock(side_effect=fake_generate)
    pipeline = ScriptedPipeline(
        [make_attempt(client)], actions=["retry_same"], context=make_context()
    )

    await generation_service.generate(
        model_path="gemini-3.7-flash",
        req=_tool_request(),
        pipeline=pipeline,
        api_format="gemini",
    )

    assert calls == 2
    assert len(_toolshape_logs(caplog)) == 1


@pytest.mark.asyncio
async def test_non_stream_producer_caches_tool_call_signature():
    signature_cache.clear()

    async def fake_generate(**_kwargs):
        return ModelOutput(
            candidates=[
                Candidate(
                    function_calls=[
                        {
                            "id": "call_sig_nonstream",
                            "name": "lookup",
                            "args": {},
                            "thought_signature": "sig-nonstream",
                        }
                    ]
                )
            ],
            usage={},
        )

    client = MagicMock()
    client.generate_content = AsyncMock(side_effect=fake_generate)
    pipeline = ScriptedPipeline([make_attempt(client)], context=make_context())

    await generation_service.generate(
        model_path="gemini-3.7-flash",
        req=_plain_request(),
        pipeline=pipeline,
        api_format="openai",
    )

    assert signature_cache.get("call_sig_nonstream") == "sig-nonstream"


@pytest.mark.asyncio
async def test_stream_producer_caches_tool_call_signature_and_skips_empty():
    signature_cache.clear()

    async def fake_stream(**_kwargs):
        yield (
            "tool_calls",
            [
                {
                    "id": "call_sig_stream",
                    "name": "lookup",
                    "args": {},
                    "thought_signature": "sig-stream",
                },
                {"id": "", "name": "lookup", "args": {}, "thought_signature": "no-id"},
                {"id": "call_no_sig", "name": "lookup", "args": {}},
            ],
        )
        yield ("usage", {"prompt_tokens": 1, "completion_tokens": 1})
        yield ("done", None)

    client = MagicMock()
    client.stream_generate_content = fake_stream
    pipeline = ScriptedPipeline([make_attempt(client)], context=make_context())

    events: list[Any] = [
        event
        async for event in generation_service.stream_generation(
            model_path="gemini-3.7-flash",
            req=_plain_request(),
            pipeline=pipeline,
            api_format="openai",
        )
    ]
    assert events

    assert signature_cache.get("call_sig_stream") == "sig-stream"
    assert signature_cache.get("") is None
    assert signature_cache.get("call_no_sig") is None
    assert signature_cache.get("no-id") is None
