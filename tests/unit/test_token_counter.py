"""Contracts for WP11 real upstream token counting."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.api.schemas_claude import ClaudeCountTokensRequest
from aistudio_api.api.state import RuntimeState
from aistudio_api.application import token_counter
from aistudio_api.application.format_converters.claude_request import (
    convert_claude_count_request,
)
from aistudio_api.application.format_converters.media import MediaFetcher
from aistudio_api.domain.errors import RequestError, UsageLimitExceeded
from aistudio_api.infrastructure.gateway.client import AIStudioClient

MODEL = "gemini-3.8-flash"


def _gemini_request(**overrides: object) -> GeminiGenerateContentRequest:
    payload: dict[str, object] = {
        "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 64},
        "tools": [{"functionDeclarations": [{"name": "lookup"}]}],
    }
    payload.update(overrides)
    return GeminiGenerateContentRequest.model_validate(payload)


def test_build_count_body_packs_system_tools_and_conversation():
    req = _gemini_request(
        systemInstruction={"parts": [{"text": "be brief"}]},
        safetySettings=[
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"}
        ],
        toolConfig={"functionCallingConfig": {"mode": "AUTO"}},
    )

    body = token_counter.build_count_body(req, MODEL)

    assert body[0] == f"models/{MODEL}"
    text = json.dumps(body, ensure_ascii=False)
    assert "[system]" in text
    assert "[tools]" in text
    assert "[conversation]" in text
    assert "hello" in text
    assert "BLOCK_NONE" not in text


async def test_claude_count_conversion_text_tools_images_thinking():
    image = base64.b64encode(b"image-bytes").decode()
    payload = ClaudeCountTokensRequest.model_validate(
        {
            "model": "claude-3-5-sonnet",
            "max_tokens": 1024,
            "thinking": {"type": "enabled", "budget_tokens": 4096},
            "system": "system prompt",
            "tools": [
                {
                    "name": "lookup",
                    "description": "lookup things",
                    "input_schema": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                    },
                }
            ],
            "tool_choice": {"type": "auto"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look this up"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image,
                            },
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "I should call lookup"},
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "lookup",
                            "input": {"q": "x"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": [{"type": "text", "text": "result"}],
                        }
                    ],
                },
            ],
        }
    )

    body = await convert_claude_count_request(payload, MediaFetcher())

    assert "generationConfig" not in body
    assert "toolConfig" not in body
    assert body["systemInstruction"] == {"parts": [{"text": "system prompt"}]}
    assert body["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "lookup",
                    "parametersJsonSchema": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                    },
                    "description": "lookup things",
                }
            ]
        }
    ]
    contents = body["contents"]
    assert isinstance(contents, list)
    parts = [part for content in contents for part in content["parts"]]
    assert any("inlineData" in part for part in parts)
    assert any(part.get("thought") is True for part in parts)
    assert any("functionCall" in part for part in parts)
    assert any("functionResponse" in part for part in parts)


class FakePipe:
    def __init__(self, ctx: SimpleNamespace) -> None:
        self.ctx = ctx
        self.released: list[object] = []
        self.success = False

    def next_candidate(self, ctx, failed_account_id=None):
        return SimpleNamespace(account_id="acc_1")

    async def acquire(self, ctx, candidate, require_idle=True):
        return SimpleNamespace(account_id="acc_1", adapter=None)

    async def release(self, ctx, lease):
        self.released.append(lease)

    def record_success(self, ctx):
        self.success = True


class FakeClient:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def count_tokens(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def test_count_tokens_maps_upstream_total_and_skips_usage_stats(
    monkeypatch,
):
    ctx = SimpleNamespace(
        clean_model=MODEL,
        surface="chat",
        retry_same=False,
        budget=SimpleNamespace(snapshot=lambda: {"max_switches": 2}),
    )
    pipe = FakePipe(ctx)
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(token_counter, "_begin_route", lambda *a, **k: (pipe, ctx))
    monkeypatch.setattr(token_counter, "record_rotator_event", lambda *a, **k: None)
    monkeypatch.setattr(
        token_counter,
        "runtime_state",
        SimpleNamespace(
            record_token_count=lambda model, api_format, **kwargs: recorded.append(
                (model, api_format)
            )
        ),
    )
    client = FakeClient(42)

    outcome = await token_counter.count_tokens(
        model_path="pg/gemini-3.8-flash",
        req=_gemini_request(),
        client=cast("AIStudioClient", client),
        api_format="claude",
    )

    assert outcome.total_tokens == 42
    assert outcome.model == MODEL
    assert pipe.success is True
    assert len(pipe.released) == 1
    assert recorded == [(MODEL, "claude")]
    body = client.calls[0]["packed_body"]
    assert isinstance(body, list)
    assert body[0] == f"models/{MODEL}"


async def test_count_tokens_propagates_upstream_error(monkeypatch):
    ctx = SimpleNamespace(
        clean_model=MODEL,
        surface="chat",
        retry_same=False,
        budget=SimpleNamespace(snapshot=lambda: {"max_switches": 2}),
    )
    pipe = FakePipe(ctx)
    monkeypatch.setattr(token_counter, "_begin_route", lambda *a, **k: (pipe, ctx))
    monkeypatch.setattr(token_counter, "record_rotator_event", lambda *a, **k: None)

    async def no_retry(*args, **kwargs):
        return False

    monkeypatch.setattr(token_counter, "handle_attempt_exception", no_retry)
    client = FakeClient(RequestError(400, "bad request"))

    with pytest.raises(RequestError):
        await token_counter.count_tokens(
            model_path="gemini-3.8-flash",
            req=_gemini_request(),
            client=cast("AIStudioClient", client),
        )

    assert len(pipe.released) == 1


class FakeSession:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        self.sent: list[dict[str, Any]] = []
        self.active_context = SimpleNamespace(
            base_template={
                "url": (
                    "https://alkalimakersuite-pa.clients6.google.com/$rpc/"
                    "google.internal.alkali.applications.makersuite.v1."
                    "MakerSuiteService/GenerateContent"
                ),
                "headers": {
                    "X-Goog-Api-Key": "test-key",
                    "Content-Type": "application/json+protobuf",
                },
            }
        )

    async def send_hooked_request(self, **kwargs):
        self.sent.append(kwargs)
        return self.status, self.body.encode()


async def test_client_count_tokens_uses_rpc():
    client = AIStudioClient()
    session = FakeSession(200, "[9,[],[[[6],1]],null,null,[[1,9]]]")
    client._session = session  # type: ignore[assignment]

    total = await client.count_tokens(
        packed_body=["models/gemini-3.8-flash", []],
    )

    assert total == 9
    sent = session.sent[0]
    assert sent["url"].endswith("/CountTokens")
    headers = sent["headers"]
    assert headers["X-Goog-Api-Key"] == "test-key"
    assert headers["Content-Type"] == "application/json+protobuf"


async def test_client_count_tokens_rejects_missing_total():
    client = AIStudioClient()
    client._session = FakeSession(200, '{"tokens": 3}')  # type: ignore[assignment]

    with pytest.raises(RequestError) as excinfo:
        await client.count_tokens(packed_body=[])

    assert excinfo.value.status == 502


async def test_client_count_tokens_maps_429():
    client = AIStudioClient()
    client._session = FakeSession(429, '{"error": {"message": "quota"}}')  # type: ignore[assignment]

    with pytest.raises(UsageLimitExceeded):
        await client.count_tokens(packed_body=[])


def test_record_token_count_does_not_touch_generation_stats(monkeypatch):
    state = RuntimeState()
    state.model_stats.clear()
    monkeypatch.setattr(state, "_schedule_debounced_save", lambda: None)

    state.record_token_count(MODEL, "claude")

    stats = state.model_stats[MODEL]
    assert stats.requests == 0
    assert stats.count_requests == 1
    assert stats.count_success == 1
    assert stats.count_input_tokens == 0
    assert stats.formats["claude"].count_requests == 1
    assert stats.success == 0
    assert stats.errors == 0
    assert stats.prompt_tokens == 0
    assert stats.completion_tokens == 0
    assert stats.total_tokens == 0
