"""PG-08 lenient preset history normalization (P5 B5b).

Every case here must produce a legal Gemini tool conversation instead of a
400.  Warnings are sanitized and never echo prompt/tool content.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from aistudio_api.application.contracts.openai import OpenAIChatRequest
from aistudio_api.application.format_converters.media import MediaFetcher
from aistudio_api.application.format_converters.openai_request import (
    convert_openai_request,
)


async def _convert(messages: list[dict[str, Any]]) -> dict[str, Any]:
    req = OpenAIChatRequest.model_validate(
        {"model": "gemini-3.7-flash", "messages": messages}
    )
    return await convert_openai_request(req, MediaFetcher())


def _parts(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        part
        for content in body["contents"]
        for part in content["parts"]
        if isinstance(part, dict)
    ]


def _calls(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [part["functionCall"] for part in _parts(body) if "functionCall" in part]


def _responses(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        part["functionResponse"]
        for part in _parts(body)
        if "functionResponse" in part
    ]


@pytest.mark.asyncio
async def test_missing_and_duplicate_tool_call_ids_are_synthesized():
    body = await _convert(
        [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"type": "function", "function": {"name": "a", "arguments": "{}"}},
                    {"type": "function", "function": {"name": "b", "arguments": "{}"}},
                ],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_auto_1_0",
                        "type": "function",
                        "function": {"name": "c", "arguments": "{}"},
                    }
                ],
            },
        ]
    )
    calls = _calls(body)
    ids = [call["id"] for call in calls]
    assert len(ids) == len(set(ids)) == 3
    assert all(str(call_id).startswith("call_auto_") for call_id in ids)


@pytest.mark.asyncio
async def test_missing_tool_call_id_matches_last_pending_call():
    body = await _convert(
        [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_x",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "content": "result"},
        ]
    )
    response = _responses(body)[0]
    assert response["name"] == "lookup"
    assert response["id"] == "call_x"
    assert response["response"] == {"result": "result"}


@pytest.mark.asyncio
async def test_unknown_tool_call_id_matches_pending_by_declared_name():
    body = await _convert(
        [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_real",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_unknown",
                "name": "lookup",
                "content": "result",
            },
        ]
    )
    response = _responses(body)[0]
    assert response["name"] == "lookup"
    assert response["id"] == "call_unknown"


@pytest.mark.asyncio
async def test_isolated_tool_output_without_assistant_call_is_kept():
    body = await _convert(
        [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "call_orphan", "content": "orphan"},
            {"role": "tool", "content": "extra"},
        ]
    )
    responses = _responses(body)
    assert len(responses) == 2
    assert responses[0]["name"] == "tool_output"
    assert responses[0]["response"] == {"result": "orphan"}
    assert responses[1]["response"] == {"result": "extra"}
    assert responses[1]["id"] != responses[0]["id"]


@pytest.mark.asyncio
async def test_user_prompt_in_tool_content_is_preserved_as_struct():
    body = await _convert(
        [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "emit", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "USER PROMPT TEXT"},
        ]
    )
    assert _responses(body)[0]["response"] == {"result": "USER PROMPT TEXT"}


@pytest.mark.asyncio
async def test_extra_tool_outputs_stay_as_additional_responses(caplog):
    caplog.set_level(logging.WARNING)
    secret = "SECRET_TOOL_OUTPUT_VALUE"
    body = await _convert(
        [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "first"},
            {
                "role": "tool",
                "tool_call_id": "call_extra",
                "content": secret,
            },
        ]
    )
    responses = _responses(body)
    assert len(responses) == 2
    assert responses[1]["name"] == "tool_output"
    assert responses[1]["response"] == {"result": secret}
    warnings = [
        record.getMessage()
        for record in caplog.records
        if "[pg08]" in record.getMessage()
    ]
    assert warnings
    assert all(secret not in message for message in warnings)
    assert all("lookup" not in message for message in warnings)
