"""Array ``items`` normalization across all four protocol faces (2.1.0 blocker)."""

from __future__ import annotations

import copy
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


KILO_BUNDLE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "read_file": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "write_to_file": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        "apply_diff": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "diff": {"type": "string"}},
            "required": ["path", "diff"],
        },
        "execute_command": {
            "type": "object",
            "properties": {"command": {"type": "string"}, "cwd": {"type": "string"}},
            "required": ["command"],
        },
        "ask_followup_question": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "answers": {"type": "array"},
            },
            "required": ["question", "answers"],
        },
        "update_todo_list": {
            "type": "object",
            "properties": {"tasks": {"type": "array", "items": {}}},
            "required": ["tasks"],
        },
        "nullable_field": {"type": ["string", "null"]},
        "anyof_field": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "empty_node": {},
        "bad_required": {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a", "missing"],
        },
        "tuple_items": {"type": "array", "items": [{}, {"type": "integer"}]},
        "empty_tuple_items": {"type": "array", "items": []},
        "nested_object": {
            "type": "object",
            "properties": {
                "children": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name", "ghost"],
                    },
                }
            },
            "required": ["children"],
        },
        "valid": {
            "type": "object",
            "properties": {"q": {"type": "string"}, "n": {"type": "integer"}},
            "required": ["q"],
        },
    },
    "required": [
        "read_file",
        "write_to_file",
        "apply_diff",
        "execute_command",
        "ask_followup_question",
        "update_todo_list",
        "nullable_field",
        "anyof_field",
        "empty_node",
        "bad_required",
        "tuple_items",
        "empty_tuple_items",
        "nested_object",
        "valid",
        "ghost",
    ],
}


def _wire_property(schema_wire: list[Any], name: str) -> list[Any]:
    properties = dict(
        cast("list[tuple[str, list[Any]]]", schema_wire[SchemaIndex.PROPERTIES])
    )
    return properties[name]


def _iter_wire_schema(node: list[Any]):
    yield node
    items = node[SchemaIndex.ITEMS] if len(node) > SchemaIndex.ITEMS else None
    if isinstance(items, list) and items and isinstance(items[0], int):
        yield from _iter_wire_schema(items)
    properties = (
        node[SchemaIndex.PROPERTIES] if len(node) > SchemaIndex.PROPERTIES else None
    )
    if isinstance(properties, list):
        for entry in properties:
            if (
                isinstance(entry, list)
                and len(entry) == 2
                and isinstance(entry[1], list)
            ):
                yield from _iter_wire_schema(entry[1])
    for index in (SchemaIndex.ONE_OF, SchemaIndex.ANY_OF, SchemaIndex.ALL_OF):
        branches = node[index] if len(node) > index else None
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, list) and branch and isinstance(branch[0], int):
                    yield from _iter_wire_schema(branch)


def _assert_wire_schema_legal(root: list[Any]) -> None:
    for node in _iter_wire_schema(root):
        type_code = node[SchemaIndex.TYPE]
        assert isinstance(type_code, int)
        assert 1 <= type_code <= 6
        if type_code == 5:
            items = node[SchemaIndex.ITEMS] if len(node) > SchemaIndex.ITEMS else None
            assert isinstance(items, list) and items
            assert isinstance(items[0], int)
        if type_code == 6:
            properties = (
                node[SchemaIndex.PROPERTIES]
                if len(node) > SchemaIndex.PROPERTIES
                else None
            )
            names = (
                {entry[0] for entry in properties if isinstance(entry, list)}
                if isinstance(properties, list)
                else set()
            )
            required = (
                node[SchemaIndex.REQUIRED] if len(node) > SchemaIndex.REQUIRED else None
            )
            if required:
                assert set(required) <= names


def _assert_kilo_bundle_wire(root: list[Any]) -> None:
    _assert_wire_schema_legal(root)
    properties = cast("list[list[Any]]", root[SchemaIndex.PROPERTIES])
    props = {entry[0]: entry[1] for entry in properties}

    assert _wire_property(props["ask_followup_question"], "answers")[
        SchemaIndex.ITEMS
    ] == [1]
    assert _wire_property(props["update_todo_list"], "tasks")[SchemaIndex.ITEMS] == [1]
    assert props["nullable_field"][SchemaIndex.TYPE] == 1
    assert props["nullable_field"][SchemaIndex.NULLABLE] is True

    anyof = props["anyof_field"]
    assert anyof[SchemaIndex.NULLABLE] is True
    assert [branch[SchemaIndex.TYPE] for branch in anyof[SchemaIndex.ANY_OF]] == [1]

    assert props["empty_node"][SchemaIndex.TYPE] == 1
    assert props["bad_required"][SchemaIndex.REQUIRED] == ["a"]
    assert props["tuple_items"][SchemaIndex.ITEMS] == [3]
    assert props["empty_tuple_items"][SchemaIndex.ITEMS] == [1]

    nested = props["nested_object"]
    children = _wire_property(nested, "children")
    child_object = children[SchemaIndex.ITEMS]
    assert child_object[SchemaIndex.REQUIRED] == ["name"]

    assert "ghost" not in root[SchemaIndex.REQUIRED]
    assert root[SchemaIndex.REQUIRED] == [
        "read_file",
        "write_to_file",
        "apply_diff",
        "execute_command",
        "ask_followup_question",
        "update_todo_list",
        "nullable_field",
        "anyof_field",
        "empty_node",
        "bad_required",
        "tuple_items",
        "empty_tuple_items",
        "nested_object",
        "valid",
    ]


def _normalized_kilo_bundle() -> dict[str, Any]:
    original = copy.deepcopy(KILO_BUNDLE)
    normalized = normalize_tool_schema(KILO_BUNDLE)
    assert original == KILO_BUNDLE
    assert normalize_tool_schema(normalized) == normalized
    return cast("dict[str, Any]", normalized)


def test_kilo_bundle_normalization_rules_are_idempotent_and_non_mutating():
    normalized = _normalized_kilo_bundle()
    props = cast("dict[str, Any]", normalized["properties"])

    assert props["ask_followup_question"] == {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "answers": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["question", "answers"],
    }
    assert props["update_todo_list"] == {
        "type": "object",
        "properties": {"tasks": {"type": "array", "items": {"type": "string"}}},
        "required": ["tasks"],
    }
    assert props["nullable_field"] == {"type": ["string", "null"]}
    assert props["anyof_field"] == {
        "anyOf": [{"type": "string"}],
        "nullable": True,
    }
    assert props["empty_node"] == {"type": "string"}
    assert props["bad_required"]["required"] == ["a"]
    assert props["tuple_items"] == {"type": "array", "items": {"type": "integer"}}
    assert props["empty_tuple_items"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert props["nested_object"]["required"] == ["children"]
    children_item = props["nested_object"]["properties"]["children"]["items"]
    assert children_item["required"] == ["name"]
    assert props["valid"] == {
        "type": "object",
        "properties": {"q": {"type": "string"}, "n": {"type": "integer"}},
        "required": ["q"],
    }


@pytest.mark.asyncio
async def test_kilo_bundle_through_openai_is_gemini_safe():
    request = OpenAIChatRequest.model_validate(
        {
            "model": "gemini-test",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "kilo", "parameters": KILO_BUNDLE},
                }
            ],
        }
    )
    body = await convert_openai_request(request, MediaFetcher())
    _assert_kilo_bundle_wire(_schema_from_body(body))


@pytest.mark.asyncio
async def test_kilo_bundle_through_claude_is_gemini_safe():
    request = ClaudeMessagesRequest.model_validate(
        {
            "model": "gemini-test",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "kilo", "input_schema": KILO_BUNDLE}],
        }
    )
    body = await convert_claude_request(request, MediaFetcher())
    _assert_kilo_bundle_wire(_schema_from_body(body))


@pytest.mark.asyncio
async def test_kilo_bundle_through_responses_is_gemini_safe():
    request = ResponsesRequest.model_validate(
        {
            "model": "gemini-test",
            "input": "hi",
            "tools": [{"type": "function", "name": "kilo", "parameters": KILO_BUNDLE}],
        }
    )
    body = await convert_responses_request(request, MediaFetcher())
    _assert_kilo_bundle_wire(_schema_from_body(body))


def test_kilo_bundle_through_gemini_is_gemini_safe():
    request = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
            "tools": [
                {"functionDeclarations": [{"name": "kilo", "parameters": KILO_BUNDLE}]}
            ],
        }
    )
    _assert_kilo_bundle_wire(_schema_from_request(request))


def test_boolean_and_tuple_items_and_empty_required():
    assert normalize_tool_schema({"type": "ARRAY", "items": True}) == {
        "type": "ARRAY",
        "items": {"type": "STRING"},
    }
    assert normalize_tool_schema({"type": "array", "items": False}) == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert normalize_tool_schema(
        {"type": "array", "items": [[], {"type": "integer"}]}
    ) == {
        "type": "array",
        "items": {"type": "integer"},
    }
    assert normalize_tool_schema({"type": "array", "items": [{"type": "array"}]}) == {
        "type": "array",
        "items": {"type": "array", "items": {"type": "string"}},
    }
    assert normalize_tool_schema(
        {"type": "object", "properties": {}, "required": ["x"]}
    ) == {"type": "object", "properties": {}}
    assert normalize_tool_schema({"anyOf": [{"type": "null"}]}) == {
        "type": "string",
        "nullable": True,
    }
