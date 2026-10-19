"""Tests for the WP1 feature-toggle framework (env baseline + config.yaml features)."""

from __future__ import annotations

import os
from typing import Any, cast

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.config import (
    _load_bool_env,
    _load_choice_env,
    _load_float_env,
    _load_int_env,
    settings,
)
from aistudio_api.infrastructure.gateway.model_defaults import (
    get_features_config,
    invalidate_config_cache,
    resolve_model_defaults,
)


def _features(config_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    return cast("dict[str, Any]", get_features_config(config_path))


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


# ========== Settings env helpers: defaults / overrides ==========


def test_env_helpers_fall_back_to_defaults_when_unset():
    assert _load_bool_env("AISTUDIO_TEST_UNSET_FLAG", default=False) is False
    assert _load_bool_env("AISTUDIO_TEST_UNSET_FLAG", default=True) is True
    assert _load_int_env("AISTUDIO_TEST_UNSET_INT", default=3) == 3
    assert _load_float_env("AISTUDIO_TEST_UNSET_FLOAT", default=6.0) == 6.0
    assert (
        _load_choice_env(
            "AISTUDIO_TEST_UNSET_CHOICE", default="off", choices=("off", "prefer")
        )
        == "off"
    )


def test_env_helpers_parse_overrides(monkeypatch):
    monkeypatch.setenv("AISTUDIO_TEST_FLAG", "1")
    monkeypatch.setenv("AISTUDIO_TEST_INT", "5")
    monkeypatch.setenv("AISTUDIO_TEST_FLOAT", "1.5")
    monkeypatch.setenv("AISTUDIO_TEST_CHOICE", "REQUIRED")
    assert _load_bool_env("AISTUDIO_TEST_FLAG", default=False) is True
    assert _load_int_env("AISTUDIO_TEST_INT", default=3) == 5
    assert _load_float_env("AISTUDIO_TEST_FLOAT", default=3.0) == 1.5
    assert (
        _load_choice_env(
            "AISTUDIO_TEST_CHOICE", default="off", choices=("off", "prefer", "required")
        )
        == "required"
    )


def test_env_helpers_are_lenient_on_bad_input(monkeypatch):
    monkeypatch.setenv("AISTUDIO_TEST_BAD_INT", "not-a-number")
    monkeypatch.setenv("AISTUDIO_TEST_BAD_FLOAT", "nan-ish")
    monkeypatch.setenv("AISTUDIO_TEST_BAD_CHOICE", "bogus")
    assert _load_int_env("AISTUDIO_TEST_BAD_INT", default=3) == 3
    assert _load_float_env("AISTUDIO_TEST_BAD_FLOAT", default=3.0) == 3.0
    assert (
        _load_choice_env(
            "AISTUDIO_TEST_BAD_CHOICE", default="off", choices=("off", "prefer")
        )
        == "off"
    )


def test_env_float_helper_clamps_to_minimum(monkeypatch):
    monkeypatch.setenv("AISTUDIO_TEST_SMALL_FLOAT", "0.01")
    assert _load_float_env("AISTUDIO_TEST_SMALL_FLOAT", default=3.0, minimum=0.1) == 0.1


def test_settings_exposes_feature_defaults():
    assert settings.build_surface is False
    assert settings.atg_mode == "off"
    assert settings.account_refresh_concurrency == 3
    assert settings.route_by_tier is False
    assert settings.auto_switch is False
    assert settings.auto_switch_interval_hours == 3.0


# ========== get_features_config: defaults / parsing / normalization ==========


def test_features_config_defaults_without_section(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("api_keys: []\n", encoding="utf-8")
    features = _features(config_path)
    assert features["build_surface"] is False
    assert features["route_by_tier"] is False
    assert features["account_refresh_concurrency"] == 3
    assert features["auto_switch"] is False
    assert features["auto_switch_interval_hours"] == 3.0
    assert features["formats"]["openai"] is True
    assert features["formats"]["claude"] is True
    assert features["formats"]["media"]["allow_private_urls"] is False
    assert features["atg"]["mode"] == "off"
    assert features["atg"]["mode_by_format"] == {
        "gemini": "inherit",
        "openai": "inherit",
        "claude": "inherit",
    }
    assert features["atg"]["tool_prefix"] == "atg_emit_"
    assert features["atg"]["control_position"] == "tail"
    assert features["atg"]["strict"] is False
    assert features["atg"]["empty_retries"] == 2
    assert features["atg"]["recovery"] == "repair"
    assert features["atg"]["side_buffer_bytes"] == 0
    assert features["atg"]["client_synthetic_guard"] is True
    assert features["atg"]["text_model_pattern"] == ""
    assert features["atg"]["non_text_model_pattern"] == ""
    assert features["fallback"] == {
        "on_quota": False,
        "on_failure": False,
        "cross_surface": False,
    }


def test_features_config_reads_and_merges_yaml(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
features:
  build_surface: true
  account_refresh_concurrency: 8
  auto_switch: true
  auto_switch_interval_hours: 1.5
  atg:
    mode: prefer
    strict: true
    empty_retries: 4
    side_buffer_bytes: 4096
    client_synthetic_guard: false
    text_model_pattern: "^(gemini|gpt)"
  fallback:
    on_failure: true
""",
        encoding="utf-8",
    )
    features = _features(config_path)
    assert features["build_surface"] is True
    assert features["account_refresh_concurrency"] == 8
    assert features["auto_switch"] is True
    assert features["auto_switch_interval_hours"] == 1.5
    assert features["atg"]["mode"] == "prefer"
    assert features["atg"]["strict"] is True
    assert features["atg"]["empty_retries"] == 4
    assert features["atg"]["side_buffer_bytes"] == 4096
    assert features["atg"]["client_synthetic_guard"] is False
    assert features["atg"]["text_model_pattern"] == "^(gemini|gpt)"
    # 未在 yaml 中出现的字段仍保留内置默认
    assert features["atg"]["tool_prefix"] == "atg_emit_"
    assert features["atg"]["recovery"] == "repair"
    assert features["fallback"]["on_failure"] is True
    assert features["fallback"]["on_quota"] is False


def test_features_config_normalizes_invalid_values(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
features:
  account_refresh_concurrency: 999
  auto_switch_interval_hours: 0
  atg:
    mode: bogus
    control_position: middle
    recovery: explode
    empty_retries: 99
    side_buffer_bytes: -5
  fallback:
    on_quota: "yes"
""",
        encoding="utf-8",
    )
    features = _features(config_path)
    assert features["account_refresh_concurrency"] == 16
    assert features["auto_switch_interval_hours"] == 0.1
    assert features["atg"]["mode"] == "off"
    assert features["atg"]["control_position"] == "tail"
    assert features["atg"]["recovery"] == "repair"
    assert features["atg"]["empty_retries"] == 5
    assert features["atg"]["side_buffer_bytes"] == 0
    assert features["fallback"]["on_quota"] is True


# ========== Existing YAML consumers stay intact with a features key ==========


def test_existing_loader_unaffected_by_features_key(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
features:
  build_surface: true
  atg:
    mode: required
model_defaults:
  profiles:
    - name: gemini_models
      match:
        prefixes: [gemini-]
      default_tools: [google_search]
      safety_settings:
        Harassment: 5
        Hate: 5
        Sexually Explicit: 5
        Dangerous Content: 5
""",
        encoding="utf-8",
    )
    defaults = resolve_model_defaults(
        "models/gemini-2.5-flash", config_path=config_path
    )
    assert defaults.default_tools == ("google_search",)
    assert defaults.safety_settings == (
        (None, None, 7, 5),
        (None, None, 8, 5),
        (None, None, 9, 5),
        (None, None, 10, 5),
    )
    # 同一份含 features 的 YAML 也能被 get_features_config 正确读取
    assert _features(config_path)["atg"]["mode"] == "required"


# ========== Routes: /config visibility + hot reload ==========


@pytest.mark.asyncio
async def test_get_config_includes_features():
    async with _client() as client:
        resp = await client.get("/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "features" in data
        features = data["features"]
        assert features["atg"]["mode"] == "off"
        assert features["build_surface"] is False
        assert features["fallback"]["cross_surface"] is False


@pytest.mark.asyncio
async def test_put_config_yaml_hot_reloads_features(tmp_path, monkeypatch):
    target_config = tmp_path / "hot_reload_config.yaml"
    target_config.write_text("api_keys: []\n", encoding="utf-8")
    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(target_config))
    invalidate_config_cache()

    async with _client() as client:
        before = await client.get("/config")
        assert before.status_code == 200
        assert before.json()["features"]["atg"]["mode"] == "off"

        update = await client.put(
            "/config/yaml",
            json={
                "yaml_content": (
                    "api_keys: []\n"
                    "features:\n"
                    "  build_surface: true\n"
                    "  atg:\n"
                    "    mode: prefer\n"
                    "    empty_retries: 3\n"
                    "  fallback:\n"
                    "    cross_surface: true\n"
                )
            },
        )
        assert update.status_code == 200

        after = await client.get("/config")
        assert after.status_code == 200
        features = after.json()["features"]
        assert features["build_surface"] is True
        assert features["atg"]["mode"] == "prefer"
        assert features["atg"]["empty_retries"] == 3
        assert features["fallback"]["cross_surface"] is True

    invalidate_config_cache()
