"""Protocol contracts for the Claude Messages format face (C3 Wave 1)."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from aistudio_api.api import format_errors, routes_claude
from aistudio_api.api.dependencies import get_routing_pipeline
from aistudio_api.application.contracts.claude import (
    ClaudeCountTokensRequest,
    ClaudeMessagesRequest,
)
from aistudio_api.application.contracts.gemini import GeminiGenerateContentRequest
from aistudio_api.application.format_converters.claude_request import (
    convert_claude_count_request,
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
from aistudio_api.application.generation_service import (
    GenerationOutcome,
    StreamEvent,
)
from aistudio_api.application.signature_cache import signature_cache
from aistudio_api.domain.errors import (
    RequestError,
    SurfaceSwitchExhaustedError,
    TierRequiredError,
    UsageLimitExceeded,
)
from aistudio_api.domain.models import Candidate, GeneratedImage, ModelOutput

MODEL = "gemini-3.7-flash"
PUBLIC_IMAGE_URL = "http://93.184.216.34/image.png"


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


def _dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def _contents(body: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", body["contents"])


def _parts(body: dict[str, object], index: int = 0) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", _contents(body)[index]["parts"])


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


def _outcome(
    candidate: Candidate,
    *,
    finish_reason: str = "STOP",
    usage: dict[str, object] | None = None,
) -> GenerationOutcome:
    return GenerationOutcome(
        output=ModelOutput(candidates=[candidate], usage=usage or {}),
        model=MODEL,
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
        if not name and not data_lines:
            continue
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
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "url", "url": PUBLIC_IMAGE_URL}},
            ],
        }
    ]
    media = MediaFetcher(transport=_image_transport())
    body = await convert_claude_request(_request(messages=messages), media)
    inline = _dict(_parts(body)[0]["inlineData"])
    assert inline["mimeType"] == "image/png"
    assert inline["data"] == "aW1hZ2UtYnl0ZXM="

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
    call_part = _parts(body, 0)[0]
    assert _dict(call_part["functionCall"]) == {
        "id": "toolu_01",
        "name": "lookup",
        "args": {"query": "x"},
    }
    assert call_part["thoughtSignature"] == "sig-toolu-01"

    function_response = _dict(_parts(body, 1)[0]["functionResponse"])
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


async def test_pg01_historical_tool_use_without_signature_uses_dummy():
    signature_cache.clear()
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


async def test_pg08_unknown_tool_use_id_is_lenient_not_400():
    body = await _convert(
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
    function_response = _dict(_parts(body)[0]["functionResponse"])
    assert function_response["id"] == "missing"
    assert function_response["name"] == "unknown_tool"
    assert function_response["response"] == {"result": "x"}


async def test_pg08_unknown_tool_use_id_infers_name_from_pending_context():
    body = await _convert(
        messages=[
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_real",
                        "name": "lookup",
                        "input": {"q": "x"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_stale",
                        "content": {"result": "ok"},
                    }
                ],
            },
        ]
    )
    function_response = _dict(_parts(body, 1)[0]["functionResponse"])
    assert function_response["name"] == "lookup"
    assert function_response["response"] == {"result": "ok"}


async def test_pg08_unknown_tool_use_id_infers_name_from_whole_history():
    body = await _convert(
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_future",
                        "content": "ok",
                    }
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_future",
                        "name": "lookup",
                        "input": {},
                    }
                ],
            },
        ]
    )
    function_response = _dict(_parts(body, 0)[0]["functionResponse"])
    assert function_response["name"] == "lookup"


async def test_pg04_tool_arguments_and_result_are_always_structs():
    body = await _convert(
        messages=[
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_odd",
                        "name": "lookup",
                        "input": "not-a-dict",
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_odd",
                        "content": "plain text",
                    }
                ],
            },
        ]
    )
    call = _dict(_parts(body, 0)[0]["functionCall"])
    assert isinstance(call["args"], dict)
    assert call["args"] == {"content": "not-a-dict"}
    function_response = _dict(_parts(body, 1)[0]["functionResponse"])
    assert isinstance(function_response["response"], dict)
    assert function_response["response"] == {"result": "plain text"}


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
    assert _parts(body)[0] == {"text": "plan", "thought": True}
    assert _parts(body)[1] == {"text": "answer"}


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


def test_max_tokens_is_required():
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


async def test_e04_seed_is_accepted_but_not_forwarded(caplog: pytest.LogCaptureFixture):
    with caplog.at_level("WARNING", logger="format.claude"):
        body = await _convert(seed=12345)
    config = _dict(body["generationConfig"])
    assert "seed" not in config
    assert any("E-04" in record.message for record in caplog.records)


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


async def test_count_request_is_prompt_only():
    request = ClaudeCountTokensRequest.model_validate(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": "hi"}],
            "system": "sys",
            "tools": [{"name": "lookup", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "auto"},
        }
    )
    body = await convert_claude_count_request(request, MediaFetcher())
    assert "generationConfig" not in body
    assert "toolConfig" not in body
    assert "systemInstruction" in body
    assert "tools" in body


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
# Streaming renderer
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
    message = _dict(message_start["message"])
    assert message["role"] == "assistant"
    assert _dict(message["usage"])["input_tokens"] == 0

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


def test_stream_message_start_reports_known_input_tokens():
    renderer = ClaudeStreamRenderer(MODEL, input_tokens=17)
    parsed = _parse_sse(renderer.start() + renderer.close())
    message_start = next(payload for name, payload in parsed if name == "message_start")
    assert _dict(_dict(message_start["message"])["usage"])["input_tokens"] == 17


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
# Routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("models/gemini-3.7-flash", "gemini-3.7-flash"),
        ("chat/gemini-3.7-flash", "gemini-3.7-flash"),
        ("models/models/gemini-3.7-flash", "gemini-3.7-flash"),
        ("chat/models/gemini-3.7-flash", "gemini-3.7-flash"),
        ("  models/chat/gemini-3.7-flash  ", "gemini-3.7-flash"),
        ("gemini-3.7-flash", "gemini-3.7-flash"),
        ("claude-3-5-sonnet", "claude-3-5-sonnet"),
    ],
)
def test_model_prefix_stripping_has_no_alias(raw: str, clean: str):
    assert routes_claude._strip_model_prefix(raw) == clean


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_claude.router)
    app.dependency_overrides[routes_claude.require_claude] = lambda: None
    app.dependency_overrides[routes_claude.require_api_key] = lambda: None
    app.dependency_overrides[get_routing_pipeline] = lambda: object()
    return app


async def _post(path: str, payload: dict[str, object]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        return await client.post(path, json=payload)


def test_require_claude_maps_disabled_feature_to_claude_403(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(format_errors, "format_enabled", lambda _fmt: False)
    with pytest.raises(HTTPException) as excinfo:
        routes_claude.require_claude()
    response = format_errors.format_error_response(excinfo.value, "claude")
    assert response.status_code == 403
    body = json.loads(bytes(response.body))
    assert body["type"] == "error"
    assert body["error"]["type"] == "permission_error"


async def test_route_non_stream_success_and_atg_passthrough(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}

    async def fake_generate(**kwargs: object) -> GenerationOutcome:
        captured.update(kwargs)
        return _outcome(Candidate(text="routed"))

    monkeypatch.setattr(routes_claude, "generate", fake_generate)
    monkeypatch.setattr(routes_claude, "format_media_allow_private", lambda: False)

    response = await _post(
        "/v1/messages",
        {
            "model": "models/gemini-3.7-flash",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "message"
    assert str(body["id"]).startswith("msg_")
    assert body["content"] == [{"type": "text", "text": "routed"}]
    assert captured["model_path"] == "gemini-3.7-flash"
    assert captured["api_format"] == "claude"


async def test_route_missing_max_tokens_returns_claude_400(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(routes_claude, "format_media_allow_private", lambda: False)
    response = await _post(
        "/v1/messages",
        {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert "max_tokens" in body["error"]["message"]


async def test_route_stream_success_emits_claude_event_sequence(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_stream(**_kwargs: object):
        yield StreamEvent(kind="body", payload="hi")
        yield StreamEvent(
            kind="usage",
            payload={"prompt_tokens": 1, "completion_tokens": 1},
            finish_reason="STOP",
        )

    monkeypatch.setattr(routes_claude, "stream_generation", fake_stream)
    monkeypatch.setattr(routes_claude, "format_media_allow_private", lambda: False)

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


async def test_route_stream_first_event_429_returns_real_http_status(
    monkeypatch: pytest.MonkeyPatch,
):
    async def failing_stream(**_kwargs: object):
        raise UsageLimitExceeded("quota exhausted")
        yield StreamEvent(kind="usage", payload=None)

    monkeypatch.setattr(routes_claude, "stream_generation", failing_stream)
    monkeypatch.setattr(routes_claude, "format_media_allow_private", lambda: False)

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


async def test_route_stream_usage_only_returns_502(monkeypatch: pytest.MonkeyPatch):
    async def usage_only(**_kwargs: object):
        yield StreamEvent(
            kind="usage",
            payload={"prompt_tokens": 1, "completion_tokens": 0},
            finish_reason="STOP",
        )

    monkeypatch.setattr(routes_claude, "stream_generation", usage_only)
    monkeypatch.setattr(routes_claude, "format_media_allow_private", lambda: False)

    response = await _post(
        "/v1/messages",
        {
            "model": MODEL,
            "max_tokens": 16,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 502
    assert response.json()["error"]["type"] == "api_error"


async def test_route_stream_empty_stream_returns_502(monkeypatch: pytest.MonkeyPatch):
    async def empty_stream(**_kwargs: object):
        return
        yield StreamEvent(kind="usage", payload=None)

    monkeypatch.setattr(routes_claude, "stream_generation", empty_stream)
    monkeypatch.setattr(routes_claude, "format_media_allow_private", lambda: False)

    response = await _post(
        "/v1/messages",
        {
            "model": MODEL,
            "max_tokens": 16,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 502
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "api_error"


async def test_route_stream_after_content_error_emits_event_error(
    monkeypatch: pytest.MonkeyPatch,
):
    async def failing_after_body(**_kwargs: object):
        yield StreamEvent(kind="body", payload="partial")
        raise RuntimeError("upstream dropped")

    monkeypatch.setattr(routes_claude, "stream_generation", failing_after_body)
    monkeypatch.setattr(routes_claude, "format_media_allow_private", lambda: False)

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
    assert "event: error" in response.text
    assert "upstream dropped" in response.text


async def test_route_stream_keepalive_before_slow_first_event(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AISTUDIO_SSE_PREREAD_WINDOW_SEC", "0.05")
    monkeypatch.setenv("AISTUDIO_SSE_KEEPALIVE_SEC", "0.02")

    async def slow_stream(**_kwargs: object):
        await asyncio.sleep(0.15)
        yield StreamEvent(kind="body", payload="slow")
        yield StreamEvent(
            kind="usage",
            payload={"prompt_tokens": 1, "completion_tokens": 1},
            finish_reason="STOP",
        )

    monkeypatch.setattr(routes_claude, "stream_generation", slow_stream)
    monkeypatch.setattr(routes_claude, "format_media_allow_private", lambda: False)

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
    assert ": keepalive" in response.text
    assert "slow" in response.text


async def test_route_count_tokens_uses_prompt_only_counter(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}

    async def fake_count(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(total_tokens=42)

    monkeypatch.setattr(routes_claude, "count_prompt_tokens", fake_count)
    monkeypatch.setattr(routes_claude, "format_media_allow_private", lambda: False)

    response = await _post(
        "/v1/messages/count_tokens",
        {
            "model": "models/gemini-3.7-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "system": "sys",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"input_tokens": 42}
    assert captured["api_format"] == "claude"
    assert captured["model_path"] == "gemini-3.7-flash"
    request = cast("GeminiGenerateContentRequest", captured["req"])
    assert request.generationConfig is None


async def test_route_tier_required_is_403_with_four_fields(
    monkeypatch: pytest.MonkeyPatch,
):
    async def tier_error(**_kwargs: object) -> GenerationOutcome:
        raise TierRequiredError(
            model=MODEL,
            tier="Free",
            required_tier="Pro",
            current_tier="Free",
        )

    monkeypatch.setattr(routes_claude, "generate", tier_error)
    monkeypatch.setattr(routes_claude, "format_media_allow_private", lambda: False)

    response = await _post(
        "/v1/messages",
        {
            "model": MODEL,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["type"] == "permission_error"
    assert error["tier"] == "Free"
    assert error["model"] == MODEL
    assert error["required_tier"] == "Pro"
    assert error["current_tier"] == "Free"


def test_5xx_error_mapping_uses_claude_types():
    overloaded = format_errors.format_error_response(
        RequestError(503, "upstream unavailable"), "claude"
    )
    assert overloaded.status_code == 503
    assert json.loads(bytes(overloaded.body))["error"]["type"] == "overloaded_error"

    api_error = format_errors.format_error_response(
        RequestError(502, "bad gateway"), "claude"
    )
    assert api_error.status_code == 502
    assert json.loads(bytes(api_error.body))["error"]["type"] == "api_error"


async def test_route_pg08_unknown_tool_use_id_is_not_400(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_generate(**_kwargs: object) -> GenerationOutcome:
        return _outcome(Candidate(text="ok"))

    monkeypatch.setattr(routes_claude, "generate", fake_generate)
    monkeypatch.setattr(routes_claude, "format_media_allow_private", lambda: False)

    response = await _post(
        "/v1/messages",
        {
            "model": MODEL,
            "max_tokens": 16,
            "messages": [
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
            ],
        },
    )
    assert response.status_code == 200


async def test_route_switch_exhausted_is_503(monkeypatch: pytest.MonkeyPatch):
    async def exhausted(**_kwargs: object) -> GenerationOutcome:
        raise SurfaceSwitchExhaustedError(surface="chat")

    monkeypatch.setattr(routes_claude, "generate", exhausted)
    monkeypatch.setattr(routes_claude, "format_media_allow_private", lambda: False)

    response = await _post(
        "/v1/messages",
        {
            "model": MODEL,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["type"] == "api_error"
    assert error["code"] == "surface_switch_exhausted"


# ---------------------------------------------------------------------------
# Layer boundary
# ---------------------------------------------------------------------------


def test_claude_application_modules_import_no_api_or_fastapi():
    src_root = Path(__file__).resolve().parents[2] / "src"
    targets = [
        "aistudio_api/application/contracts/claude.py",
        "aistudio_api/application/format_converters/claude_request.py",
        "aistudio_api/application/format_converters/claude_response.py",
    ]
    forbidden = ("fastapi", "aistudio_api.api")
    violations: list[str] = []
    for relative in targets:
        path = src_root / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(
                    name == prefix or name.startswith(f"{prefix}.")
                    for prefix in forbidden
                ):
                    violations.append(f"{relative}: {name}")
    assert violations == []
