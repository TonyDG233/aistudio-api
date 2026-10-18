"""Tests for ATG config loading/clamping and the model filter."""

from __future__ import annotations

import os
from typing import Any, cast

from aistudio_api.application.atg.config import AtgConfig, load_atg_config
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
    assert cfg.empty_retries == 2
    assert cfg.recovery == "repair"
    assert cfg.side_buffer_bytes == 0
    assert cfg.client_synthetic_guard is True
    assert cfg.max_response_bytes == 16 * MB


def test_load_atg_config_feature_defaults_expose_max_response_bytes(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("api_keys: []\n", encoding="utf-8")
    atg = _features(config_path)["atg"]
    assert atg["max_response_bytes"] == 16 * MB


def test_enabled_derived_from_mode():
    assert load_atg_config({"mode": "prefer"}).enabled is True
    assert load_atg_config({"mode": "required"}).enabled is True
    assert load_atg_config({"mode": "off"}).enabled is False


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
    assert (
        load_atg_config({"client_synthetic_guard": "off"}).client_synthetic_guard
        is False
    )
    assert (
        load_atg_config({"client_synthetic_guard": 0}).client_synthetic_guard is False
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
