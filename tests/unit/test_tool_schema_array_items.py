"""Array ``items`` normalization across all four protocol faces (2.1.0 blocker)."""

from __future__ import annotations

from typing import Any, cast

import pytest

from aistudio_api.application.chat_service import (
    encode_schema_to_wire,
    normalize_gemini_request,
)
from aistudio_api.application.contracts.claude import ClaudeMessagesRequest
from aistudio_api.application.contracts.gemini import GeminiGenerateContentRequest
from aistudio_api.application.contracts.openai import OpenAIChatRequest
from aistudio_api.application.contracts.responses import ResponsesRequest
from aistudio_api.application.format_converters.claude_request import (
    convert_claude_request,
)
from aistudio_api.application.format_converters.common import normalize_tool_schema
from aistudio_api.application.format_converters.media import MediaFetcher
from aistudio_api.application.format_converters.openai_request import (
    convert_openai_request,
)
from aistudio_api.application.format_converters.responses_request import (
    convert_responses_request,
)
from aistudio_api.infrastructure.gateway.wire_spec import SchemaIndex

MODEL = "gemini-3.7-flash"

ARRAY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answers": {"type": "array"},
        "tasks": {"type": "array", "items": {}},
    },
}


def _schema_from_body(body: object) -> list[Any]:
    request = GeminiGenerateContentRequest.model_validate(body)
    normalized = normalize_gemini_request(request, MODEL)
    assert normalized.tools is not None
    declarations = cast("list[list[Any]]", normalized.tools[0][1])
    return cast("list[Any]", declarations[0][2])


def _schema_from_request(request: GeminiGenerateContentRequest) -> list[Any]:
    normalized = normalize_gemini_request(request, MODEL)
    assert normalized.tools is not None
    declarations = cast("list[list[Any]]", normalized.tools[0][1])
    return cast("list[Any]", declarations[0][2])


def _property_wire(schema_wire: list[Any], name: str) -> list[Any]:
    properties = dict(
        cast("list[tuple[str, list[Any]]]", schema_wire[SchemaIndex.PROPERTIES])
    )
    return properties[name]


def _assert_array_items(schema_wire: list[Any]) -> None:
    answers = _property_wire(schema_wire, "answers")
    tasks = _property_wire(schema_wire, "tasks")
    assert answers[SchemaIndex.TYPE] == 5
    assert tasks[SchemaIndex.TYPE] == 5
    assert answers[SchemaIndex.ITEMS] == [1]
    assert tasks[SchemaIndex.ITEMS] == [1]


@pytest.mark.asyncio
async def test_openai_array_parameters_receive_items():
    request = OpenAIChatRequest.model_validate(
        {
            "model": "gemini-test",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "ask", "parameters": ARRAY_SCHEMA},
                }
            ],
        }
    )
    body = await convert_openai_request(request, MediaFetcher())
    _assert_array_items(_schema_from_body(body))


def test_gemini_array_parameters_receive_items():
    request = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
            "tools": [
                {"functionDeclarations": [{"name": "ask", "parameters": ARRAY_SCHEMA}]}
            ],
        }
    )
    _assert_array_items(_schema_from_request(request))


@pytest.mark.asyncio
async def test_claude_array_parameters_receive_items():
    request = ClaudeMessagesRequest.model_validate(
        {
            "model": "gemini-test",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "ask", "input_schema": ARRAY_SCHEMA}],
        }
    )
    body = await convert_claude_request(request, MediaFetcher())
    _assert_array_items(_schema_from_body(body))


@pytest.mark.asyncio
async def test_responses_array_parameters_receive_items():
    request = ResponsesRequest.model_validate(
        {
            "model": "gemini-test",
            "input": "hi",
            "tools": [{"type": "function", "name": "ask", "parameters": ARRAY_SCHEMA}],
        }
    )
    body = await convert_responses_request(request, MediaFetcher())
    _assert_array_items(_schema_from_body(body))


def test_normalize_tool_schema_is_recursive_idempotent_and_keeps_valid_items():
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tags": {"type": "array", "items": {"type": "string"}},
            "matrix": {"type": "ARRAY", "items": {"type": "array"}},
            "union": {
                "anyOf": [
                    {"type": "array"},
                    {"type": "integer"},
                ]
            },
            "count": {"type": "integer"},
        },
    }

    normalized = normalize_tool_schema(schema)
    properties = cast("dict[str, Any]", normalized["properties"])
    assert properties["tags"]["items"] == {"type": "string"}
    assert properties["matrix"]["items"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert properties["union"]["anyOf"] == [
        {"type": "array", "items": {"type": "string"}},
        {"type": "integer"},
    ]
    assert "items" not in properties["count"]
    assert normalize_tool_schema(normalized) == normalized
    assert schema["properties"]["matrix"] == {
        "type": "ARRAY",
        "items": {"type": "array"},
    }


def test_normalize_tool_schema_uses_uppercase_fallback_for_uppercase_types():
    assert normalize_tool_schema({"type": "ARRAY"}) == {
        "type": "ARRAY",
        "items": {"type": "STRING"},
    }
    assert normalize_tool_schema({"type": "array", "items": {}}) == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert normalize_tool_schema({"type": "ArrayLike"}) == {
        "type": "ArrayLike",
        "items": {"type": "string"},
    }


def test_encode_schema_to_wire_adds_items_for_bare_array_schema():
    wire = encode_schema_to_wire({"type": "array"})
    assert wire[SchemaIndex.TYPE] == 5
    assert wire[SchemaIndex.ITEMS] == [1]
