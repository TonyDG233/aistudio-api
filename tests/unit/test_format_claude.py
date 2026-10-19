"""Protocol-contract tests for the WP8 Claude Messages format face."""

from __future__ import annotations

import json
from typing import cast

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from aistudio_api.api import format_errors, routes_claude
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.api.schemas_claude import ClaudeMessagesRequest
from aistudio_api.application.format_converters.claude_request import (
    convert_claude_request,
)
from aistudio_api.application.format_converters.claude_response import (
    ClaudeStreamRenderer,
    claude_response,
)
from aistudio_api.application.format_converters.common import (
    DUMMY_THOUGHT_SIGNATURE,
    ConversionError,
)
from aistudio_api.application.format_converters.media import MediaFetcher
from aistudio_api.application.format_converters.signature_cache import (
    signature_cache,
)
from aistudio_api.application.generation_service import (
    GenerationOutcome,
    StreamEvent,
)
from aistudio_api.domain.models import Candidate, GeneratedImage, ModelOutput

MODEL = "claude-3-5-sonnet"


def _request(**overrides: object) -> ClaudeMessagesRequest:
    body: dict[str, object] = {
        "model": MODEL,
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "hi"}],
    }
    body.update(overrides)
    return ClaudeMessagesRequest.model_validate(body)


async def _convert(
    *, media: MediaFetcher | None = None, **overrides: object
) -> dict[str, object]:
    return await convert_claude_request(_request(**overrides), media or MediaFetcher())


def _contents(body: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", body["contents"])


def _parts(body: dict[str, object], index: int = 0) -> list[dict[str, object]]:
    contents = _contents(body)
    return cast("list[dict[str, object]]", contents[index]["parts"])


def _dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


def _image_transport() -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "image/png"}, content=b"\x89PNG"
        )

    return httpx.MockTransport(handler)


def _outcome(
    candidate: Candidate,
    *,
    finish_reason: str = "STOP",
    usage: dict[str, object] | None = None,
) -> GenerationOutcome:
    return GenerationOutcome(
        output=ModelOutput(candidates=[candidate], usage=usage or {}),
        model="gemini-3.7-flash",
        finish_reason=finish_reason,
        response_id=None,
        surface="chat",
    )


def _parse_sse(raw: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        name = ""
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: ") :])
        payload: object = json.loads("\n".join(data_lines)) if data_lines else {}
        assert isinstance(payload, dict)
        events.append((name, cast("dict[str, object]", payload)))
    return events


# ---------------------------------------------------------------------------
# Request conversion
# ---------------------------------------------------------------------------


async def test_system_string_and_text_block_array():
    string_body = await _convert(system="You are helpful")
    assert string_body["systemInstruction"] == {"parts": [{"text": "You are helpful"}]}

    block_body = await _convert(
        system=[{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]
    )
    assert block_body["systemInstruction"] == {"parts": [{"text": "first\nsecond"}]}


async def test_system_non_text_block_is_rejected():
    with pytest.raises(ConversionError):
        await _convert(system=[{"type": "image", "source": {"type": "base64"}}])


async def test_text_and_consecutive_same_role_messages_merge():
    body = await _convert(
        messages=[
            {"role": "user", "content": "one"},
            {"role": "user", "content": [{"type": "text", "text": "two"}]},
            {"role": "assistant", "content": "three"},
        ]
    )
    contents = _contents(body)
    assert [item["role"] for item in contents] == ["user", "model"]
    user_parts = _parts(body, 0)
    assert [part.get("text") for part in user_parts] == ["one", "two"]


async def test_image_base64_becomes_inline_data():
    body = await _convert(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "aGk=",
                        },
                    },
                ],
            }
        ]
    )
    part = _parts(body)[1]
    assert part["inlineData"] == {"mimeType": "image/png", "data": "aGk="}


async def test_image_url_is_fetched_and_private_urls_are_gated():
    public_url = "http://93.184.216.34/picture.png"
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "url", "url": public_url},
                }
            ],
        }
    ]
    media = MediaFetcher(transport=_image_transport())
    body = await convert_claude_request(_request(messages=messages), media)
    inline = _dict(_parts(body)[0]["inlineData"])
    assert inline["mimeType"] == "image/png"
    assert inline["data"] == "iVBORw=="

    private_request = _request(
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "url", "url": "http://127.0.0.1/x.png"},
                    }
                ],
            }
        ]
    )
    with pytest.raises(ConversionError):
        await convert_claude_request(
            private_request, MediaFetcher(transport=_image_transport())
        )

    allowed = await convert_claude_request(
        private_request,
        MediaFetcher(allow_private=True, transport=_image_transport()),
    )
    assert "inlineData" in _parts(allowed)[0]


async def test_tool_use_and_tool_result_roundtrip():
    signature_cache.put("toolu_01", "sig-toolu-01")
    body = await _convert(
        messages=[
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_01",
                        "name": "lookup",
                        "input": {"query": "x"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_01",
                        "content": "found",
                    }
                ],
            },
        ]
    )
    assert _contents(body)[0]["role"] == "model"
    call = _dict(_parts(body, 0)[0]["functionCall"])
    assert call == {"id": "toolu_01", "name": "lookup", "args": {"query": "x"}}

    response_part = _parts(body, 1)[0]
    function_response = _dict(response_part["functionResponse"])
    assert function_response["id"] == "toolu_01"
    assert function_response["name"] == "lookup"
    assert function_response["response"] == {"result": "found"}


async def test_tool_result_is_error_and_images_use_response_parts():
    signature_cache.put("toolu_err", "sig-toolu-err")
    body = await _convert(
        messages=[
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_err",
                        "name": "render",
                        "input": {},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_err",
                        "is_error": True,
                        "content": [
                            {"type": "text", "text": "boom"},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": "aGk=",
                                },
                            },
                        ],
                    }
                ],
            },
        ]
    )
    function_response = _dict(_parts(body, 1)[0]["functionResponse"])
    assert function_response["response"] == {"error": "boom"}
    parts = cast("list[dict[str, object]]", function_response["parts"])
    assert parts[0]["inlineData"] == {"mimeType": "image/png", "data": "aGk="}


async def test_historical_tool_use_without_signature_uses_dummy_signature():
    body = await _convert(
        messages=[
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_unsigned",
                        "name": "lookup",
                        "input": {},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_unsigned",
                        "content": "x",
                    }
                ],
            },
        ]
    )
    call_part = _parts(body, 0)[0]
    assert call_part["thoughtSignature"] == DUMMY_THOUGHT_SIGNATURE
    result = _dict(_parts(body, 1)[0]["functionResponse"])
    assert result["id"] == "toolu_unsigned"


async def test_unresolved_tool_use_id_is_rejected():
    with pytest.raises(ConversionError) as excinfo:
        await _convert(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "missing",
                            "content": "x",
                        }
                    ],
                }
            ]
        )
    assert excinfo.value.field is not None
    assert "tool_use_id" in excinfo.value.field


async def test_thinking_block_maps_to_thought_part():
    body = await _convert(
        messages=[
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "plan",
                        "signature": "external-signature-is-not-trusted",
                    },
                    {"type": "text", "text": "answer"},
                ],
            }
        ]
    )
    thinking_part = _parts(body)[0]
    assert thinking_part == {"text": "plan", "thought": True}
    assert _parts(body)[1] == {"text": "answer"}


async def test_thinking_block_without_signature_still_maps_to_thought_part():
    body = await _convert(
        messages=[
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "plan"},
                    {"type": "text", "text": "answer"},
                ],
            }
        ]
    )
    assert _parts(body) == [
        {"text": "plan", "thought": True},
        {"text": "answer"},
    ]


@pytest.mark.parametrize(
    "block",
    [
        {"type": "document", "source": {"type": "text"}},
        {"type": "server_tool_use", "id": "srv_1", "name": "web_search"},
        {"type": "web_search_tool_result", "tool_use_id": "srv_1"},
        {"type": "mystery"},
    ],
)
async def test_unknown_blocks_are_rejected(block: dict[str, object]):
    with pytest.raises(ConversionError):
        await _convert(messages=[{"role": "user", "content": [block]}])


async def test_max_tokens_is_required():
    with pytest.raises(ValidationError) as excinfo:
        ClaudeMessagesRequest.model_validate(
            {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]}
        )
    assert any(error["loc"][-1] == "max_tokens" for error in excinfo.value.errors())


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        ({"type": "auto"}, {"mode": "AUTO"}),
        ({"type": "none"}, {"mode": "NONE"}),
        ({"type": "any"}, {"mode": "ANY"}),
        (
            {"type": "tool", "name": "lookup"},
            {"mode": "ANY", "allowedFunctionNames": ["lookup"]},
        ),
    ],
)
async def test_tool_choice_modes(
    choice: dict[str, object], expected: dict[str, object]
):
    body = await _convert(tool_choice=choice)
    tool_config = _dict(body["toolConfig"])
    assert tool_config["functionCallingConfig"] == expected


async def test_disable_parallel_tool_use_is_ignored():
    body = await _convert(
        tool_choice={"type": "auto", "disable_parallel_tool_use": True}
    )
    assert body["toolConfig"] == {"functionCallingConfig": {"mode": "AUTO"}}


async def test_builtin_tool_types_are_rejected():
    with pytest.raises(ConversionError) as excinfo:
        await _convert(
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 5,
                }
            ]
        )
    assert excinfo.value.field == "tools[0]"


async def test_custom_tool_input_schema_uses_parameters_json_schema():
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    body = await _convert(
        tools=[
            {
                "name": "lookup",
                "description": "find things",
                "input_schema": schema,
            }
        ]
    )
    tools = cast("list[dict[str, object]]", body["tools"])
    declarations = cast("list[dict[str, object]]", tools[0]["functionDeclarations"])
    assert declarations[0] == {
        "name": "lookup",
        "description": "find things",
        "parametersJsonSchema": schema,
    }


@pytest.mark.parametrize(
    ("budget", "level"),
    [(1500, "LOW"), (5000, "MEDIUM"), (9000, "HIGH")],
)
async def test_thinking_budget_maps_to_level(budget: int, level: str):
    body = await _convert(thinking={"type": "enabled", "budget_tokens": budget})
    generation_config = _dict(body["generationConfig"])
    assert generation_config["thinkingConfig"] == {"thinkingLevel": level}


async def test_disabled_thinking_sets_no_level():
    body = await _convert(thinking={"type": "disabled"})
    assert "thinkingConfig" not in _dict(body["generationConfig"])


async def test_output_format_json_schema_and_json_object():
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    body = await _convert(output_format={"type": "json_schema", "schema": schema})
    config = _dict(body["generationConfig"])
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"] == schema

    nested = await _convert(output_config={"format": {"type": "json_object"}})
    nested_config = _dict(nested["generationConfig"])
    assert nested_config["responseMimeType"] == "application/json"
    assert _dict(nested_config["responseSchema"])["type"] == "object"

    plain = await _convert(output_format={"type": "text"})
    assert "responseMimeType" not in _dict(plain["generationConfig"])


async def test_converted_body_is_a_valid_gemini_request():
    body = await _convert(
        system="sys",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "aGk=",
                        },
                    },
                ],
            }
        ],
        tools=[{"name": "lookup", "input_schema": {"type": "object"}}],
        tool_choice={"type": "auto"},
        thinking={"type": "adaptive", "budget_tokens": 1000},
        output_format={"type": "json_object"},
    )
    parsed = GeminiGenerateContentRequest.model_validate(body)
    assert parsed.contents[0].parts[1].inlineData is not None
    assert parsed.generationConfig is not None
    assert parsed.generationConfig.maxOutputTokens == 128


# ---------------------------------------------------------------------------
# Non-stream response
# ---------------------------------------------------------------------------


def test_non_stream_content_order_stop_reason_and_usage():
    outcome = _outcome(
        Candidate(
            thinking="plan",
            text="answer",
            function_calls=[{"id": "call_1", "name": "lookup", "args": {"q": "x"}}],
        ),
        usage={
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "prompt_tokens_details": {"cached_tokens": 4},
        },
    )
    response = claude_response(model=MODEL, outcome=outcome)

    assert str(response["id"]).startswith("msg_")
    assert response["type"] == "message"
    assert response["role"] == "assistant"
    assert response["model"] == MODEL
    assert response["stop_reason"] == "tool_use"
    assert response["stop_sequence"] is None

    content = cast("list[dict[str, object]]", response["content"])
    assert [block["type"] for block in content] == [
        "thinking",
        "text",
        "tool_use",
    ]
    assert str(content[0]["signature"]).startswith("proxy_thinking_")
    assert content[1]["text"] == "answer"
    assert content[2] == {
        "type": "tool_use",
        "id": "call_1",
        "name": "lookup",
        "input": {"q": "x"},
    }
    assert response["usage"] == {
        "input_tokens": 6,
        "output_tokens": 5,
        "cache_read_input_tokens": 4,
        "cache_creation_input_tokens": 0,
    }


def test_non_stream_empty_content_returns_empty_text_block():
    response = claude_response(model=MODEL, outcome=_outcome(Candidate()))
    assert response["content"] == [{"type": "text", "text": ""}]
    assert response["stop_reason"] == "end_turn"
    assert response["usage"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


def test_non_stream_images_become_markdown_text():
    outcome = _outcome(
        Candidate(images=[GeneratedImage(mime="image/png", data=b"hi", size=2)])
    )
    response = claude_response(model=MODEL, outcome=outcome)
    content = cast("list[dict[str, object]]", response["content"])
    assert content == [
        {"type": "text", "text": "![Generated Image](data:image/png;base64,aGk=)"}
    ]


def test_non_stream_max_tokens_finish_reason():
    response = claude_response(
        model=MODEL,
        outcome=_outcome(Candidate(text="cut"), finish_reason="MAX_TOKENS"),
    )
    assert response["stop_reason"] == "max_tokens"


# ---------------------------------------------------------------------------
# Streaming response
# ---------------------------------------------------------------------------


def test_stream_event_order_blocks_and_indices():
    renderer = ClaudeStreamRenderer(MODEL)
    events = [
        StreamEvent(
            kind="thinking",
            payload="plan",
            thought_signature="proxy_thinking_sig",
        ),
        StreamEvent(kind="body", payload="hello"),
        StreamEvent(
            kind="tool_calls",
            payload=[{"id": "toolu_1", "name": "lookup", "args": {"q": "x"}}],
        ),
        StreamEvent(
            kind="usage",
            payload={"prompt_tokens": 3, "completion_tokens": 2},
            finish_reason="STOP",
        ),
    ]

    raw = renderer.start()
    assert renderer.start() == ""
    for event in events:
        raw += renderer.render(event)
    raw += renderer.close()

    parsed = _parse_sse(raw)
    names = [name for name, _ in parsed]
    assert names[0] == "ping"
    assert names[1] == "message_start"
    assert names[-2:] == ["message_delta", "message_stop"]

    message_start = parsed[1][1]
    assert message_start["type"] == "message_start"
    assert _dict(message_start["message"])["role"] == "assistant"

    delta_types = [
        _dict(payload["delta"])["type"]
        for name, payload in parsed
        if name == "content_block_delta"
    ]
    assert delta_types == [
        "thinking_delta",
        "signature_delta",
        "text_delta",
        "input_json_delta",
    ]

    indices = [
        payload["index"] for name, payload in parsed if name == "content_block_start"
    ]
    assert indices == [0, 1, 2]

    signature_delta = next(
        payload
        for name, payload in parsed
        if name == "content_block_delta"
        and _dict(payload["delta"])["type"] == "signature_delta"
    )
    assert _dict(signature_delta["delta"])["signature"] == "proxy_thinking_sig"

    tool_delta = next(
        payload
        for name, payload in parsed
        if name == "content_block_delta"
        and _dict(payload["delta"])["type"] == "input_json_delta"
    )
    assert json.loads(str(_dict(tool_delta["delta"])["partial_json"])) == {"q": "x"}

    message_delta = parsed[-2][1]
    delta = _dict(message_delta["delta"])
    assert delta["stop_reason"] == "tool_use"
    assert _dict(message_delta["usage"])["output_tokens"] == 2


def test_stream_block_indices_increase_across_multiple_tool_calls():
    renderer = ClaudeStreamRenderer(MODEL)
    raw = renderer.start()
    raw += renderer.render(
        StreamEvent(
            kind="tool_calls",
            payload=[
                {"id": "toolu_1", "name": "a", "args": {}},
                {"id": "toolu_2", "name": "b", "args": {}},
            ],
        )
    )
    raw += renderer.close()
    parsed = _parse_sse(raw)
    indices = [
        payload["index"] for name, payload in parsed if name == "content_block_start"
    ]
    assert indices == [0, 1]


# ---------------------------------------------------------------------------
# Route-level contracts
# ---------------------------------------------------------------------------


def _route_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_claude.router)
    return app


async def _post(path: str, payload: dict[str, object]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_route_app()), base_url="http://test"
    ) as client:
        return await client.post(path, json=payload)


def _enable_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        format_errors, "format_enabled", lambda api_format: api_format == "claude"
    )


async def test_route_disabled_feature_returns_claude_403(monkeypatch):
    monkeypatch.setattr(format_errors, "format_enabled", lambda _format: False)
    response = await _post(
        "/v1/messages",
        {"model": MODEL, "max_tokens": 16, "messages": []},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "permission_error"


async def test_route_non_stream_success(monkeypatch):
    _enable_claude(monkeypatch)
    captured: dict[str, object] = {}

    async def fake_generate(**kwargs: object) -> GenerationOutcome:
        captured.update(kwargs)
        return _outcome(Candidate(text="routed"))

    monkeypatch.setattr(routes_claude, "generate", fake_generate)
    monkeypatch.setattr(routes_claude, "get_client", lambda: object())

    response = await _post(
        "/v1/messages",
        {
            "model": "models/claude-3-5-sonnet",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "message"
    assert str(body["id"]).startswith("msg_")
    assert body["content"] == [{"type": "text", "text": "routed"}]
    assert captured["model_path"] == "claude-3-5-sonnet"
    assert captured["api_format"] == "claude"


async def test_route_missing_max_tokens_returns_400(monkeypatch):
    _enable_claude(monkeypatch)
    response = await _post(
        "/v1/messages",
        {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert "max_tokens" in body["error"]["message"]


async def test_route_stream_first_event_error_returns_status(monkeypatch):
    _enable_claude(monkeypatch)

    async def fake_stream(**_kwargs: object):
        raise HTTPException(
            429, detail={"message": "quota exhausted", "type": "rate_limit"}
        )
        yield StreamEvent(kind="usage", payload=None)

    monkeypatch.setattr(routes_claude, "stream_generation", fake_stream)
    monkeypatch.setattr(routes_claude, "get_client", lambda: object())

    response = await _post(
        "/v1/messages",
        {
            "model": MODEL,
            "max_tokens": 16,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 429
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "rate_limit_error"


async def test_route_stream_success_emits_claude_event_sequence(monkeypatch):
    _enable_claude(monkeypatch)

    async def fake_stream(**_kwargs: object):
        yield StreamEvent(kind="body", payload="hi")
        yield StreamEvent(
            kind="usage",
            payload={"prompt_tokens": 1, "completion_tokens": 1},
            finish_reason="STOP",
        )

    monkeypatch.setattr(routes_claude, "stream_generation", fake_stream)
    monkeypatch.setattr(routes_claude, "get_client", lambda: object())

    response = await _post(
        "/v1/messages",
        {
            "model": MODEL,
            "max_tokens": 16,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    parsed = _parse_sse(response.text)
    names = [name for name, _ in parsed]
    assert names[0] == "ping"
    assert names[1] == "message_start"
    assert names[-2:] == ["message_delta", "message_stop"]


async def test_route_count_tokens_returns_explicit_404(monkeypatch):
    _enable_claude(monkeypatch)
    response = await _post("/v1/messages/count_tokens", {})
    assert response.status_code == 404
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "not_found_error"
    assert "count_tokens" in body["error"]["message"]


@pytest.mark.asyncio
async def test_tool_use_thought_signature_is_cached_and_reattached():
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
                            "id": "toolu_sig1",
                            "name": "get_weather",
                            "args": {"city": "Paris"},
                            "thought_signature": "sig-claude",
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
    rendered = cast("dict[str, object]", claude_response(model="m", outcome=outcome))
    content = cast("list[dict[str, object]]", rendered["content"])
    assert content[0]["type"] == "tool_use"

    history = ClaudeMessagesRequest.model_validate(
        {
            "model": "m",
            "max_tokens": 32,
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_sig1",
                            "name": "get_weather",
                            "input": {"city": "Paris"},
                        }
                    ],
                }
            ],
        }
    )
    body = await convert_claude_request(history, MediaFetcher())
    contents = cast("list[dict[str, object]]", body["contents"])
    parts = cast("list[dict[str, object]]", contents[0]["parts"])
    assert parts[0]["thoughtSignature"] == "sig-claude"
