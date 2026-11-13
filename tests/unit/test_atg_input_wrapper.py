"""Tests for ATG input-side synthetic tool wrapping and dual-track media routing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from aistudio_api.application.atg import AtgProcessor, create_atg_processor
from aistudio_api.application.atg.config import load_atg_config
from aistudio_api.application.atg.history_purge import purge_synthetic_history
from aistudio_api.application.atg.input_wrapper import (
    CONTEXT_INSTRUCTION,
    DUMMY_THOUGHT_SIGNATURE,
    INIT_CONTEXT_TEXT,
    INPUT_TOOL_PREFIX,
    wrap_input_contents,
)
from aistudio_api.application.chat_service import normalize_gemini_request
from aistudio_api.application.contracts.gemini import GeminiGenerateContentRequest
from aistudio_api.infrastructure.gateway.wire_codec import AistudioWireCodec
from aistudio_api.infrastructure.gateway.wire_spec import PartIndex


def _declared_names(body: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        for decl in tool.get("functionDeclarations") or []:
            if isinstance(decl, dict) and isinstance(decl.get("name"), str):
                names.append(decl["name"])
    return names


def _base_text_request() -> dict[str, Any]:
    return {
        "contents": [
            {"role": "user", "parts": [{"text": "Hello"}]},
            {
                "role": "model",
                "parts": [
                    {"text": "internal thinking", "thought": True},
                    {"text": "Hi! How can I help?"},
                ],
            },
            {"role": "user", "parts": [{"text": "Write a poem."}]},
        ]
    }


def test_wrap_input_inherits_mode_by_format(monkeypatch):
    features = {
        "atg": {
            "mode": "off",
            "wrap_input": True,
            "mode_by_format": {
                "gemini": "off",
                "openai": "prefer",
                "claude": "required",
                "responses": "inherit",
            },
        }
    }
    monkeypatch.setattr(
        "aistudio_api.application.atg.config.get_features_config",
        lambda: features,
    )

    processor = create_atg_processor()
    assert processor.cfg.wrap_input is True

    # Off / inherit->off protocols pass through untouched (neither input nor output wrapped)
    for off_fmt in ("gemini", "responses"):
        req = _base_text_request()
        res = processor.wrap(req, "gemini-3.7-flash", api_format=off_fmt)
        assert res.synthetic_hit is False
        assert res.tool_name == ""
        assert res.body == _base_text_request()

    # Enabled protocols wrap input (atg_ctx_*) AND output (atg_emit_*)
    for active_fmt in ("openai", "claude"):
        req = _base_text_request()
        res = processor.wrap(req, "gemini-3.7-flash", api_format=active_fmt)
        assert res.synthetic_hit is True
        assert res.tool_name.startswith("atg_emit_")
        wrapped = cast("dict[str, Any]", res.body)
        names = _declared_names(wrapped)
        assert any(name.startswith(INPUT_TOOL_PREFIX) for name in names)
        assert res.tool_name in names
        assert len(wrapped["contents"]) == 4
        assert wrapped["contents"][0]["parts"][0]["text"] == INIT_CONTEXT_TEXT


def test_wrap_input_via_generation_service_create_atg(monkeypatch):
    from aistudio_api.application.generation_service import _create_atg

    features = {
        "atg": {
            "mode": "prefer",
            "wrap_input": True,
            "mode_by_format": {
                "gemini": "off",
                "openai": "inherit",
            },
        }
    }
    monkeypatch.setattr(
        "aistudio_api.application.atg.config.get_features_config",
        lambda: features,
    )

    gemini_atg = _create_atg("gemini", None)
    assert gemini_atg.enabled is False
    gemini_res = gemini_atg.wrap(_base_text_request(), "gemini-3.7-flash")
    assert gemini_res.synthetic_hit is False
    assert gemini_res.body == _base_text_request()

    openai_atg = _create_atg("openai", None)
    assert openai_atg.enabled is True
    assert openai_atg.cfg.wrap_input is True
    openai_res = openai_atg.wrap(_base_text_request(), "gemini-3.7-flash")
    assert openai_res.synthetic_hit is True
    wrapped = cast("dict[str, Any]", openai_res.body)
    assert any(n.startswith(INPUT_TOOL_PREFIX) for n in _declared_names(wrapped))


def test_wrap_input_plain_text_standalone_and_with_processor():
    raw = _base_text_request()
    wrapped, ctx_name = wrap_input_contents(raw)
    assert ctx_name is not None
    assert ctx_name.startswith(INPUT_TOOL_PREFIX)
    # Original input dict is not mutated
    assert len(raw["contents"]) == 3
    assert "tools" not in raw

    contents = cast("list[dict[str, Any]]", wrapped["contents"])
    assert len(contents) == 3

    # Turn 0: user initializer
    assert contents[0] == {"role": "user", "parts": [{"text": INIT_CONTEXT_TEXT}]}

    # Turn 1: model functionCall with PG-01 thoughtSignature
    assert contents[1]["role"] == "model"
    t1_part = contents[1]["parts"][0]
    assert t1_part["thoughtSignature"] == DUMMY_THOUGHT_SIGNATURE
    assert t1_part["functionCall"] == {
        "id": f"call_{ctx_name}",
        "name": ctx_name,
        "args": {"scope": "conversation"},
    }

    # Turn 2: user functionResponse with structured turns
    assert contents[2]["role"] == "user"
    assert len(contents[2]["parts"]) == 1
    fr = contents[2]["parts"][0]["functionResponse"]
    assert fr["id"] == f"call_{ctx_name}"
    assert fr["name"] == ctx_name
    resp = fr["response"]
    assert resp["attached_media_count"] == 0
    assert resp["instruction"] == CONTEXT_INSTRUCTION
    assert resp["conversation_turns"] == [
        {"index": 0, "role": "user", "content": "Hello"},
        {
            "index": 1,
            "role": "model",
            "content": "Hi! How can I help?",
            "thought": "internal thinking",
        },
        {"index": 2, "role": "user", "content": "Write a poem."},
    ]

    # Declaration registered in tools[0]
    tools = cast("list[dict[str, Any]]", wrapped["tools"])
    decls = cast("list[dict[str, Any]]", tools[0]["functionDeclarations"])
    assert len(decls) == 1
    assert decls[0]["name"] == ctx_name
    assert "DO NOT call this tool again" in decls[0]["description"]
    assert decls[0]["parameters"] == {
        "type": "OBJECT",
        "properties": {"scope": {"type": "STRING"}},
    }


def test_wrap_input_allowed_function_names_whitelist_preservation():
    raw: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": "Hi"}]}],
        "tools": [
            {
                "functionDeclarations": [
                    {
                        "name": "real_tool",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {"x": {"type": "STRING"}},
                        },
                    }
                ]
            }
        ],
        "toolConfig": {
            "functionCallingConfig": {
                "mode": "ANY",
                "allowedFunctionNames": ["real_tool"],
            }
        },
    }

    proc = AtgProcessor(load_atg_config({"mode": "prefer", "wrap_input": True}))
    res = proc.wrap(raw, "gemini-3.7-flash")
    assert res.synthetic_hit is True
    wrapped = cast("dict[str, Any]", res.body)

    names = _declared_names(wrapped)
    ctx_names = [n for n in names if n.startswith(INPUT_TOOL_PREFIX)]
    emit_names = [n for n in names if n.startswith("atg_emit_")]
    assert len(ctx_names) == 1
    assert len(emit_names) == 1

    allowed = wrapped["toolConfig"]["functionCallingConfig"]["allowedFunctionNames"]
    assert "real_tool" in allowed
    assert ctx_names[0] in allowed
    assert emit_names[0] in allowed


def test_wrap_input_dual_track_media_end_to_end_wire_slots():
    img1_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    img2_b64 = "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="
    raw: dict[str, Any] = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": "Compare these two images:"},
                    {"inlineData": {"mimeType": "image/png", "data": img1_b64}},
                ],
            },
            {
                "role": "model",
                "parts": [{"text": "Sure, send the second question."}],
            },
            {
                "role": "user",
                "parts": [
                    {"inlineData": {"mimeType": "image/jpeg", "data": img2_b64}},
                    {"text": "What changed?"},
                ],
            },
        ]
    }

    proc = AtgProcessor(load_atg_config({"mode": "prefer", "wrap_input": True}))
    res = proc.wrap(raw, "gemini-3.7-flash")
    assert res.synthetic_hit is True
    wrapped = cast("dict[str, Any]", res.body)

    # 3 dual-track turns + 1 output control turn at tail
    assert len(wrapped["contents"]) == 4
    turn2_parts = wrapped["contents"][2]["parts"]
    assert len(turn2_parts) == 3  # functionResponse + 2 inlineData media parts
    fr_resp = turn2_parts[0]["functionResponse"]["response"]
    assert fr_resp["attached_media_count"] == 2
    assert (
        "[Attached Media #1: image/png]" in fr_resp["conversation_turns"][0]["content"]
    )
    assert (
        "[Attached Media #2: image/jpeg]" in fr_resp["conversation_turns"][2]["content"]
    )
    assert "Compare these two images:" in fr_resp["conversation_turns"][0]["content"]
    assert "What changed?" in fr_resp["conversation_turns"][2]["content"]
    assert turn2_parts[1] == {"inlineData": {"mimeType": "image/png", "data": img1_b64}}
    assert turn2_parts[2] == {
        "inlineData": {"mimeType": "image/jpeg", "data": img2_b64}
    }

    # Pydantic validation + normalization + wire encoding
    gemini_req = GeminiGenerateContentRequest.model_validate(wrapped)
    normalized = normalize_gemini_request(gemini_req, "gemini-3.7-flash")
    assert normalized.capture_images == [
        ("image/png", img1_b64),
        ("image/jpeg", img2_b64),
    ]

    codec = AistudioWireCodec()
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "test_input.json"
    wire_req = codec.decode(fixture.read_text(encoding="utf-8"))
    wire_req.model = normalized.model
    wire_req.contents = normalized.contents
    wire_req.tools = normalized.tools
    wire_req.tool_config = normalized.tool_config
    encoded = codec.encode(wire_req)

    wire_payload = json.loads(encoded)
    wire_contents = wire_payload[codec.CONTENTS_INDEX]
    assert len(wire_contents) == 4

    # Turn 1 Part 0 -> slot [10] FUNCTION_CALL_ALT + [14] THOUGHT_SIGNATURE
    turn1_wire_part = wire_contents[1][0][0]
    assert isinstance(turn1_wire_part[PartIndex.FUNCTION_CALL_ALT], list)
    assert turn1_wire_part[PartIndex.FUNCTION_CALL_ALT][0].startswith(INPUT_TOOL_PREFIX)
    assert turn1_wire_part[PartIndex.THOUGHT_SIGNATURE] == DUMMY_THOUGHT_SIGNATURE

    # Turn 2 Part 0 -> slot [11] FUNCTION_RESPONSE_ALT
    turn2_fr_wire_part = wire_contents[2][0][0]
    assert isinstance(turn2_fr_wire_part[PartIndex.FUNCTION_RESPONSE_ALT], list)
    assert turn2_fr_wire_part[PartIndex.FUNCTION_RESPONSE_ALT][0].startswith(
        INPUT_TOOL_PREFIX
    )

    # Turn 2 Part 1 & 2 -> slot [2] INLINE_DATA
    turn2_img1_wire_part = wire_contents[2][0][1]
    turn2_img2_wire_part = wire_contents[2][0][2]
    assert turn2_img1_wire_part[PartIndex.INLINE_DATA] == ["image/png", img1_b64]
    assert turn2_img2_wire_part[PartIndex.INLINE_DATA] == ["image/jpeg", img2_b64]


def test_wrap_input_skips_when_real_business_tool_calls_exist():
    raw_fc: dict[str, Any] = {
        "contents": [
            {"role": "user", "parts": [{"text": "Check weather"}]},
            {
                "role": "model",
                "parts": [
                    {"functionCall": {"name": "get_weather", "args": {"city": "NYC"}}}
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "name": "get_weather",
                            "response": {"temp": "20C"},
                        }
                    }
                ],
            },
        ],
        "tools": [
            {
                "functionDeclarations": [
                    {
                        "name": "get_weather",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {"city": {"type": "STRING"}},
                        },
                    }
                ]
            }
        ],
    }

    skipped, ctx_name = wrap_input_contents(raw_fc)
    assert ctx_name is None
    assert skipped is raw_fc

    # Full processor still skips input wrap while keeping real tool chain & injecting atg_emit_*
    proc = AtgProcessor(load_atg_config({"mode": "prefer", "wrap_input": True}))
    res = proc.wrap(raw_fc, "gemini-3.7-flash")
    assert res.synthetic_hit is True
    wrapped = cast("dict[str, Any]", res.body)
    names = _declared_names(wrapped)
    assert "get_weather" in names
    assert res.tool_name in names
    assert not any(n.startswith(INPUT_TOOL_PREFIX) for n in names)
    # Original 3 turns preserved + 1 output control turn
    assert len(wrapped["contents"]) == 4
    assert wrapped["contents"][1]["parts"][0]["functionCall"]["name"] == "get_weather"


def test_wrap_input_skips_empty_or_non_list_contents_and_non_text_models():
    assert wrap_input_contents({}) == ({}, None)
    assert wrap_input_contents({"contents": []}) == ({"contents": []}, None)
    assert wrap_input_contents({"contents": "bad"}) == ({"contents": "bad"}, None)
    empty_text: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": ""}]}]
    }
    assert wrap_input_contents(empty_text) == (empty_text, None)

    proc = AtgProcessor(load_atg_config({"mode": "prefer", "wrap_input": True}))
    img_req: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": "draw"}]}]
    }
    res = proc.wrap(img_req, "imagen-3.0-generate-002")
    assert res.synthetic_hit is False
    assert "tools" not in res.body


def test_wrap_input_client_synthetic_guard_prevents_false_skip_and_respects_real_guard():
    # Ensure a normal request with wrap_input=True is NOT blocked by client_synthetic_guard
    proc = AtgProcessor(load_atg_config({"mode": "prefer", "wrap_input": True}))
    res = proc.wrap(_base_text_request(), "gemini-3.7-flash")
    assert res.synthetic_hit is True
    assert res.client_synthetic is None

    # Ensure a client-provided atg_emit_ tool skips BOTH wrap_input and output wrap
    client_guarded: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
        "tools": [{"functionDeclarations": [{"name": "atg_emit_client"}]}],
    }
    res2 = proc.wrap(client_guarded, "gemini-3.7-flash")
    assert res2.synthetic_hit is False
    assert res2.client_synthetic is not None
    body2 = cast("dict[str, Any]", res2.body)
    assert len(body2["contents"]) == 1


def test_purge_synthetic_history_cleans_both_prefixes_and_anchor_on_replay():
    stale_req: dict[str, Any] = {
        "contents": [
            {"role": "user", "parts": [{"text": INIT_CONTEXT_TEXT}]},
            {
                "role": "model",
                "parts": [
                    {
                        "functionCall": {
                            "id": "call_atg_ctx_old123",
                            "name": "atg_ctx_old123",
                            "args": {"scope": "conversation"},
                        }
                    }
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "id": "call_atg_ctx_old123",
                            "name": "atg_ctx_old123",
                            "response": {"conversation_turns": []},
                        }
                    }
                ],
            },
            {
                "role": "model",
                "parts": [
                    {
                        "functionCall": {
                            "name": "atg_emit_old456",
                            "args": {"content": "old answer"},
                        }
                    }
                ],
            },
            {"role": "user", "parts": [{"text": "Follow-up question"}]},
        ],
        "tools": [
            {
                "functionDeclarations": [
                    {"name": "atg_ctx_old123"},
                    {"name": "real_tool"},
                ]
            }
        ],
        "toolConfig": {
            "functionCallingConfig": {
                "mode": "ANY",
                "allowedFunctionNames": ["atg_ctx_old123", "real_tool"],
            }
        },
    }

    purged = cast("dict[str, Any]", purge_synthetic_history(stale_req, "atg_emit_"))
    assert purged["contents"] == [
        {"role": "user", "parts": [{"text": "Follow-up question"}]}
    ]
    assert _declared_names(purged) == ["real_tool"]
    assert purged["toolConfig"]["functionCallingConfig"]["allowedFunctionNames"] == [
        "real_tool"
    ]

    # Re-wrapping via processor produces a clean single layer without nesting stale atg_ctx_*
    proc = AtgProcessor(load_atg_config({"mode": "prefer", "wrap_input": True}))
    res = proc.wrap(stale_req, "gemini-3.7-flash")
    assert res.synthetic_hit is True
    wrapped = cast("dict[str, Any]", res.body)
    ctx_names = [n for n in _declared_names(wrapped) if n.startswith(INPUT_TOOL_PREFIX)]
    assert len(ctx_names) == 1
    assert ctx_names[0] != "atg_ctx_old123"
    fr = wrapped["contents"][2]["parts"][0]["functionResponse"]["response"]
    assert fr["conversation_turns"] == [
        {"index": 0, "role": "user", "content": "Follow-up question"}
    ]


def test_wrap_input_only_without_wrap_output_skips_output_tool_and_control():
    raw: dict[str, Any] = {
        **_base_text_request(),
        "toolConfig": {"functionCallingConfig": {"mode": "NONE"}},
    }
    proc = AtgProcessor(
        load_atg_config(
            {
                "mode": "required",
                "wrap_input": True,
                "wrap_output": False,
            }
        )
    )
    res = proc.wrap(raw, "gemini-3.7-flash")
    assert res.synthetic_hit is True
    assert res.tool_name == ""
    wrapped = cast("dict[str, Any]", res.body)
    names = _declared_names(wrapped)
    assert any(n.startswith(INPUT_TOOL_PREFIX) for n in names)
    assert not any(n.startswith("atg_emit_") for n in names)
    # 3 dual-track turns only, no trailing output control turn
    assert len(wrapped["contents"]) == 3
    assert wrapped["contents"][0]["parts"][0]["text"] == INIT_CONTEXT_TEXT
    # toolConfig was not rewritten for output tool
    assert wrapped["toolConfig"]["functionCallingConfig"]["mode"] == "NONE"

    # unwrap_response is a no-op when wrap_output=False
    from aistudio_api.domain.models import Candidate, ModelOutput

    output = ModelOutput(candidates=[Candidate(text="native stream answer")], usage={})
    unwrapped = proc.unwrap_response(output, "")
    assert unwrapped.output.candidates[0].text == "native stream answer"
    assert unwrapped.stats.synthetic_hit is False


def test_wrap_output_only_without_wrap_input():
    raw = _base_text_request()
    proc = AtgProcessor(
        load_atg_config(
            {
                "mode": "prefer",
                "wrap_input": False,
                "wrap_output": True,
            }
        )
    )
    res = proc.wrap(raw, "gemini-3.7-flash")
    assert res.synthetic_hit is True
    assert res.tool_name.startswith("atg_emit_")
    wrapped = cast("dict[str, Any]", res.body)
    names = _declared_names(wrapped)
    assert not any(n.startswith(INPUT_TOOL_PREFIX) for n in names)
    assert res.tool_name in names
    assert len(wrapped["contents"]) == 4


def test_wrap_input_false_and_wrap_output_false_returns_passthrough():
    raw = _base_text_request()
    proc = AtgProcessor(
        load_atg_config(
            {
                "mode": "prefer",
                "wrap_input": False,
                "wrap_output": False,
            }
        )
    )
    res = proc.wrap(raw, "gemini-3.7-flash")
    assert res.synthetic_hit is False
    assert res.tool_name == ""
    assert res.body == _base_text_request()


def test_custom_control_template_and_input_context_instruction():
    # 1) Custom control_template with {tool_name} and custom input_context_instruction
    proc = AtgProcessor(
        load_atg_config(
            {
                "mode": "prefer",
                "wrap_input": True,
                "wrap_output": True,
                "control_template": "CUSTOM_CTRL::{tool_name}",
                "input_context_instruction": "CUSTOM_INPUT_INST::{tool_name}",
            }
        )
    )
    res = proc.wrap(_base_text_request(), "gemini-3.7-flash")
    assert res.synthetic_hit is True
    wrapped = cast("dict[str, Any]", res.body)
    ctx_names = [n for n in _declared_names(wrapped) if n.startswith(INPUT_TOOL_PREFIX)]
    assert len(ctx_names) == 1
    fr_resp = wrapped["contents"][2]["parts"][0]["functionResponse"]["response"]
    assert fr_resp["instruction"] == f"CUSTOM_INPUT_INST::{ctx_names[0]}"
    assert wrapped["contents"][3]["parts"][0]["text"] == f"CUSTOM_CTRL::{res.tool_name}"

    # 2) Custom control_template WITHOUT {tool_name} placeholder auto-appends function name
    proc_no_placeholder = AtgProcessor(
        load_atg_config(
            {
                "mode": "prefer",
                "wrap_input": False,
                "wrap_output": True,
                "control_template": "Always use the emit tool.",
            }
        )
    )
    res2 = proc_no_placeholder.wrap(_base_text_request(), "gemini-3.7-flash")
    wrapped2 = cast("dict[str, Any]", res2.body)
    assert (
        wrapped2["contents"][-1]["parts"][0]["text"]
        == f"Always use the emit tool. (Function name: {res2.tool_name})"
    )

    # 3) Standalone wrap_input_contents with custom & blank instruction
    standalone_raw, _ = wrap_input_contents(
        _base_text_request(), context_instruction="Standalone instruction."
    )
    standalone = cast("dict[str, Any]", standalone_raw)
    assert (
        standalone["contents"][2]["parts"][0]["functionResponse"]["response"][
            "instruction"
        ]
        == "Standalone instruction."
    )
    blank_raw, _ = wrap_input_contents(_base_text_request(), context_instruction=" ")
    blank_fallback = cast("dict[str, Any]", blank_raw)
    assert (
        blank_fallback["contents"][2]["parts"][0]["functionResponse"]["response"][
            "instruction"
        ]
        == CONTEXT_INSTRUCTION
    )


def test_stream_generation_with_wrap_input_only_passes_native_body(monkeypatch):
    import asyncio
    from unittest.mock import MagicMock

    from aistudio_api.application import generation_service
    from tests.unit.routing_fakes import ScriptedPipeline, make_attempt, make_context

    monkeypatch.setattr(
        generation_service,
        "create_atg_processor",
        lambda base=None: create_atg_processor(
            {
                **(base or {}),
                "mode": "required",
                "wrap_input": True,
                "wrap_output": False,
            }
        ),
    )

    captured: dict[str, Any] = {}
    client = MagicMock()

    async def fake_stream(**kwargs):
        captured["tools"] = kwargs.get("tools")
        captured["contents"] = kwargs.get("contents")
        yield ("body", "native ")
        yield ("body", "stream!")
        yield ("usage", {"prompt_tokens": 1, "completion_tokens": 2})
        yield ("done", None)

    client.stream_generate_content = fake_stream
    pipeline = ScriptedPipeline([make_attempt(client)], context=make_context())
    req = GeminiGenerateContentRequest.model_validate(_base_text_request())

    async def run():
        return [
            ev
            async for ev in generation_service.stream_generation(
                model_path="gemini-3.7-flash",
                req=req,
                pipeline=pipeline,
                api_format="gemini",
            )
        ]

    events = asyncio.run(run())
    body_text = "".join(str(ev.payload) for ev in events if ev.kind == "body")
    assert body_text == "native stream!"
    # Input was wrapped into 3 turns with atg_ctx_*, and NO atg_emit_* was injected
    assert len(captured["contents"]) == 3
    wire_tool_names = [
        decl[0]
        for tool in (captured["tools"] or [])
        for decl in (tool[1] if len(tool) > 1 and isinstance(tool[1], list) else [])
        if isinstance(decl, list) and decl
    ]
    assert any(str(name).startswith(INPUT_TOOL_PREFIX) for name in wire_tool_names)
    assert not any(str(name).startswith("atg_emit_") for name in wire_tool_names)
