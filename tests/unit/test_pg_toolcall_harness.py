"""End-to-end preset-shape harness for the P5 tool-call path (Q-01 / N-12).

The harness chains the real production slices in one deterministic, offline
flow: preset fixtures -> ``normalize_gemini_request`` -> ``modify_body`` wire
body -> fake ``RequestSession`` stream -> ``StreamingGateway.stream_chat`` ->
Gemini SSE rendering.  No network, browser, account or real prompt is involved.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from aistudio_api.api.gemini_service import (
    format_sse_event,
    format_sse_usage,
)
from aistudio_api.application.chat_service import normalize_gemini_request
from aistudio_api.application.contracts.gemini import GeminiGenerateContentRequest
from aistudio_api.application.finish_reasons import to_gemini_finish_reason
from aistudio_api.application.signature_cache import DUMMY_THOUGHT_SIGNATURE
from aistudio_api.infrastructure.gateway.capture import CapturedRequest
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.streaming import StreamingGateway
from aistudio_api.infrastructure.gateway.wire_codec import modify_body

PG_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "pg"

# Minimal captured AI Studio template body; ``modify_body`` rewrites index 1.
TEMPLATE_BODY = (
    '["models/original",[[[[null,"old"]],"user"]],null,'
    '[null,null,null,128,0.5,0.8,16],"!snap",null,null]'
)
PROMPT_TEXT = "What is the weather in Paris? Use the tool."
ARG_VALUE = "Paris"
RESPONSE_TEXT = "user text"


def _load_pg_fixture(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((PG_FIXTURES / name).read_text(encoding="utf-8")),
    )


def _sse_payload(chunk: str) -> dict[str, Any]:
    assert chunk.startswith("data: ")
    return cast(dict[str, Any], json.loads(chunk.removeprefix("data: ").strip()))


def _build_preset_request() -> GeminiGenerateContentRequest:
    """Combine F-01 (missing signature) and F-04 (string response) presets."""
    f01 = _load_pg_fixture("f01_missing_signature.json")
    f04 = _load_pg_fixture("f04_string_response.json")
    contents = list(f01["request"]["contents"]) + list(f04["request"]["contents"])
    return GeminiGenerateContentRequest.model_validate({"contents": contents})


def _toolcall_frame() -> str:
    """One upstream Gemini functionCall chunk (wire indexes 10/14 style)."""
    call_part = [None] * 10 + [
        [
            "get_weather",
            [[["city", [None, None, ARG_VALUE]]]],
            "call_live_1",
        ]
    ]
    candidate = [[[call_part], "model"], None]
    chunk = [[candidate], None, [7, 4, 11]]
    return ")]}'\n" + json.dumps([[chunk]])


def _finish_frame() -> str:
    """A candidate carrying finishReason=STOP, with no extra content."""
    candidate = [[[], "model"], 1]
    chunk = [[candidate], None, [7, 4, 11]]
    return json.dumps([[chunk]])


@pytest.mark.asyncio
async def test_pg_toolcall_preset_shape_end_to_end_harness(caplog) -> None:
    req = _build_preset_request()

    caplog.set_level(logging.INFO, logger="aistudio.application.chat_service")
    normalized = normalize_gemini_request(req, "gemini-3.7-flash")

    # --- 1. normalize -> part wire: dummy signature at 14, Struct at 11 ----
    model_content = normalized.contents[1]
    assert model_content.role == "model"
    model_part_wire = cast(list[Any], model_content.parts[0].to_wire())
    assert model_part_wire[10][0] == "get_weather"
    assert model_part_wire[14] == DUMMY_THOUGHT_SIGNATURE

    string_response_wire = cast(list[Any], normalized.contents[3].parts[0].to_wire())
    assert string_response_wire[11] == [
        "emit_complete_response_x",
        [[["result", [None, None, RESPONSE_TEXT]]]],
    ]

    # The diagnostic log must not need the plaintext prompt/argument values.
    toolshape_messages = [
        record.getMessage()
        for record in caplog.records
        if "[toolshape]" in record.getMessage()
    ]
    assert toolshape_messages
    toolshape_log = "\n".join(toolshape_messages)
    assert PROMPT_TEXT not in toolshape_log
    assert ARG_VALUE not in toolshape_log
    assert RESPONSE_TEXT not in toolshape_log

    # --- 2. normalize -> modify_body wire body -----------------------------
    modified_body = modify_body(
        TEMPLATE_BODY,
        model=normalized.model,
        contents=normalized.contents,
        prompt=normalized.capture_prompt,
        tools=normalized.tools,
        tool_config=normalized.tool_config,
        safety_settings=normalized.safety_settings,
        temperature=normalized.temperature,
        top_p=normalized.top_p,
        top_k=normalized.top_k,
        max_tokens=normalized.max_tokens,
        generation_config_overrides=normalized.generation_config_overrides,
    )
    body = cast(list[Any], json.loads(modified_body))
    wire_contents = cast(list[Any], body[1])
    assert len(wire_contents) == 4

    wire_model_part = cast(list[Any], wire_contents[1][0][0])
    assert wire_model_part[10][0] == "get_weather"
    assert wire_model_part[14] == DUMMY_THOUGHT_SIGNATURE

    wire_response_part = cast(list[Any], wire_contents[3][0][0])
    assert wire_response_part[11] == [
        "emit_complete_response_x",
        [[["result", [None, None, RESPONSE_TEXT]]]],
    ]
    # A Struct payload is always a list of encoded fields, never a bare string.
    assert isinstance(wire_response_part[11][1], list)

    # --- 3. fake RequestSession stream -> StreamingGateway ----------------
    mock_session = MagicMock(spec=BrowserSession)
    received: list[str] = []

    async def fake_stream(**_kwargs):
        received.append(_kwargs.get("body", ""))
        yield ("status", 200)
        yield ("chunk", _toolcall_frame().encode("utf-8"))
        yield ("chunk", _finish_frame().encode("utf-8"))

    mock_session.send_streaming_request = MagicMock(side_effect=fake_stream)
    gateway = StreamingGateway(session=mock_session)
    captured = CapturedRequest(
        url="https://alkalimakersuite-pa.clients6.google.com/$rpc/GenerateContent",
        headers={},
        body=modified_body,
        model=normalized.model,
    )

    events = [
        event
        async for event in gateway.stream_chat(
            captured=captured, model=normalized.model
        )
    ]
    assert received == [modified_body]

    tool_call_events = [payload for kind, payload in events if kind == "tool_calls"]
    assert len(tool_call_events) == 1
    finish_events = [payload for kind, payload in events if kind == "finish_reason"]
    assert finish_events == [1]
    usage_events = [payload for kind, payload in events if kind == "usage"]
    assert len(usage_events) == 1
    usage = cast(dict[str, Any], usage_events[0])
    assert usage["prompt_tokens"] == 7
    assert usage["completion_tokens"] == 4
    assert usage["total_tokens"] == 11
    assert events[-1] == ("done", None)

    # --- 4. render final events as the Gemini SSE service does ------------
    rendered_tool = format_sse_event(
        "tool_calls", tool_call_events[0], model_version=normalized.model
    )
    assert rendered_tool is not None
    tool_payload = _sse_payload(rendered_tool)
    rendered_call = tool_payload["candidates"][0]["content"]["parts"][0]["functionCall"]
    assert rendered_call["name"] == "get_weather"
    assert rendered_call["args"] == {"city": ARG_VALUE}

    finish_reason = to_gemini_finish_reason(cast(int, finish_events[0]))
    assert finish_reason == "STOP"
    rendered_final = format_sse_usage(
        usage, finish_reason=finish_reason, model_version=normalized.model
    )
    final_payload = _sse_payload(rendered_final)
    assert final_payload["candidates"][0]["finishReason"] == "STOP"
    assert final_payload["usageMetadata"]["totalTokenCount"] == 11
