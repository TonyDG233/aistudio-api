"""Tests for the ATG request injector (Gemini REST shape, E5 whitelist fix)."""

from __future__ import annotations

from typing import Any, cast

from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application.atg.config import load_atg_config
from aistudio_api.application.atg.injector import (
    Injector,
    InjectResult,
    _collect_existing_names,
    _generate_tool_name,
    create_injector,
)


def _injector(**overrides: object) -> Injector:
    return create_injector(load_atg_config({"mode": "prefer", **overrides}))


def _body(result: InjectResult) -> dict[str, Any]:
    return cast("dict[str, Any]", result.body)


def _names(body: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for tool in body.get("tools") or []:
        for decl in tool.get("functionDeclarations") or []:
            names.append(decl["name"])
    return names


def test_off_mode_is_verbatim_passthrough():
    inj = create_injector(load_atg_config({"mode": "off"}))
    body: dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
    result = inj.inject_gemini(body, "gemini-3.7-flash")
    assert result.synthetic_hit is False
    assert result.tool_name == ""
    assert result.body == {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


def test_non_text_model_passthrough():
    inj = _injector()
    body: dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": "draw"}]}]}
    result = inj.inject_gemini(body, "imagen-3.0-generate-002")
    assert result.synthetic_hit is False
    assert "tools" not in body


def test_guard_skips_when_client_declares_prefixed_tool():
    inj = _injector()
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        "tools": [{"functionDeclarations": [{"name": "atg_emit_client"}]}],
    }
    result = inj.inject_gemini(body, "gemini-3.7-flash")
    assert result.synthetic_hit is False
    assert result.client_synthetic is not None
    assert result.client_synthetic.hit is True
    assert _names(body) == ["atg_emit_client"]


def test_guard_disabled_wraps_anyway():
    inj = _injector(client_synthetic_guard=False)
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        "tools": [{"functionDeclarations": [{"name": "atg_emit_client"}]}],
    }
    result = inj.inject_gemini(body, "gemini-3.7-flash")
    assert result.synthetic_hit is True
    assert result.tool_name.startswith("atg_emit_")
    assert "atg_emit_client" in _names(_body(result))


def test_control_message_tail_and_head_positions():
    body: dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
    tail = _injector().inject_gemini(body, "gemini-3.7-flash")
    assert len(_body(tail)["contents"]) == 2
    assert "atg_emit_" in _body(tail)["contents"][-1]["parts"][0]["text"]

    body2: dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
    head = _injector(control_position="head").inject_gemini(body2, "gemini-3.7-flash")
    assert "atg_emit_" in _body(head)["contents"][0]["parts"][0]["text"]


def test_synthetic_declaration_content_param():
    result = _injector().inject_gemini(
        {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}, "gemini-3.7-flash"
    )
    decl = _body(result)["tools"][0]["functionDeclarations"][0]
    assert decl["name"] == result.tool_name
    assert decl["parameters"]["required"] == ["content"]
    assert decl["parameters"]["properties"]["content"]["type"] == "STRING"


def test_strict_declaration_sets_additional_properties_false():
    result = _injector(strict=True).inject_gemini(
        {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}, "gemini-3.7-flash"
    )
    decl = _body(result)["tools"][0]["functionDeclarations"][0]
    assert decl["parameters"]["additionalProperties"] is False


def test_none_mode_is_upgraded_to_auto():
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        "toolConfig": {"functionCallingConfig": {"mode": "NONE"}},
    }
    result = _injector().inject_gemini(body, "gemini-3.7-flash")
    assert _body(result)["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"


def test_any_pin_without_names_kept_as_any():
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},
    }
    result = _injector().inject_gemini(body, "gemini-3.7-flash")
    assert _body(result)["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"


def test_any_pin_with_names_merges_synthetic_name_e5():
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        "tools": [{"functionDeclarations": [{"name": "real_tool"}]}],
        "toolConfig": {
            "functionCallingConfig": {
                "mode": "ANY",
                "allowedFunctionNames": ["real_tool"],
            }
        },
    }
    result = _injector().inject_gemini(body, "gemini-3.7-flash")
    allowed = _body(result)["toolConfig"]["functionCallingConfig"][
        "allowedFunctionNames"
    ]
    assert "real_tool" in allowed
    assert result.tool_name in allowed
    assert set(_names(_body(result))) == {"real_tool", result.tool_name}


def test_any_pin_with_names_does_not_duplicate_existing_name():
    inj = _injector()
    result = inj.inject_gemini(
        {
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
            "tools": [{"functionDeclarations": [{"name": "real_tool"}]}],
            "toolConfig": {
                "functionCallingConfig": {
                    "mode": "ANY",
                    "allowedFunctionNames": ["real_tool"],
                }
            },
        },
        "gemini-3.7-flash",
    )
    allowed = _body(result)["toolConfig"]["functionCallingConfig"][
        "allowedFunctionNames"
    ]
    assert allowed.count(result.tool_name) == 1


def test_client_tools_surfaced_when_no_pattern():
    body: dict[str, Any] = {
        "tools": [
            {
                "functionDeclarations": [
                    {
                        "name": "plain_tool",
                        "parameters": {"properties": {"city": {"type": "STRING"}}},
                    }
                ]
            }
        ]
    }
    result = _injector().inject_gemini(body, "gemini-3.7-flash")
    assert result.synthetic_hit is True
    assert result.client_tools == ["plain_tool"]


def test_collect_existing_names():
    body: dict[str, Any] = {
        "tools": [
            {"functionDeclarations": [{"name": "a"}, {"name": "b"}]},
            {"functionDeclarations": [{"name": "c"}]},
        ]
    }
    assert _collect_existing_names(body) == {"a", "b", "c"}


def test_generate_tool_name_avoids_existing(monkeypatch):
    monkeypatch.setattr(
        "aistudio_api.application.atg.injector.secrets.token_hex",
        lambda _n=12: "ab" * 12,
    )
    fixed = "atg_emit_" + "ab" * 12
    assert _generate_tool_name("atg_emit_", set()) == fixed
    deduped = _generate_tool_name("atg_emit_", {fixed})
    assert deduped.startswith("atg_emit_")
    assert deduped != fixed


# ========== dump -> validate idempotence (wiring requirement) ==========


def test_request_dump_validate_roundtrip_is_idempotent():
    req = GeminiGenerateContentRequest.model_validate(
        {
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
            "systemInstruction": {"parts": [{"text": "sys"}]},
            "tools": [
                {
                    "functionDeclarations": [
                        {
                            "name": "real_tool",
                            "description": "d",
                            "parameters": {
                                "type": "OBJECT",
                                "properties": {"x": {"type": "STRING"}},
                                "required": ["x"],
                            },
                        }
                    ]
                }
            ],
            "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},
        }
    )
    dumped = req.model_dump(mode="json", exclude_none=True)
    revalidated = GeminiGenerateContentRequest.model_validate(dumped)
    assert revalidated.model_dump(mode="json", exclude_none=True) == dumped


def test_wrapped_body_survives_revalidation():
    req = GeminiGenerateContentRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
    )
    body = req.model_dump(mode="json", exclude_none=True)
    result = _injector().inject_gemini(body, "gemini-3.7-flash")
    revalidated = GeminiGenerateContentRequest.model_validate(result.body)
    assert revalidated.model_dump(mode="json", exclude_none=True) == result.body
