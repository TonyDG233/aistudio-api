"""OpenAI Responses face contracts (C3 Wave 1).

These tests assert protocol-visible behavior only: what Gemini receives, which
events a Responses client observes, and how the shared error adapter renders
the domain errors.  No server, browser or network is started.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI

from aistudio_api.api import routes_responses
from aistudio_api.api.dependencies import get_routing_pipeline, require_api_key
from aistudio_api.api.error_mapping import error_payload
from aistudio_api.api.format_errors import format_error_response
from aistudio_api.application.contracts.responses import ResponsesRequest
from aistudio_api.application.format_converters.common import (
    DUMMY_THOUGHT_SIGNATURE,
    ConversionError,
)
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
from aistudio_api.application.signature_cache import signature_cache
from aistudio_api.domain.errors import (
    RequestError,
    SurfaceSwitchExhaustedError,
    TierRequiredError,
)
from aistudio_api.domain.models import Candidate, ModelOutput

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "aistudio_api"


def _as_dict(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value)


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value)


def _request(**overrides: Any) -> ResponsesRequest:
    body: dict[str, Any] = {"model": "gemini-test", "input": "hi"}
    body.update(overrides)
    return ResponsesRequest.model_validate(body)


async def _convert(**overrides: Any) -> dict[str, Any]:
    body = await convert_responses_request(_request(**overrides), MediaFetcher())
    return _as_dict(body)


def _outcome(
    candidate: Candidate,
    *,
    finish_reason: str = "STOP",
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
        finish_reason=finish_reason,
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
            events.append((event_name, _as_dict(json.loads(data))))
    return events


def _names(events: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [name for name, _payload in events]


# ========== Request conversion ==========


async def test_string_input_instructions_and_generation_config():
    body = await _convert(
        model="models/gemini-3.8-flash",
        input="hello",
        instructions="be brief",
        max_output_tokens=64,
        temperature=0.2,
        top_p=0.9,
        reasoning={"effort": "high", "summary": "auto"},
        text={
            "format": {
                "type": "json_schema",
                "name": "answer",
                "schema": {"type": "object", "properties": {}},
            }
        },
        seed=1234,
    )

    assert body["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
    assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}
    config = _as_dict(body["generationConfig"])
    assert config["maxOutputTokens"] == 64
    assert config["temperature"] == 0.2
    assert config["topP"] == 0.9
    assert config["thinkingConfig"] == {"thinkingLevel": "HIGH"}
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"] == {"type": "object", "properties": {}}
    # E-04: seed is Gemini-only and must not leak into the Gemini request.
    assert "seed" not in config


async def test_easy_input_message_without_type_maps_roles():
    body = await _convert(
        input=[
            {"role": "system", "content": "S"},
            {"role": "user", "content": "U"},
            {"role": "assistant", "content": [{"type": "output_text", "text": "A"}]},
        ]
    )

    assert body["systemInstruction"] == {"parts": [{"text": "S"}]}
    contents = _as_list(body["contents"])
    assert [item["role"] for item in contents] == ["user", "model"]
    assert contents[0]["parts"] == [{"text": "U"}]
    assert contents[1]["parts"] == [{"text": "A"}]


async def test_function_call_roundtrip_and_signature_cache():
    signature_cache.clear()
    signature_cache.put("call_sig", "sig-real")
    body = await _convert(
        input=[
            {
                "type": "function_call",
                "call_id": "call_sig",
                "name": "lookup",
                "arguments": '{"q":"x"}',
            },
            {
                "type": "function_call",
                "call_id": "call_unsigned",
                "name": "lookup",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call_sig",
                "output": '{"result":"ok"}',
            },
        ]
    )

    contents = _as_list(body["contents"])
    model_parts = _as_list(contents[0]["parts"])
    assert model_parts[0]["functionCall"] == {
        "name": "lookup",
        "args": {"q": "x"},
        "id": "call_sig",
    }
    # PG-01: cache hit first, dummy signature only when absent.
    assert model_parts[0]["thoughtSignature"] == "sig-real"
    assert model_parts[1]["thoughtSignature"] == DUMMY_THOUGHT_SIGNATURE

    user_parts = _as_list(contents[1]["parts"])
    assert user_parts[0]["functionResponse"] == {
        "name": "lookup",
        "response": {"result": "ok"},
        "id": "call_sig",
    }


async def test_pg04_arguments_and_outputs_are_always_structs():
    array_args = await _convert(
        input=[
            {
                "type": "function_call",
                "call_id": "call_arr",
                "name": "lookup",
                "arguments": "[1,2]",
            }
        ]
    )
    call = _as_dict(_as_list(_as_list(array_args["contents"])[0]["parts"])[0])[
        "functionCall"
    ]
    assert call["args"] == {"content": [1, 2]}

    broken_args = await _convert(
        input=[
            {
                "type": "function_call",
                "call_id": "call_broken",
                "name": "lookup",
                "arguments": "{not json",
            }
        ]
    )
    broken_call = _as_dict(_as_list(_as_list(broken_args["contents"])[0]["parts"])[0])[
        "functionCall"
    ]
    assert broken_call["args"] == {"content": "{not json"}

    scalar_output = await _convert(
        input=[
            {
                "type": "function_call_output",
                "call_id": "call_scalar",
                "output": '"ok"',
            }
        ]
    )
    response = _as_dict(
        _as_dict(_as_list(_as_list(scalar_output["contents"])[0]["parts"])[0])[
            "functionResponse"
        ]
    )
    assert response["response"] == {"result": "ok"}

    list_output = await _convert(
        input=[
            {
                "type": "function_call_output",
                "call_id": "call_list",
                "output": [1, 2],
            }
        ]
    )
    list_response = _as_dict(
        _as_dict(_as_list(_as_list(list_output["contents"])[0]["parts"])[0])[
            "functionResponse"
        ]
    )
    assert list_response["response"] == {"result": [1, 2]}

    text_output = await _convert(
        input=[
            {
                "type": "function_call_output",
                "call_id": "call_text",
                "output": [
                    {"type": "text", "text": "a"},
                    {"type": "text", "text": "b"},
                ],
            }
        ]
    )
    text_response = _as_dict(
        _as_dict(_as_list(_as_list(text_output["contents"])[0]["parts"])[0])[
            "functionResponse"
        ]
    )
    assert text_response["response"] == {"result": "a\nb"}


async def test_pg08_unknown_function_output_never_rejects():
    isolated = await _convert(
        input=[
            {
                "type": "function_call_output",
                "call_id": "missing_1",
                "output": "x",
            }
        ]
    )
    part = _as_dict(_as_list(_as_list(isolated["contents"])[0]["parts"])[0])
    assert part["functionResponse"] == {
        "name": "unknown_tool",
        "response": {"result": "x"},
        "id": "missing_1",
    }

    declared = await _convert(
        input=[
            {
                "type": "function_call_output",
                "call_id": "missing_2",
                "name": "lookup",
                "output": "x",
            }
        ]
    )
    declared_part = _as_dict(_as_list(_as_list(declared["contents"])[0]["parts"])[0])
    assert declared_part["functionResponse"]["name"] == "lookup"

    # Unknown id with exactly one pending call: infer from context.
    contextual = await _convert(
        input=[
            {
                "type": "function_call",
                "call_id": "call_ctx",
                "name": "lookup",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call_other",
                "output": "x",
            },
        ]
    )
    contextual_parts = _as_list(_as_list(contextual["contents"])[1]["parts"])
    assert contextual_parts[0]["functionResponse"]["name"] == "lookup"

    # Missing id is synthesised deterministically instead of rejected.
    missing_id = await _convert(input=[{"type": "function_call_output", "output": "x"}])
    missing_id_part = _as_dict(
        _as_list(_as_list(missing_id["contents"])[0]["parts"])[0]
    )
    assert str(missing_id_part["functionResponse"]["id"]).startswith("call_output_")


async def test_stateless_previous_response_and_item_reference():
    with pytest.raises(ConversionError) as previous:
        await _convert(previous_response_id="resp_old")
    assert previous.value.field == "previous_response_id"

    # Empty/whitespace previous_response_id is not state use.
    await _convert(previous_response_id="   ")

    with pytest.raises(ConversionError) as reference:
        await _convert(
            input=[
                {"type": "item_reference", "id": "msg_1"},
            ]
        )
    assert reference.value.field == "input[0].type"


async def test_lenient_optional_fields_are_accepted_and_store_is_stateless():
    body = await _convert(
        store=True,
        parallel_tool_calls=False,
        truncation="auto",
        include=["reasoning.encrypted_content"],
        metadata={"trace": "1"},
        user="user-1",
        service_tier="auto",
        reasoning={"effort": "low", "summary": "auto"},
        seed=7,
    )
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
    config = _as_dict(body["generationConfig"])
    assert config["thinkingConfig"] == {"thinkingLevel": "LOW"}
    assert "seed" not in config

    false_store = await _convert(store=False)
    assert false_store["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]


async def test_tools_only_function_and_tool_choice():
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    body = await _convert(
        tools=[
            {
                "type": "function",
                "name": "lookup",
                "description": "look things up",
                "parameters": schema,
            }
        ],
        tool_choice="required",
    )
    declarations = _as_list(_as_list(body["tools"])[0]["functionDeclarations"])
    assert declarations[0] == {
        "name": "lookup",
        "description": "look things up",
        "parametersJsonSchema": schema,
    }
    assert body["toolConfig"] == {"functionCallingConfig": {"mode": "ANY"}}

    named = await _convert(
        tools=[{"type": "function", "name": "lookup"}],
        tool_choice={"type": "function", "name": "lookup"},
    )
    assert named["toolConfig"] == {
        "functionCallingConfig": {
            "mode": "ANY",
            "allowedFunctionNames": ["lookup"],
        }
    }

    with pytest.raises(ConversionError) as unsupported:
        await _convert(tools=[{"type": "web_search"}])
    assert unsupported.value.field == "tools[0].type"


async def test_text_format_json_object_and_text():
    json_object = await _convert(text={"format": {"type": "json_object"}})
    object_config = _as_dict(json_object["generationConfig"])
    assert object_config["responseMimeType"] == "application/json"
    assert object_config["responseSchema"] == {"type": "object", "properties": {}}

    plain = await _convert(text={"format": {"type": "text"}})
    assert plain.get("generationConfig") is None


# ========== Non-stream response ==========


def test_non_stream_response_output_text_items_and_usage():
    response = responses_response(
        model="gemini-test",
        outcome=_outcome(
            Candidate(
                text="hello",
                thinking="think",
                function_calls=[{"id": "call_1", "name": "lookup", "args": {"q": "x"}}],
            )
        ),
    )

    assert str(response["id"]).startswith("resp_")
    assert response["object"] == "response"
    assert response["status"] == "completed"
    assert response["output_text"] == "hello"
    assert response["store"] is False
    assert response["incomplete_details"] is None
    items = _as_list(response["output"])
    assert [item["type"] for item in items] == [
        "reasoning",
        "message",
        "function_call",
    ]
    usage = _as_dict(response["usage"])
    assert usage["input_tokens"] == 11
    assert usage["output_tokens"] == 7
    assert usage["total_tokens"] == 18
    assert usage["input_tokens_details"] == {"cached_tokens": 3}
    assert usage["output_tokens_details"] == {"reasoning_tokens": 2}


def test_non_stream_max_tokens_is_incomplete():
    response = responses_response(
        model="gemini-test",
        outcome=_outcome(Candidate(text="cut"), finish_reason="MAX_TOKENS"),
    )
    assert response["status"] == "incomplete"
    assert response["incomplete_details"] == {"reason": "max_output_tokens"}


# ========== Streaming response ==========


def test_stream_text_event_sequence_reasoning_tools_and_indices():
    renderer = ResponsesStreamRenderer("gemini-test")
    raw = renderer.start()
    raw += renderer.render(StreamEvent(kind="thinking", payload="why"))
    raw += renderer.render(StreamEvent(kind="body", payload="hello"))
    raw += renderer.render(StreamEvent(kind="body", payload="!"))
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
    names = _names(events)
    assert names[0] == "response.created"
    assert names[-1] == "response.completed"
    assert names.index("response.output_item.added") < names.index(
        "response.content_part.added"
    )
    assert names.index("response.content_part.added") < names.index(
        "response.output_text.delta"
    )
    assert names.index("response.output_text.delta") < names.index(
        "response.output_text.done"
    )
    assert names.index("response.output_text.done") < names.index(
        "response.content_part.done"
    )
    message_done = next(
        index
        for index, (name, payload) in enumerate(events)
        if name == "response.output_item.done"
        and _as_dict(payload["item"])["type"] == "message"
    )
    assert names.index("response.content_part.done") < message_done
    assert names.index("response.reasoning_summary_part.added") < names.index(
        "response.reasoning_summary_text.delta"
    )
    assert names.index("response.reasoning_summary_text.delta") < names.index(
        "response.reasoning_summary_text.done"
    )
    assert names.index("response.reasoning_summary_text.done") < names.index(
        "response.reasoning_summary_part.done"
    )
    assert "response.function_call_arguments.delta" in names
    assert "response.function_call_arguments.done" in names

    # Sequence numbers are contiguous; keepalive comments are not part of this.
    assert [payload["sequence_number"] for _name, payload in events] == list(
        range(1, len(events) + 1)
    )

    added = [
        (_as_dict(payload["item"])["type"], payload["output_index"])
        for name, payload in events
        if name == "response.output_item.added"
    ]
    assert added == [("reasoning", 0), ("message", 1), ("function_call", 2)]

    completed = _as_dict(events[-1][1]["response"])
    assert completed["status"] == "completed"
    assert completed["output_text"] == "hello!"
    assert [item["type"] for item in _as_list(completed["output"])] == [
        "reasoning",
        "message",
        "function_call",
    ]


def test_stream_output_index_follows_real_arrival_order():
    renderer = ResponsesStreamRenderer("gemini-test")
    raw = renderer.start()
    raw += renderer.render(StreamEvent(kind="body", payload="hello"))
    raw += renderer.render(StreamEvent(kind="thinking", payload="why"))
    raw += renderer.render(
        StreamEvent(
            kind="tool_calls",
            payload=[{"id": "call_1", "name": "lookup", "args": {}}],
        )
    )
    raw += renderer.render(StreamEvent(kind="usage", payload={}, finish_reason="STOP"))

    events = _sse_events(raw)
    added = [
        (_as_dict(payload["item"])["type"], payload["output_index"])
        for name, payload in events
        if name == "response.output_item.added"
    ]
    assert added == [("message", 0), ("reasoning", 1), ("function_call", 2)]


def test_stream_max_tokens_terminates_incomplete():
    renderer = ResponsesStreamRenderer("gemini-test")
    raw = renderer.start()
    raw += renderer.render(StreamEvent(kind="body", payload="cut"))
    raw += renderer.render(
        StreamEvent(kind="usage", payload={}, finish_reason="MAX_TOKENS")
    )

    events = _sse_events(raw)
    assert _names(events)[-1] == "response.incomplete"
    response = _as_dict(events[-1][1]["response"])
    assert response["status"] == "incomplete"
    assert response["incomplete_details"] == {"reason": "max_output_tokens"}


def test_stream_failed_reuses_response_id_and_increments_sequence():
    renderer = ResponsesStreamRenderer("gemini-test")
    raw = renderer.start()
    created = _sse_events(raw)
    created_id = _as_dict(created[0][1]["response"])["id"]

    raw += renderer.render(StreamEvent(kind="body", payload="partial"))
    raw += renderer.failed(RuntimeError("boom"))

    events = _sse_events(raw)
    assert _names(events)[-1] == "response.failed"
    failed = _as_dict(events[-1][1]["response"])
    assert failed["id"] == created_id
    assert failed["status"] == "failed"
    assert failed["error"] == {"code": "server_error", "message": "boom"}
    assert [payload["sequence_number"] for _name, payload in events] == list(
        range(1, len(events) + 1)
    )
    assert renderer.failed(RuntimeError("again")) == ""


def test_stream_keepalive_comments_do_not_advance_sequence():
    renderer = ResponsesStreamRenderer("gemini-test")
    raw = ": keepalive\n\n" + renderer.start()
    raw += ": keepalive\n\n" + renderer.render(StreamEvent(kind="body", payload="hi"))
    events = _sse_events(raw)
    assert [payload["sequence_number"] for _name, payload in events] == list(
        range(1, len(events) + 1)
    )
    assert "sequence_number" not in ": keepalive\n\n"


# ========== Routes ==========


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_responses.router)
    app.dependency_overrides[routes_responses.require_responses] = lambda: None
    app.dependency_overrides[require_api_key] = lambda: None
    app.dependency_overrides[get_routing_pipeline] = lambda: object()
    return app


async def _post(app: FastAPI, path: str, body: dict[str, Any]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, json=body)


async def test_route_non_stream_success_and_atg_format(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_generate(**kwargs: Any) -> GenerationOutcome:
        captured.update(kwargs)
        return _outcome(Candidate(text="route-hello"))

    monkeypatch.setattr(routes_responses, "generate", fake_generate)
    monkeypatch.setattr(routes_responses, "format_media_allow_private", lambda: False)

    response = await _post(
        _app(),
        "/v1/responses",
        {
            "model": "models/gemini-test",
            "input": "hello",
            "store": True,
            "seed": 9,
            "parallel_tool_calls": False,
        },
    )

    assert response.status_code == 200
    body = _as_dict(response.json())
    assert body["object"] == "response"
    assert body["output_text"] == "route-hello"
    assert body["store"] is False
    assert captured["model_path"] == "gemini-test"
    assert captured["api_format"] == "responses"


async def test_route_conversion_error_has_responses_shape(monkeypatch):
    monkeypatch.setattr(routes_responses, "format_media_allow_private", lambda: False)
    response = await _post(
        _app(),
        "/v1/responses",
        {
            "model": "gemini-test",
            "input": "hi",
            "previous_response_id": "resp_old",
        },
    )

    assert response.status_code == 400
    error = _as_dict(_as_dict(response.json())["error"])
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "previous_response_id"


async def test_route_unknown_function_output_never_400(monkeypatch):
    async def fake_generate(**_kwargs: Any) -> GenerationOutcome:
        return _outcome(Candidate(text="ok"))

    monkeypatch.setattr(routes_responses, "generate", fake_generate)
    monkeypatch.setattr(routes_responses, "format_media_allow_private", lambda: False)
    response = await _post(
        _app(),
        "/v1/responses",
        {
            "model": "gemini-test",
            "input": [
                {
                    "type": "function_call_output",
                    "call_id": "unknown",
                    "output": "x",
                }
            ],
        },
    )
    assert response.status_code == 200


async def test_route_tier_error_has_four_fields(monkeypatch):
    async def fake_generate(**_kwargs: Any) -> GenerationOutcome:
        raise TierRequiredError(
            model="gemini-pro",
            tier="Pro",
            required_tier="Pro",
            current_tier="Free",
        )

    monkeypatch.setattr(routes_responses, "generate", fake_generate)
    monkeypatch.setattr(routes_responses, "format_media_allow_private", lambda: False)
    response = await _post(
        _app(), "/v1/responses", {"model": "gemini-pro", "input": "hi"}
    )

    assert response.status_code == 403
    error = _as_dict(_as_dict(response.json())["error"])
    assert error["type"] == "tier_required"
    assert error["tier"] == "Pro"
    assert error["model"] == "gemini-pro"
    assert error["required_tier"] == "Pro"
    assert error["current_tier"] == "Free"


async def test_route_5xx_protocol_mapping(monkeypatch):
    async def fake_generate(**_kwargs: Any) -> GenerationOutcome:
        raise RequestError(502, "upstream down")

    monkeypatch.setattr(routes_responses, "generate", fake_generate)
    monkeypatch.setattr(routes_responses, "format_media_allow_private", lambda: False)
    response = await _post(
        _app(), "/v1/responses", {"model": "gemini-test", "input": "hi"}
    )

    assert response.status_code == 502
    error = _as_dict(_as_dict(response.json())["error"])
    assert error["type"] == "api_error"
    assert "upstream down" in error["message"]


async def test_route_stream_full_sequence(monkeypatch):
    async def fake_stream(**_kwargs: Any):
        yield StreamEvent(kind="routing_wait", payload="resolving-instance")
        yield StreamEvent(kind="thinking", payload="why")
        yield StreamEvent(kind="body", payload="hello")
        yield StreamEvent(
            kind="usage",
            payload={
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "total_tokens": 5,
            },
            finish_reason="STOP",
        )

    monkeypatch.setattr(routes_responses, "stream_generation", fake_stream)
    monkeypatch.setattr(routes_responses, "format_media_allow_private", lambda: False)
    response = await _post(
        _app(),
        "/v1/responses",
        {"model": "gemini-test", "input": "hi", "stream": True},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    names = _names(events)
    assert names[0] == "response.created"
    assert names[-1] == "response.completed"
    assert "response.reasoning_summary_text.delta" in names
    assert "response.output_text.delta" in names
    completed = _as_dict(events[-1][1]["response"])
    assert completed["output_text"] == "hello"
    assert completed["usage"]["total_tokens"] == 5


async def test_route_stream_incomplete_on_max_tokens(monkeypatch):
    async def fake_stream(**_kwargs: Any):
        yield StreamEvent(kind="body", payload="cut")
        yield StreamEvent(kind="usage", payload={}, finish_reason="MAX_TOKENS")

    monkeypatch.setattr(routes_responses, "stream_generation", fake_stream)
    monkeypatch.setattr(routes_responses, "format_media_allow_private", lambda: False)
    response = await _post(
        _app(),
        "/v1/responses",
        {"model": "gemini-test", "input": "hi", "stream": True},
    )

    events = _sse_events(response.text)
    assert _names(events)[-1] == "response.incomplete"
    assert _as_dict(events[-1][1]["response"])["status"] == "incomplete"


async def test_route_stream_tier_error_before_first_event_is_real_403(monkeypatch):
    async def failing_stream(**_kwargs: Any):
        raise TierRequiredError(
            model="gemini-pro",
            tier="Pro",
            required_tier="Pro",
            current_tier="Free",
        )
        yield StreamEvent(kind="body", payload="")

    monkeypatch.setattr(routes_responses, "stream_generation", failing_stream)
    monkeypatch.setattr(routes_responses, "format_media_allow_private", lambda: False)
    response = await _post(
        _app(),
        "/v1/responses",
        {"model": "gemini-pro", "input": "hi", "stream": True},
    )

    assert response.status_code == 403
    error = _as_dict(_as_dict(response.json())["error"])
    assert error["type"] == "tier_required"
    assert error["model"] == "gemini-pro"
    assert error["required_tier"] == "Pro"
    assert error["current_tier"] == "Free"


async def test_route_stream_first_event_error_returns_real_http(monkeypatch):
    async def failing_stream(**_kwargs: Any):
        raise RequestError(400, "bad upstream")
        yield StreamEvent(kind="body", payload="")

    monkeypatch.setattr(routes_responses, "stream_generation", failing_stream)
    monkeypatch.setattr(routes_responses, "format_media_allow_private", lambda: False)
    response = await _post(
        _app(),
        "/v1/responses",
        {"model": "gemini-test", "input": "hi", "stream": True},
    )

    assert response.status_code == 400
    error = _as_dict(_as_dict(response.json())["error"])
    assert "bad upstream" in error["message"]


async def test_route_stream_empty_is_502(monkeypatch):
    async def empty_stream(**_kwargs: Any):
        for _item in ():
            yield StreamEvent(kind="body", payload="")

    monkeypatch.setattr(routes_responses, "stream_generation", empty_stream)
    monkeypatch.setattr(routes_responses, "format_media_allow_private", lambda: False)
    response = await _post(
        _app(),
        "/v1/responses",
        {"model": "gemini-test", "input": "hi", "stream": True},
    )

    assert response.status_code == 502
    error = _as_dict(_as_dict(response.json())["error"])
    assert "no stream events" in error["message"]


async def test_route_stream_failure_after_first_event_is_http_200(monkeypatch):
    async def late_failure(**_kwargs: Any):
        yield StreamEvent(kind="body", payload="partial")
        raise RequestError(500, "late failure")

    monkeypatch.setattr(routes_responses, "stream_generation", late_failure)
    monkeypatch.setattr(routes_responses, "format_media_allow_private", lambda: False)
    response = await _post(
        _app(),
        "/v1/responses",
        {"model": "gemini-test", "input": "hi", "stream": True},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert _names(events)[0] == "response.created"
    assert _names(events)[-1] == "response.failed"
    created_id = _as_dict(events[0][1]["response"])["id"]
    failed = _as_dict(events[-1][1]["response"])
    assert failed["id"] == created_id
    assert "late failure" in failed["error"]["message"]


async def test_route_keepalive_before_first_event(monkeypatch):
    async def fake_stream(**_kwargs: Any):
        yield StreamEvent(kind="body", payload="hi")
        yield StreamEvent(kind="usage", payload={}, finish_reason="STOP")

    monkeypatch.setattr(routes_responses, "stream_generation", fake_stream)
    monkeypatch.setattr(routes_responses, "preread_window_sec", lambda: 0.0)
    monkeypatch.setattr(routes_responses, "keepalive_interval_sec", lambda: 10.0)
    monkeypatch.setattr(routes_responses, "format_media_allow_private", lambda: False)
    response = await _post(
        _app(),
        "/v1/responses",
        {"model": "gemini-test", "input": "hi", "stream": True},
    )

    assert response.status_code == 200
    assert response.text.startswith("event: response.created")
    assert ": keepalive\n\n" in response.text
    events = _sse_events(response.text)
    assert [payload["sequence_number"] for _name, payload in events] == list(
        range(1, len(events) + 1)
    )


async def test_route_input_tokens(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_count_tokens(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(total_tokens=42)

    monkeypatch.setattr(routes_responses, "count_tokens", fake_count_tokens)
    monkeypatch.setattr(routes_responses, "format_media_allow_private", lambda: False)
    response = await _post(
        _app(),
        "/v1/responses/input_tokens",
        {"model": "chat/gemini-test", "input": "hi"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "object": "response.input_tokens",
        "input_tokens": 42,
    }
    assert captured["model_path"] == "gemini-test"
    assert captured["api_format"] == "responses"


def test_model_prefix_strips_only_models_and_chat():
    assert (
        routes_responses.strip_responses_model_prefix("models/gemini-x") == "gemini-x"
    )
    assert routes_responses.strip_responses_model_prefix("chat/gemini-x") == "gemini-x"
    assert (
        routes_responses.strip_responses_model_prefix("models/chat/gemini-x")
        == "gemini-x"
    )
    assert (
        routes_responses.strip_responses_model_prefix("responses/gemini-x")
        == "responses/gemini-x"
    )


def test_atg_mode_is_resolved_from_responses_face(monkeypatch):
    from aistudio_api.application.atg import config as atg_config

    monkeypatch.setattr(
        atg_config,
        "get_features_config",
        lambda: {
            "atg": {
                "mode": "off",
                "mode_by_format": {"responses": "required"},
            }
        },
    )
    assert atg_config.resolve_atg_mode("responses") == "required"


# ========== Shared error adapter (D-04/D-06) ==========


def test_tier_error_adapter_has_four_fields_for_responses():
    status, body = error_payload(
        TierRequiredError(
            model="gemini-pro",
            tier="Pro",
            required_tier="Pro",
            current_tier="Free",
        ),
        "responses",
    )
    assert status == 403
    error = _as_dict(_as_dict(body)["error"])
    assert error["type"] == "tier_required"
    for field in ("tier", "model", "required_tier", "current_tier"):
        assert field in error


def test_429_exhaustion_maps_to_503_for_responses():
    response = format_error_response(
        SurfaceSwitchExhaustedError(surface="chat"), "responses"
    )
    assert response.status_code == 503


# ========== Layer boundaries ==========


def _forbidden_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(("fastapi", "aistudio_api.api")):
                    found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(("fastapi", "aistudio_api.api")):
                found.append(module)
    return found


def test_application_responses_modules_do_not_import_api_or_fastapi():
    files = [
        SRC_ROOT / "application" / "contracts" / "responses.py",
        SRC_ROOT / "application" / "format_converters" / "responses_request.py",
        SRC_ROOT / "application" / "format_converters" / "responses_response.py",
    ]
    for path in files:
        assert _forbidden_imports(path) == [], str(path)
