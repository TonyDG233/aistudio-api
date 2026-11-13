"""Tests for ATG config loading/clamping and the model filter."""

from __future__ import annotations

import os
from typing import Any, cast

from aistudio_api.application.atg.config import (
    DEFAULT_CONTROL_TEMPLATE,
    DEFAULT_INPUT_CONTEXT_INSTRUCTION,
    AtgConfig,
    load_atg_config,
    resolve_atg_mode,
)
from aistudio_api.application.atg.model_filter import create_model_filter
from aistudio_api.infrastructure.gateway.model_defaults import (
    get_features_config,
    invalidate_config_cache,
)

MB = 1024 * 1024


def _features(config_path: str | os.PathLike[str]) -> dict[str, Any]:
    return cast("dict[str, Any]", get_features_config(config_path))


# ========== Defaults ==========


def test_load_atg_config_defaults():
    cfg = load_atg_config()
    assert isinstance(cfg, AtgConfig)
    assert cfg.mode == "off"
    assert cfg.enabled is False
    assert cfg.tool_prefix == "atg_emit_"
    assert cfg.control_position == "tail"
    assert cfg.strict is False
    assert cfg.wrap_input is False
    assert cfg.wrap_output is True
    assert cfg.control_template == DEFAULT_CONTROL_TEMPLATE
    assert cfg.input_context_instruction == DEFAULT_INPUT_CONTEXT_INSTRUCTION
    assert cfg.empty_retries == 2
    assert cfg.recovery == "repair"
    assert cfg.side_buffer_bytes == 0
    assert cfg.client_synthetic_guard is True
    assert cfg.max_response_bytes == 16 * MB
    exported = cfg.to_dict()
    assert exported["wrap_input"] is False
    assert exported["wrap_output"] is True
    assert exported["control_template"] == DEFAULT_CONTROL_TEMPLATE
    assert exported["input_context_instruction"] == DEFAULT_INPUT_CONTEXT_INSTRUCTION


def test_load_atg_config_feature_defaults_expose_max_response_bytes(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("api_keys: []\n", encoding="utf-8")
    atg = _features(config_path)["atg"]
    assert atg["max_response_bytes"] == 16 * MB
    assert atg["wrap_input"] is False
    assert atg["wrap_output"] is True
    assert atg["control_template"] == DEFAULT_CONTROL_TEMPLATE
    assert atg["input_context_instruction"] == DEFAULT_INPUT_CONTEXT_INSTRUCTION


def test_enabled_derived_from_mode():
    assert load_atg_config({"mode": "prefer"}).enabled is True
    assert load_atg_config({"mode": "required"}).enabled is True
    assert load_atg_config({"mode": "off"}).enabled is False


def test_resolve_atg_mode_by_format(monkeypatch):
    features = {
        "atg": {
            "mode": "prefer",
            "mode_by_format": {
                "gemini": "off",
                "openai": "inherit",
                "claude": "required",
                "responses": "bogus",
            },
        }
    }
    monkeypatch.setattr(
        "aistudio_api.application.atg.config.get_features_config",
        lambda: features,
    )
    assert resolve_atg_mode("gemini") == "off"
    assert resolve_atg_mode("openai") == "prefer"
    assert resolve_atg_mode("claude") == "required"
    assert resolve_atg_mode("responses") == "prefer"


def test_resolve_atg_mode_global_fallback(monkeypatch):
    features = {"atg": {"mode": "required"}}
    monkeypatch.setattr(
        "aistudio_api.application.atg.config.get_features_config",
        lambda: features,
    )
    assert resolve_atg_mode("gemini") == "required"


def test_create_atg_uses_format_mode_for_gemini(monkeypatch):
    from aistudio_api.application.generation_service import _create_atg

    features = {
        "atg": {
            "mode": "off",
            "mode_by_format": {"gemini": "prefer"},
        }
    }
    monkeypatch.setattr(
        "aistudio_api.application.atg.config.get_features_config",
        lambda: features,
    )
    assert _create_atg("gemini", None).cfg.mode == "prefer"


# ========== Normalization / clamping semantics (aligned with Ark config.js) ==========


def test_mode_invalid_falls_back_to_off():
    for bogus in ("bogus", "", None, "REQUIRE"):
        assert load_atg_config({"mode": bogus}).mode == "off"
    assert load_atg_config({"mode": "REQUIRED"}).mode == "required"
    assert load_atg_config({"mode": " Prefer "}).mode == "prefer"


def test_bounded_int_clamping():
    assert load_atg_config({"empty_retries": 99}).empty_retries == 5
    assert load_atg_config({"empty_retries": -3}).empty_retries == 0
    assert load_atg_config({"empty_retries": "abc"}).empty_retries == 2
    assert load_atg_config({"empty_retries": "4x"}).empty_retries == 4

    assert load_atg_config({"side_buffer_bytes": -5}).side_buffer_bytes == 0
    assert load_atg_config({"side_buffer_bytes": 10**12}).side_buffer_bytes == 16 * MB

    assert load_atg_config({"max_response_bytes": 500}).max_response_bytes == 1024
    assert (
        load_atg_config({"max_response_bytes": 10**12}).max_response_bytes == 100 * MB
    )
    assert (
        load_atg_config({"max_response_bytes": "not-int"}).max_response_bytes == 16 * MB
    )


def test_choice_clamping():
    assert load_atg_config({"control_position": "middle"}).control_position == "tail"
    assert load_atg_config({"control_position": "head"}).control_position == "head"
    assert load_atg_config({"recovery": "explode"}).recovery == "repair"
    assert load_atg_config({"recovery": "fail"}).recovery == "fail"


def test_tool_prefix_falls_back_when_blank():
    assert load_atg_config({"tool_prefix": "   "}).tool_prefix == "atg_emit_"
    assert load_atg_config({"tool_prefix": "xx_"}).tool_prefix == "xx_"


def test_bool_coercion():
    assert load_atg_config({"strict": "true"}).strict is True
    assert load_atg_config({"strict": "off"}).strict is False
    assert load_atg_config({"wrap_input": True}).wrap_input is True
    assert load_atg_config({"wrap_input": "true"}).wrap_input is True
    assert load_atg_config({"wrap_input": "off"}).wrap_input is False
    assert load_atg_config({"wrap_input": 0}).wrap_input is False
    assert load_atg_config({"wrap_output": True}).wrap_output is True
    assert load_atg_config({"wrap_output": "true"}).wrap_output is True
    assert load_atg_config({"wrap_output": "off"}).wrap_output is False
    assert load_atg_config({"wrap_output": 0}).wrap_output is False
    assert (
        load_atg_config({"client_synthetic_guard": "off"}).client_synthetic_guard
        is False
    )
    assert (
        load_atg_config({"client_synthetic_guard": 0}).client_synthetic_guard is False
    )


def test_template_and_instruction_override_and_blank_fallback():
    custom_tpl = "Use tool {tool_name} now."
    custom_inst = "Read turns carefully."
    cfg = load_atg_config(
        {
            "control_template": custom_tpl,
            "input_context_instruction": custom_inst,
        }
    )
    assert cfg.control_template == custom_tpl
    assert cfg.input_context_instruction == custom_inst

    for blank in ("", "   ", None, 123):
        fallback_cfg = load_atg_config(
            {
                "control_template": blank,
                "input_context_instruction": blank,
            }
        )
        assert fallback_cfg.control_template == DEFAULT_CONTROL_TEMPLATE
        assert (
            fallback_cfg.input_context_instruction == DEFAULT_INPUT_CONTEXT_INSTRUCTION
        )


def test_resolve_atg_mode_with_atg_config_and_to_dict():
    custom_tpl = "Call {tool_name} exclusively."
    custom_inst = "Custom input context instruction."
    cfg = load_atg_config(
        {
            "mode": "prefer",
            "wrap_input": True,
            "wrap_output": False,
            "control_template": custom_tpl,
            "input_context_instruction": custom_inst,
            "mode_by_format": {
                "gemini": "off",
                "openai": "inherit",
                "claude": "required",
                "responses": "bogus",
            },
        }
    )
    assert resolve_atg_mode(cfg, "gemini") == "off"
    assert resolve_atg_mode(cfg, "openai") == "prefer"
    assert resolve_atg_mode(cfg, "claude") == "required"
    assert resolve_atg_mode(cfg, "responses") == "prefer"
    exported = cfg.to_dict()
    assert exported["wrap_input"] is True
    assert exported["wrap_output"] is False
    assert exported["control_template"] == custom_tpl
    assert exported["input_context_instruction"] == custom_inst
    assert exported["mode"] == "prefer"
    reloaded = load_atg_config(exported)
    assert reloaded.wrap_input is True
    assert reloaded.wrap_output is False
    assert reloaded.control_template == custom_tpl
    assert reloaded.input_context_instruction == custom_inst


def test_validate_config_mapping_invalid_wrap_input_falls_back_to_false():
    from aistudio_api.infrastructure.gateway.model_defaults import (
        validate_config_mapping,
    )

    normalized = validate_config_mapping(
        {
            "features": {
                "atg": {
                    "mode": "prefer",
                    "wrap_input": "not-a-bool",
                    "wrap_output": "not-a-bool",
                    "control_template": "   ",
                    "input_context_instruction": "",
                }
            }
        }
    )
    features = cast("dict[str, Any]", normalized["features"])
    assert features["atg"]["wrap_input"] is False
    assert features["atg"]["wrap_output"] is True
    assert features["atg"]["control_template"] == DEFAULT_CONTROL_TEMPLATE
    assert (
        features["atg"]["input_context_instruction"]
        == DEFAULT_INPUT_CONTEXT_INSTRUCTION
    )


# ========== Hot reload from config.yaml ==========


def test_load_atg_config_reads_yaml_and_hot_reloads(tmp_path, monkeypatch):
    target = tmp_path / "hot_atg.yaml"
    target.write_text(
        "api_keys: []\nfeatures:\n  atg:\n    mode: prefer\n    max_response_bytes: 4096\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(target))
    invalidate_config_cache()
    try:
        cfg = load_atg_config()
        assert cfg.mode == "prefer"
        assert cfg.enabled is True
        assert cfg.max_response_bytes == 4096

        target.write_text(
            "api_keys: []\nfeatures:\n  atg:\n    mode: required\n",
            encoding="utf-8",
        )
        invalidate_config_cache()
        reloaded = load_atg_config()
        assert reloaded.mode == "required"
        assert reloaded.max_response_bytes == 16 * MB
    finally:
        invalidate_config_cache()


# ========== Model filter ==========


def test_model_filter_defaults():
    f = create_model_filter("", "")
    assert f.is_text_model("gemini-3.7-flash") is True
    assert f.is_text_model("gpt-4o") is True
    assert f.is_text_model("claude-3-opus") is True
    assert f.is_text_model("some-unknown-model") is True
    assert f.is_text_model("imagen-3.0-generate") is False
    assert f.is_text_model("text-embedding-004") is False
    assert f.is_text_model("") is False
    assert f.is_text_model("   ") is False


def test_model_filter_custom_patterns_and_invalid_fallback():
    f = create_model_filter(r"^my-model", "")
    assert f.is_text_model("my-model-x") is True
    # custom text pattern is case-sensitive (matches JS new RegExp without flags)
    assert f.is_text_model("MY-MODEL-x") is True  # still text via permissive default

    g = create_model_filter("", r"^my-special-image")
    assert g.is_text_model("my-special-image-1") is False
    assert g.is_text_model("gemini-3.7-flash") is True

    # invalid regex falls back to the built-in default
    h = create_model_filter("(", "")
    assert h.is_text_model("gemini-3.7-flash") is True


def test_config_exposes_custom_patterns_and_filter():
    cfg = load_atg_config(
        {"text_model_pattern": r"^custom-", "non_text_model_pattern": ""}
    )
    assert cfg.text_model_pattern == r"^custom-"
    assert cfg.model_filter.is_text_model("custom-llm") is True
