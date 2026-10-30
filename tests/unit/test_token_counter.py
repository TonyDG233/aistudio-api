"""Contracts for real upstream token counting (P5 B5b)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application import token_counter
from aistudio_api.domain.errors import RequestError, UsageLimitExceeded
from aistudio_api.infrastructure.gateway.instance_client import InstanceRequestClient
from tests.unit.routing_fakes import ScriptedPipeline, make_attempt, make_context

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


class FakeClient:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def count_tokens(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _pipeline(client: FakeClient) -> ScriptedPipeline:
    return ScriptedPipeline([make_attempt(client)], context=make_context())


async def test_count_tokens_maps_upstream_total_and_releases_attempt():
    client = FakeClient(42)

    outcome = await token_counter.count_tokens(
        model_path=f"pg/{MODEL}",
        req=_gemini_request(),
        pipeline=_pipeline(client),
        api_format="gemini",
    )

    assert outcome.total_tokens == 42
    assert outcome.model == MODEL
    body = client.calls[0]["packed_body"]
    assert isinstance(body, list)
    assert body[0] == f"models/{MODEL}"


async def test_count_tokens_propagates_upstream_error_and_releases_attempt():
    client = FakeClient(RequestError(400, "bad request"))
    pipeline = _pipeline(client)

    with pytest.raises(RequestError):
        await token_counter.count_tokens(
            model_path=MODEL,
            req=_gemini_request(),
            pipeline=pipeline,
        )

    assert pipeline.attempts == []


async def test_count_tokens_maps_429_to_switch_exhausted():
    client = FakeClient(UsageLimitExceeded("quota"))
    pipeline = _pipeline(client)
    pipeline.actions = ["raise"]

    with pytest.raises(UsageLimitExceeded):
        await token_counter.count_tokens(
            model_path=MODEL,
            req=_gemini_request(),
            pipeline=pipeline,
        )


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

    @asynccontextmanager
    async def request_scope(self):
        yield self.active_context

    async def send_hooked_request(self, **kwargs):
        self.sent.append(kwargs)
        return self.status, self.body.encode()


async def test_instance_client_count_tokens_uses_rpc():
    session = FakeSession(200, "[9,[],[[[6],1]],null,null,[[1,9]]]")
    client = InstanceRequestClient(
        cast("Any", session), account_id="acc", tier="Free", surface="chat"
    )

    total = await client.count_tokens(packed_body=["models/gemini-3.8-flash", []])

    assert total == 9
    sent = session.sent[0]
    assert str(sent["url"]).endswith("/CountTokens")
    headers = sent["headers"]
    assert headers["X-Goog-Api-Key"] == "test-key"
    assert headers["Content-Type"] == "application/json+protobuf"


async def test_instance_client_count_tokens_rejects_missing_total():
    session = FakeSession(200, '{"tokens": 3}')
    client = InstanceRequestClient(
        cast("Any", session), account_id="acc", tier="Free", surface="chat"
    )

    with pytest.raises(RequestError) as excinfo:
        await client.count_tokens(packed_body=[])

    assert excinfo.value.status == 502


async def test_instance_client_count_tokens_maps_429():
    session = FakeSession(429, '{"error": {"message": "quota"}}')
    client = InstanceRequestClient(
        cast("Any", session), account_id="acc", tier="Free", surface="chat"
    )

    with pytest.raises(UsageLimitExceeded):
        await client.count_tokens(packed_body=[])
