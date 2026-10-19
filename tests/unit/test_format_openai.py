"""Behavior contracts for the OpenAI Chat Completions format face (WP8 Phase B).

These tests assert protocol-visible request/response shapes, not converter
internals: what Gemini receives, what OpenAI clients observe, and which errors
are surfaced.
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from aistudio_api.api import routes_openai
from aistudio_api.api.dependencies import get_client, get_runtime_state
from aistudio_api.api.schemas_openai import OpenAIChatRequest
from aistudio_api.application.format_converters.common import (
    DUMMY_THOUGHT_SIGNATURE,
    ConversionError,
)
from aistudio_api.application.format_converters.media import MediaFetcher
from aistudio_api.application.format_converters.openai_request import (
    convert_openai_request,
)
from aistudio_api.application.format_converters.openai_response import (
    OpenAIStreamRenderer,
    openai_response,
)
from aistudio_api.application.format_converters.signature_cache import (
    signature_cache,
)
from aistudio_api.application.generation_service import (
    GenerationOutcome,
    StreamEvent,
)
from aistudio_api.domain.models import Candidate, GeneratedImage, ModelOutput
from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

PUBLIC_IMAGE_URL = "http://93.184.216.34/image.png"


def _as_dict(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value)


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value)


def _request(**overrides: Any) -> OpenAIChatRequest:
    body: dict[str, Any] = {
        "model": "gemini-test",
        "messages": [{"role": "user", "content": "hi"}],
    }
    body.update(overrides)
    return OpenAIChatRequest.model_validate(body)


async def _convert(
    media: MediaFetcher | None = None, **overrides: Any
) -> dict[str, Any]:
    return await convert_openai_request(_request(**overrides), media or MediaFetcher())


def _image_transport(
    *,
    content_type: str = "image/png",
    content: bytes = b"image-bytes",
) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": content_type},
            content=content,
        )

    return httpx.MockTransport(handler)


# ========== Request conversion ==========


@pytest.mark.asyncio
async def test_system_and_developer_merge_and_consecutive_roles_merge():
    body = await _convert(
        messages=[
            {"role": "system", "content": "S1"},
            {"role": "developer", "content": [{"type": "text", "text": "S2"}]},
            {"role": "user", "content": "U1"},
            {"role": "user", "content": [{"type": "text", "text": "U2"}]},
            {"role": "assistant", "content": "A1"},
            {"role": "assistant", "content": [{"type": "text", "text": "A2"}]},
        ]
    )

    system = _as_dict(body["systemInstruction"])
    assert _as_list(system["parts"])[0]["text"] == "S1\nS2"

    contents = _as_list(body["contents"])
    assert [item["role"] for item in contents] == ["user", "model"]
    assert [part["text"] for part in contents[0]["parts"]] == ["U1", "U2"]
    assert [part["text"] for part in contents[1]["parts"]] == ["A1", "A2"]


@pytest.mark.asyncio
async def test_assistant_tool_calls_and_tool_results_roundtrip():
    signature_cache.put("call_1", "sig-call-1")
    signature_cache.put("call_2", "sig-call-2")
    body = await _convert(
        messages=[
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "Paris"}',
                        },
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "get_time", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": '{"temp": 20}'},
            {"role": "tool", "tool_call_id": "call_2", "content": "noon"},
        ]
    )

    contents = _as_list(body["contents"])
    assert [item["role"] for item in contents] == ["user", "model", "user"]

    model_calls = [part["functionCall"] for part in contents[1]["parts"]]
    assert [call["name"] for call in model_calls] == ["get_weather", "get_time"]
    assert [call["id"] for call in model_calls] == ["call_1", "call_2"]
    assert model_calls[0]["args"] == {"city": "Paris"}
    assert [part["thoughtSignature"] for part in contents[1]["parts"]] == [
        "sig-call-1",
        "sig-call-2",
    ]

    results = [part["functionResponse"] for part in contents[2]["parts"]]
    assert [result["name"] for result in results] == ["get_weather", "get_time"]
    assert [result["id"] for result in results] == ["call_1", "call_2"]
    assert results[0]["response"] == {"temp": 20}
    assert results[1]["response"] == {"result": "noon"}


@pytest.mark.asyncio
async def test_historical_tool_call_without_signature_uses_dummy_signature():
    body = await _convert(
        messages=[
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_unsigned",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city":"Paris"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_unsigned",
                "content": '{"temp":20}',
            },
        ]
    )
    contents = _as_list(body["contents"])
    model_part = _as_dict(contents[1]["parts"][0])
    assert model_part["thoughtSignature"] == DUMMY_THOUGHT_SIGNATURE
    result = _as_dict(_as_dict(contents[2]["parts"][0])["functionResponse"])
    assert result["id"] == "call_unsigned"


@pytest.mark.asyncio
async def test_unresolved_tool_call_id_is_rejected():
    with pytest.raises(ConversionError) as exc_info:
        await _convert(
            messages=[
                {"role": "user", "content": "weather?"},
                {"role": "tool", "tool_call_id": "call_missing", "content": "x"},
            ]
        )
    assert exc_info.value.field == "messages[1].tool_call_id"


@pytest.mark.asyncio
async def test_malformed_tool_arguments_is_rejected():
    with pytest.raises(ConversionError) as exc_info:
        await _convert(
            messages=[
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "f", "arguments": "{not json"},
                        }
                    ],
                },
            ]
        )
    assert exc_info.value.field == "messages[1].tool_calls[0].function.arguments"


@pytest.mark.asyncio
async def test_data_url_image_becomes_inline_data():
    payload = b"png-bytes"
    url = "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
    body = await _convert(
        messages=[
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": url}}],
            }
        ]
    )

    part = _as_list(_as_list(body["contents"])[0]["parts"])[0]
    assert part["inlineData"] == {
        "mimeType": "image/png",
        "data": base64.b64encode(payload).decode("ascii"),
    }


@pytest.mark.asyncio
async def test_http_image_is_fetched_through_media_transport():
    media = MediaFetcher(transport=_image_transport())
    body = await _convert(
        media,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": PUBLIC_IMAGE_URL}}
                ],
            }
        ],
    )

    part = _as_list(_as_list(body["contents"])[0]["parts"])[0]
    assert part["inlineData"]["mimeType"] == "image/png"
    assert part["inlineData"]["data"] == base64.b64encode(b"image-bytes").decode(
        "ascii"
    )


@pytest.mark.asyncio
async def test_non_image_remote_content_type_is_rejected():
    media = MediaFetcher(transport=_image_transport(content_type="text/html"))
    with pytest.raises(ConversionError) as exc_info:
        await _convert(
            media,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": PUBLIC_IMAGE_URL}}
                    ],
                }
            ],
        )
    assert exc_info.value.field == "messages[0].content[0]"


@pytest.mark.asyncio
async def test_private_image_url_is_blocked_by_default_but_allowed_on_request():
    message = {
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "http://127.0.0.1/logo.png"}}
        ],
    }
    with pytest.raises(ConversionError):
        await _convert(MediaFetcher(transport=_image_transport()), messages=[message])

    body = await _convert(
        MediaFetcher(allow_private=True, transport=_image_transport()),
        messages=[message],
    )
    part = _as_list(_as_list(body["contents"])[0]["parts"])[0]
    assert part["inlineData"]["data"] == base64.b64encode(b"image-bytes").decode(
        "ascii"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "part",
    [
        {"type": "input_audio", "input_audio": {"data": "x", "format": "wav"}},
        {"type": "file", "file": {"file_id": "file-1"}},
        {"type": "video_url", "video_url": {"url": "http://example.com/x"}},
    ],
)
async def test_unsupported_content_parts_are_rejected(part: dict[str, object]):
    with pytest.raises(ConversionError) as exc_info:
        await _convert(
            messages=[{"role": "user", "content": [part]}],
        )
    assert exc_info.value.field == "messages[0].content[0]"


@pytest.mark.asyncio
async def test_n_greater_than_one_is_rejected():
    with pytest.raises(ConversionError) as exc_info:
        await _convert(n=2)
    assert exc_info.value.field == "n"


@pytest.mark.asyncio
async def test_tools_and_legacy_functions_become_function_declarations():
    schema = {"type": "object", "properties": {"city": {"type": "string"}}}
    body = await _convert(
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Weather lookup",
                    "parameters": schema,
                },
            }
        ],
        functions=[
            {
                "name": "legacy_tool",
                "description": "Legacy",
                "parameters": {"type": "object"},
            }
        ],
    )

    declarations = _as_list(_as_list(body["tools"])[0]["functionDeclarations"])
    assert declarations[0] == {
        "name": "get_weather",
        "description": "Weather lookup",
        "parametersJsonSchema": schema,
    }
    assert declarations[1]["name"] == "legacy_tool"
    assert declarations[1]["parametersJsonSchema"] == {"type": "object"}
    assert "parameters" not in declarations[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("choice", "expected_mode"),
    [("auto", "AUTO"), ("none", "NONE"), ("required", "ANY")],
)
async def test_tool_choice_modes(choice: str, expected_mode: str):
    body = await _convert(tool_choice=choice)
    assert body["toolConfig"] == {"functionCallingConfig": {"mode": expected_mode}}


@pytest.mark.asyncio
async def test_tool_choice_named_function_and_legacy_function_call():
    tools = [{"type": "function", "function": {"name": "lookup"}}]
    body = await _convert(
        tools=tools,
        tool_choice={"type": "function", "function": {"name": "lookup"}},
    )
    assert body["toolConfig"] == {
        "functionCallingConfig": {
            "mode": "ANY",
            "allowedFunctionNames": ["lookup"],
        }
    }

    legacy = await _convert(tools=tools, function_call={"name": "lookup"})
    assert legacy["toolConfig"] == body["toolConfig"]


@pytest.mark.asyncio
async def test_response_format_json_schema_and_json_object():
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    structured = await _convert(
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": schema},
        }
    )
    config = _as_dict(structured["generationConfig"])
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"] == schema

    open_object = await _convert(response_format={"type": "json_object"})
    open_config = _as_dict(open_object["generationConfig"])
    assert open_config["responseMimeType"] == "application/json"
    assert open_config["responseSchema"] == {"type": "object", "properties": {}}

    plain = await _convert(response_format={"type": "text"})
    assert "responseMimeType" not in _as_dict(plain.get("generationConfig", {}))


@pytest.mark.asyncio
async def test_generation_config_maps_tokens_stop_and_reasoning_effort():
    body = await _convert(
        max_tokens=10,
        max_completion_tokens=20,
        temperature=0.4,
        top_p=0.9,
        top_k=40,
        stop=["STOP", "END"],
        reasoning_effort="high",
    )
    config = _as_dict(body["generationConfig"])
    assert config["maxOutputTokens"] == 20
    assert config["temperature"] == 0.4
    assert config["topP"] == 0.9
    assert config["topK"] == 40
    assert config["stopSequences"] == ["STOP", "END"]
    assert config["thinkingConfig"] == {"thinkingLevel": "HIGH"}


# ========== Non-stream response ==========


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


def test_non_stream_response_fields_finish_reason_and_usage():
    response = openai_response(
        model="client-model",
        outcome=_outcome(
            Candidate(text="hello", thinking="because"),
            finish_reason="MAX_TOKENS",
        ),
    )

    assert response["object"] == "chat.completion"
    assert str(response["id"]).startswith("chatcmpl-")
    assert isinstance(response["created"], int)
    assert response["model"] == "client-model"

    choice = _as_dict(_as_list(response["choices"])[0])
    assert choice["finish_reason"] == "length"
    message = _as_dict(choice["message"])
    assert message["role"] == "assistant"
    assert message["content"] == "hello"
    assert message["reasoning_content"] == "because"

    usage = _as_dict(response["usage"])
    assert usage["prompt_tokens"] == 11
    assert usage["completion_tokens"] == 7
    assert usage["total_tokens"] == 18
    assert usage["prompt_tokens_details"] == {"cached_tokens": 3}
    assert usage["completion_tokens_details"] == {"reasoning_tokens": 2}


def test_non_stream_tool_calls_set_finish_reason_and_json_arguments():
    response = openai_response(
        model="client-model",
        outcome=_outcome(
            Candidate(
                function_calls=[{"id": "call_9", "name": "lookup", "args": {"q": "x"}}]
            )
        ),
    )

    choice = _as_dict(_as_list(response["choices"])[0])
    assert choice["finish_reason"] == "tool_calls"
    message = _as_dict(choice["message"])
    assert message["content"] == ""
    call = _as_dict(_as_list(message["tool_calls"])[0])
    assert call["id"] == "call_9"
    assert call["type"] == "function"
    assert call["function"]["name"] == "lookup"
    assert json.loads(cast("str", call["function"]["arguments"])) == {"q": "x"}


def test_non_stream_images_become_markdown_data_urls():
    response = openai_response(
        model="client-model",
        outcome=_outcome(
            Candidate(
                text="caption",
                images=[GeneratedImage(mime="image/png", data=b"png", size=3)],
            )
        ),
    )
    message = _as_dict(_as_dict(_as_list(response["choices"])[0])["message"])
    assert message["content"] == (
        "caption\n![Generated Image](data:image/png;base64,cG5n)"
    )


# ========== Streaming response ==========


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


def test_stream_chunk_sequence_role_thinking_tools_finish_usage_done():
    renderer = OpenAIStreamRenderer("client-model")
    raw = renderer.render(StreamEvent(kind="thinking", payload="why"))
    raw += renderer.render(StreamEvent(kind="body", payload="answer"))
    raw += renderer.render(
        StreamEvent(
            kind="tool_calls",
            payload=[
                {"id": "call_1", "name": "first", "args": {"a": 1}},
                {"id": "call_2", "name": "second", "args": {"b": 2}},
            ],
        )
    )
    raw += renderer.render(
        StreamEvent(
            kind="usage",
            payload={
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "total_tokens": 5,
            },
            finish_reason="STOP",
        )
    )
    raw += renderer.close()

    chunks = _sse_payloads(raw)
    assert len(chunks) == 5
    assert chunks[0]["object"] == "chat.completion.chunk"
    assert chunks[0]["model"] == "client-model"

    first_delta = _as_dict(_as_dict(_as_list(chunks[0]["choices"])[0])["delta"])
    assert first_delta["role"] == "assistant"
    assert first_delta["reasoning_content"] == "why"

    second = _as_dict(_as_list(chunks[1]["choices"])[0])
    assert "role" not in second["delta"]
    assert second["delta"]["content"] == "answer"
    assert second["finish_reason"] is None

    first_call = _as_dict(
        _as_dict(_as_list(chunks[2]["choices"])[0])["delta"]["tool_calls"][0]
    )
    second_call = _as_dict(
        _as_dict(_as_list(chunks[3]["choices"])[0])["delta"]["tool_calls"][0]
    )
    assert first_call["index"] == 0
    assert first_call["id"] == "call_1"
    assert first_call["function"]["name"] == "first"
    assert json.loads(cast("str", first_call["function"]["arguments"])) == {"a": 1}
    assert second_call["index"] == 1
    assert second_call["id"] == "call_2"

    terminal = _as_dict(_as_list(chunks[4]["choices"])[0])
    assert terminal["delta"] == {}
    assert terminal["finish_reason"] == "tool_calls"
    assert chunks[4]["usage"]["total_tokens"] == 5
    assert raw.rstrip().endswith("data: [DONE]")


# ========== Routes ==========


def _app(*, with_client: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(routes_openai.router)
    app.dependency_overrides[routes_openai.require_openai] = lambda: None
    app.dependency_overrides[get_runtime_state] = lambda: SimpleNamespace(client=None)
    if with_client:
        app.dependency_overrides[get_client] = lambda: object()
    return app


async def _post(app: FastAPI, path: str, body: dict[str, Any]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, json=body)


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_route_format_disabled_returns_permission_error(monkeypatch):
    monkeypatch.setattr(
        "aistudio_api.api.format_errors.format_enabled", lambda _fmt: False
    )
    app = FastAPI()
    app.include_router(routes_openai.router)
    app.dependency_overrides[get_client] = lambda: object()

    response = await _post(
        app,
        "/v1/chat/completions",
        {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 403
    detail = _as_dict(_as_dict(response.json())["detail"])
    assert "features.formats.openai" in detail["message"]


@pytest.mark.asyncio
async def test_route_non_stream_success(monkeypatch):
    async def fake_generate(**_kwargs: Any) -> GenerationOutcome:
        return _outcome(Candidate(text="route-hello"))

    monkeypatch.setattr(routes_openai, "generate", fake_generate)
    monkeypatch.setattr(
        "aistudio_api.api.routes_openai.format_media_allow_private",
        lambda: False,
    )

    response = await _post(
        _app(),
        "/v1/chat/completions",
        {
            "model": "models/gemini-test",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 200
    body = _as_dict(response.json())
    assert body["object"] == "chat.completion"
    assert body["model"] == "gemini-test"
    choice = _as_dict(_as_list(body["choices"])[0])
    assert choice["message"]["content"] == "route-hello"


@pytest.mark.asyncio
async def test_route_conversion_error_has_openai_shape(monkeypatch):
    monkeypatch.setattr(
        "aistudio_api.api.routes_openai.format_media_allow_private",
        lambda: False,
    )
    response = await _post(
        _app(),
        "/v1/chat/completions",
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "n": 2,
        },
    )

    assert response.status_code == 400
    error = _as_dict(_as_dict(response.json())["error"])
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "n"


@pytest.mark.asyncio
async def test_route_stream_first_event_error_returns_http_status(monkeypatch):
    async def failing_stream(**_kwargs: Any):
        raise RuntimeError("upstream exploded")
        yield  # pragma: no cover

    monkeypatch.setattr(routes_openai, "stream_generation", failing_stream)
    monkeypatch.setattr(
        "aistudio_api.api.routes_openai.format_media_allow_private",
        lambda: False,
    )

    response = await _post(
        _app(),
        "/v1/chat/completions",
        {
            "model": "m",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 500
    error = _as_dict(_as_dict(response.json())["error"])
    assert error["type"] == "api_error"
    assert "upstream exploded" in error["message"]


@pytest.mark.asyncio
async def test_route_models_list_and_single_model(monkeypatch):
    async def fake_models(**_kwargs: Any) -> list[dict[str, object]]:
        return [{"id": "models/gemini-x"}, {"name": "gemini-y"}]

    async def fake_model(model_id: str, **_kwargs: Any) -> dict[str, object] | None:
        if model_id.removeprefix("models/") == "gemini-x":
            return {"id": "models/gemini-x"}
        return None

    monkeypatch.setattr(model_discovery, "get_models", fake_models)
    monkeypatch.setattr(model_discovery, "get_model_by_id", fake_model)
    app = _app(with_client=False)

    listing = await _get(app, "/v1/models")
    assert listing.status_code == 200
    body = _as_dict(listing.json())
    assert body["object"] == "list"
    assert [item["id"] for item in body["data"]] == ["gemini-x", "gemini-y"]
    assert body["data"][0] == {
        "id": "gemini-x",
        "object": "model",
        "created": 0,
        "owned_by": "aistudio",
    }

    single = await _get(app, "/v1/models/gemini-x")
    assert single.status_code == 200
    assert single.json()["id"] == "gemini-x"

    missing = await _get(app, "/v1/models/nope")
    assert missing.status_code == 404
    error = _as_dict(_as_dict(missing.json())["error"])
    assert error["type"] == "not_found_error"


def test_require_openai_raises_http_exception_when_disabled(monkeypatch):
    monkeypatch.setattr(
        "aistudio_api.api.format_errors.format_enabled", lambda _fmt: False
    )
    with pytest.raises(HTTPException) as exc_info:
        routes_openai.require_openai()
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_tool_call_thought_signature_is_cached_and_reattached():
    from aistudio_api.application.format_converters.signature_cache import (
        signature_cache,
    )

    signature_cache.clear()
    outcome = GenerationOutcome(
        output=ModelOutput(
            candidates=[
                Candidate(
                    function_calls=[
                        {
                            "id": "call_sig1",
                            "name": "get_weather",
                            "args": {"city": "Paris"},
                            "thought_signature": "sig-abc",
                        }
                    ]
                )
            ],
            usage={},
        ),
        model="models/m",
        finish_reason="STOP",
        response_id=None,
        surface="chat",
    )
    rendered = _as_dict(openai_response(model="m", outcome=outcome))
    choices = cast("list[dict[str, Any]]", rendered["choices"])
    message = _as_dict(choices[0]["message"])
    calls = cast("list[dict[str, Any]]", message["tool_calls"])
    assert calls[0]["id"] == "call_sig1"

    history = OpenAIChatRequest.model_validate(
        {
            "model": "m",
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_sig1",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city":"Paris"}',
                            },
                        }
                    ],
                }
            ],
        }
    )
    body = await convert_openai_request(history, MediaFetcher())
    contents = cast("list[dict[str, object]]", body["contents"])
    parts = cast("list[dict[str, object]]", contents[0]["parts"])
    assert parts[0]["thoughtSignature"] == "sig-abc"
