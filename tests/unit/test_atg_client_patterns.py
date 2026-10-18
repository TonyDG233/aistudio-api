"""Tests for the client-synthetic transport detector (5 signals)."""

from __future__ import annotations

from aistudio_api.application.atg.client_patterns import (
    detect_client_synthetic_transport,
)


def _decl(name, *, description="", properties=None, required=None, param_type="STRING"):
    params = {
        "type": "OBJECT",
        "properties": {k: {"type": param_type} for k in (properties or [])},
        "required": required or [],
    }
    return {"name": name, "description": description, "parameters": params}


def test_returns_none_without_tools():
    assert detect_client_synthetic_transport({"contents": []}) is None
    assert detect_client_synthetic_transport("not-a-dict") is None


def test_signal_1_prefixed_name():
    body = {"tools": [{"functionDeclarations": [_decl("atg_emit_deadbeef")]}]}
    det = detect_client_synthetic_transport(body, "atg_emit_")
    assert det is not None
    assert det.hit is True
    assert det.reason == "prefixed-name"
    assert det.name == "atg_emit_deadbeef"


def test_signal_2_declaration_intent():
    body = {
        "tools": [
            {
                "functionDeclarations": [
                    _decl(
                        "weird_name",
                        description="Deliver your complete final answer as text",
                        properties=["content"],
                    )
                ]
            }
        ]
    }
    det = detect_client_synthetic_transport(body, "atg_emit_")
    assert det is not None
    assert det.hit is True
    assert det.reason == "declaration-intent"


def test_signal_3_forced_content_tool():
    body = {
        "tools": [
            {"functionDeclarations": [_decl("emit_answer", properties=["content"])]}
        ],
        "toolConfig": {
            "functionCallingConfig": {"allowedFunctionNames": ["emit_answer"]}
        },
    }
    det = detect_client_synthetic_transport(body, "atg_emit_")
    assert det is not None
    assert det.hit is True
    assert det.reason == "forced-content-tool"


def test_signal_4_history_loop():
    body = {
        "tools": [
            {"functionDeclarations": [_decl("send_reply", properties=["content"])]}
        ],
        "contents": [
            {
                "role": "model",
                "parts": [
                    {"functionCall": {"name": "send_reply", "args": {"content": "x"}}}
                ],
            },
            {
                "role": "user",
                "parts": [{"functionResponse": {"name": "send_reply", "response": {}}}],
            },
        ],
    }
    det = detect_client_synthetic_transport(body, "atg_emit_")
    assert det is not None
    assert det.hit is True
    assert det.reason == "history-loop"


def test_signal_5_instruction_pattern():
    body = {
        "systemInstruction": {
            "parts": [{"text": "Deliver your complete final answer before finishing."}]
        },
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
    }
    det = detect_client_synthetic_transport(body, "atg_emit_")
    assert det is not None
    assert det.hit is True
    assert det.reason == "instruction-pattern"
    assert det.name == ""


def test_declared_tools_present_but_no_hit():
    body = {
        "tools": [{"functionDeclarations": [_decl("plain_tool", properties=["city"])]}],
        "contents": [{"role": "user", "parts": [{"text": "weather?"}]}],
    }
    det = detect_client_synthetic_transport(body, "atg_emit_")
    assert det is not None
    assert det.hit is False
    assert det.reason == "declared-tools-present"
    assert det.tools == ["plain_tool"]


def test_history_loop_requires_name_hit():
    # content-shaped but the tool name has no answer-intent token -> no history hit
    body = {
        "tools": [{"functionDeclarations": [_decl("calc", properties=["content"])]}],
        "contents": [
            {
                "role": "model",
                "parts": [{"functionCall": {"name": "calc", "args": {}}}],
            },
            {
                "role": "user",
                "parts": [{"functionResponse": {"name": "calc", "response": {}}}],
            },
        ],
    }
    det = detect_client_synthetic_transport(body, "atg_emit_")
    assert det is not None
    assert det.hit is False
