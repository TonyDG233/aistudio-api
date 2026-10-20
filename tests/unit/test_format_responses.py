"""OpenAI Responses face contracts (WP11.1)."""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI

from aistudio_api.api import format_errors, routes_responses
from aistudio_api.api.dependencies import get_client
from aistudio_api.api.schemas_openai_responses import ResponsesRequest
from aistudio_api.application.format_converters.media import MediaFetcher
from aistudio_api.application.format_converters.responses_request import (
    convert_responses_request,
)
from aistudio_api.application.format_converters.responses_response import (
    ResponsesStreamRenderer,
    responses_response,
)
from aistudio_api.application.generation_service import (
    GenerationOutcome,
    StreamEvent,
)
from aistudio_api.domain.errors import RequestError
from aistudio_api.domain.models import Candidate, ModelOutput


def _outcome(
    candidate: Candidate,
    *,
    usage: dict[str, object] | None = None,
) -> GenerationOutcome:
    return GenerationOutcome(
        output=ModelOutput(
            candidates=[candidate],
            usage=usage
            or {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
                "prompt_tokens_details": {"cached_tokens": 3},
                "completion_tokens_details": {"reasoning_tokens": 2},
            },
        ),
        model="gemini-test",
        finish_reason="STOP",
        response_id="resp-1",
        surface="chat",
    )


def _sse_events(raw: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in raw.strip().split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        event_name = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if event_name and data:
            events.append((event_name, json.loads(data)))
    return events


async def test_convert_text_instructions_tools_and_config():
    payload = ResponsesRequest.model_validate(
        {
            "model": "gemini-3.8-flash",
            "instructions": "be brief",
            "input": "hello",
            "tools": [
                {
                    "type": "function",
                    "name": "lookup",
                    "description": "look things up",
                    "parameters": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                    },
                }
            ],
            "tool_choice": "required",
            "max_output_tokens": 64,
            "temperature": 0.2,
            "reasoning": {"effort": "high"},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "answer",
                    "schema": {"type": "object", "properties": {}},
                }
            },
        }
    )

    body = await convert_responses_request(payload, MediaFetcher())

    assert body["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
    assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}
    tools = cast("list[dict[str, object]]", body["tools"])
    declarations = cast("list[dict[str, object]]", tools[0]["functionDeclarations"])
    assert declarations[0]["name"] == "lookup"
    assert body["toolConfig"] == {"functionCallingConfig": {"mode": "ANY"}}
    config = cast("dict[str, object]", body["generationConfig"])
    assert config["maxOutputTokens"] == 64
    assert config["temperature"] == 0.2
    assert config["thinkingConfig"] == {"thinkingLevel": "HIGH"}
    assert config["responseMimeType"] == "application/json"


async def test_convert_function_call_roundtrip():
    payload = ResponsesRequest.model_validate(
        {
            "model": "gemini-3.8-flash",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "look it up"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": '{"q":"x"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": '{"result":"ok"}',
                },
            ],
        }
    )

    body = await convert_responses_request(payload, MediaFetcher())

    contents = cast("list[dict[str, object]]", body["contents"])
    assert contents[0]["role"] == "user"
    model_parts = cast("list[dict[str, object]]", contents[1]["parts"])
    assert model_parts[0]["functionCall"] == {
        "name": "lookup",
        "args": {"q": "x"},
        "id": "call_1",
    }
    user_parts = cast("list[dict[str, object]]", contents[2]["parts"])
    assert user_parts[0]["functionResponse"] == {
        "name": "lookup",
        "response": {"result": "ok"},
        "id": "call_1",
    }


async def test_store_and_previous_response_are_rejected():
    for field, value in (
        ("store", True),
        ("previous_response_id", "resp_old"),
    ):
        payload = ResponsesRequest.model_validate(
            {"model": "gemini-3.8-flash", "input": "hi", field: value}
        )
        with pytest.raises(Exception) as excinfo:
            await convert_responses_request(payload, MediaFetcher())
        assert field in str(excinfo.value)


def test_non_stream_response_has_output_items_and_usage():
    outcome = _outcome(
        Candidate(
            text="hello",
            thinking="think",
            function_calls=[{"id": "call_1", "name": "lookup", "args": {"q": "x"}}],
        )
    )

    response = responses_response(model="gemini-test", outcome=outcome)

    assert response["object"] == "response"
    assert response["status"] == "completed"
    items = cast("list[dict[str, object]]", response["output"])
    assert [item["type"] for item in items] == [
        "reasoning",
        "message",
        "function_call",
    ]
    usage = cast("dict[str, object]", response["usage"])
    assert usage["input_tokens"] == 11
    assert usage["output_tokens"] == 7
    assert usage["total_tokens"] == 18
    assert usage["input_tokens_details"] == {"cached_tokens": 3}
    assert usage["output_tokens_details"] == {"reasoning_tokens": 2}


def test_stream_renderer_emits_text_tools_and_completed_usage():
    renderer = ResponsesStreamRenderer("gemini-test")
    raw = renderer.start()
    raw += renderer.render(StreamEvent(kind="thinking", payload="why"))
    raw += renderer.render(StreamEvent(kind="body", payload="hello"))
    raw += renderer.render(
        StreamEvent(
            kind="tool_calls",
            payload=[{"id": "call_1", "name": "lookup", "args": {"q": "x"}}],
        )
    )
    raw += renderer.render(
        StreamEvent(
            kind="usage",
            payload={
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
                "prompt_tokens_details": {"cached_tokens": 3},
                "completion_tokens_details": {"reasoning_tokens": 2},
            },
            finish_reason="STOP",
        )
    )

    events = _sse_events(raw)
    names = [name for name, _ in events]
    assert names[0] == "response.created"
    assert "response.output_text.delta" in names
    assert "response.function_call_arguments.delta" in names
    assert names[-1] == "response.completed"
    completed = events[-1][1]["response"]
    assert completed["status"] == "completed"
    assert completed["usage"]["input_tokens"] == 11
    assert completed["output"][1]["content"][0]["text"] == "hello"


async def _post(app: FastAPI, path: str, body: dict[str, object]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(path, json=body)


async def test_route_non_stream_success(monkeypatch):
    monkeypatch.setattr(format_errors, "format_enabled", lambda _fmt: True)

    async def fake_generate(**_kwargs: Any) -> GenerationOutcome:
        return _outcome(Candidate(text="route-hello"))

    monkeypatch.setattr(routes_responses, "generate", fake_generate)
    app = FastAPI()
    app.include_router(routes_responses.router)
    app.dependency_overrides[get_client] = lambda: object()

    response = await _post(
        app,
        "/v1/responses",
        {"model": "gemini-3.8-flash", "input": "hello"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "response"
    assert body["usage"]["input_tokens"] == 11
    assert body["output"][0]["content"][0]["text"] == "route-hello"


async def test_route_upstream_error_has_openai_shape(monkeypatch):
    monkeypatch.setattr(format_errors, "format_enabled", lambda _fmt: True)

    async def fake_generate(**_kwargs: Any) -> GenerationOutcome:
        raise RequestError(400, "bad upstream")

    monkeypatch.setattr(routes_responses, "generate", fake_generate)
    app = FastAPI()
    app.include_router(routes_responses.router)
    app.dependency_overrides[get_client] = lambda: object()

    response = await _post(
        app,
        "/v1/responses",
        {"model": "gemini-3.8-flash", "input": "hello"},
    )

    assert response.status_code == 400
    assert "bad upstream" in response.json()["error"]["message"]
