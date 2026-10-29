"""Tests for config deep merge, startup validation, instance normalization and lifecycle hooks."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import aistudio_api.api.app as app_module
import aistudio_api.infrastructure.gateway.model_defaults as model_defaults
from aistudio_api.application.account_rotator import AccountStats
from aistudio_api.config import resolve_hash_include_code
from aistudio_api.infrastructure.gateway.model_defaults import (
    ConfigValidationError,
    get_configured_instance_specs,
    get_configured_logging_settings,
    get_configured_rotator_settings,
    get_configured_security_settings,
    invalidate_config_cache,
    validate_config,
    validate_config_mapping,
    write_config_text,
)


def _write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_partial_rotator_block_deep_merges_with_defaults(tmp_path, monkeypatch):
    path = _write_config(tmp_path, "rotator:\n  max_retries: 2\n")

    rotator = get_configured_rotator_settings(path)

    assert rotator["max_retries"] == 2
    cooldowns = rotator["cooldowns"]
    assert isinstance(cooldowns, dict)
    assert set(cooldowns) >= {
        "rate_limited_short",
        "rate_limited_drip",
        "rate_limited_drip_extended",
        "auth_error",
    }

    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(path))
    invalidate_config_cache()
    stats = AccountStats(account_id="acc_1")
    stats.record_rate_limited(model="gemini-2.5-pro")
    assert stats.get_cooldown_remaining("gemini-2.5-pro") > 0


def test_hash_include_code_defaults_true_and_env_overrides_yaml(tmp_path, monkeypatch):
    """D-09: diagnostic switch comes from config/env, defaults to true."""
    path = tmp_path / "config.yaml"
    path.write_text("diagnostics:\n  hash_include_code: false\n", encoding="utf-8")
    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(path))
    monkeypatch.delenv("AISTUDIO_HASH_INCLUDE_CODE", raising=False)
    assert resolve_hash_include_code() is False

    monkeypatch.setenv("AISTUDIO_HASH_INCLUDE_CODE", "1")
    assert resolve_hash_include_code() is True


def test_invalid_rotator_type_fails_startup_validation(tmp_path):
    path = _write_config(tmp_path, "rotator:\n  - not-a-mapping\n")

    with pytest.raises(ConfigValidationError, match="rotator 必须为字典"):
        validate_config(path)


def test_security_anonymous_flag_defaults_false_and_accepts_true(tmp_path):
    default_path = _write_config(tmp_path, "logging:\n  level: INFO\n")
    assert get_configured_security_settings(default_path) is False

    enabled_path = tmp_path / "enabled.yaml"
    enabled_path.write_text(
        "security:\n  api_allow_anonymous: true\n", encoding="utf-8"
    )
    assert get_configured_security_settings(enabled_path) is True


def test_security_invalid_boolean_falls_back_to_false(tmp_path):
    path = _write_config(tmp_path, "security:\n  api_allow_anonymous: maybe\n")

    assert get_configured_security_settings(path) is False


def test_non_dict_logging_and_security_blocks_fallback_with_warning(tmp_path, caplog):
    path = _write_config(tmp_path, "logging: []\nsecurity: null\n")

    with caplog.at_level(logging.WARNING):
        normalized = validate_config_mapping({"logging": [], "security": None})
        logging_settings = get_configured_logging_settings(path)
        allow_anonymous = get_configured_security_settings(path)

    assert isinstance(normalized["logging"], dict)
    assert isinstance(normalized["security"], dict)
    assert logging_settings["level"] == "INFO"
    assert allow_anonymous is False
    messages = [record.message for record in caplog.records]
    assert any("logging 必须为字典" in message for message in messages)
    assert any("security 必须为字典" in message for message in messages)


@pytest.mark.parametrize("content", ["logging: []\n", "security: null\n"])
def test_write_config_text_rejects_non_dict_blocks(tmp_path, content):
    path = tmp_path / "config.yaml"

    with pytest.raises(ConfigValidationError, match="必须为字典"):
        write_config_text(content, path)

    assert not path.exists()


def test_instances_are_normalized_deduplicated_and_bad_items_dropped(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 2
    surface: chat
    min_tier: Free
  - count: 1
    surface: CHAT
    min_tier: free
  - count: 1
    surface: build
    min_tier: ultra_20x
  - count: 0
    surface: chat
    min_tier: Pro
  - count: 1
    surface: video
    min_tier: Pro
  - count: 1
    surface: chat
    min_tier: Diamond
  - just-a-string
""",
    )

    assert get_configured_instance_specs(path) == [
        {"count": 3, "surface": "chat", "min_tier": "Free"},
        {"count": 1, "surface": "build", "min_tier": "Ultra_20X"},
    ]


def test_instances_all_invalid_raises_config_validation_error(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 0
    surface: chat
    min_tier: Pro
  - count: 1
    surface: video
    min_tier: Pro
""",
    )

    with pytest.raises(ConfigValidationError, match="未包含任何有效实例"):
        get_configured_instance_specs(path)


def test_instances_must_be_list(tmp_path):
    path = _write_config(tmp_path, "instances: {}\n")

    with pytest.raises(ConfigValidationError, match="instances 必须为列表"):
        validate_config(path)


def test_validate_config_rejects_all_invalid_instances(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 0
    surface: chat
    min_tier: Pro
  - count: 1
    surface: video
    min_tier: Pro
""",
    )

    with pytest.raises(ConfigValidationError, match="未包含任何有效实例"):
        validate_config(path)


@pytest.mark.parametrize(
    ("preset_yaml", "missing_field"),
    [
        ("- action: enable_all", "id"),
        ("- id: preset_missing_action", "action"),
    ],
)
def test_validate_config_requires_preset_id_and_action(
    tmp_path, preset_yaml, missing_field
):
    path = _write_config(tmp_path, f"account_presets:\n  {preset_yaml}\n")

    with pytest.raises(
        ConfigValidationError,
        match=f"第 1 个 preset 缺少非空字符串字段 {missing_field}",
    ):
        validate_config(path)


def test_write_config_text_uses_atomic_replace_and_writes_content(
    tmp_path, monkeypatch
):
    path = tmp_path / "config.yaml"
    real_replace = os.replace
    replace_calls: list[tuple[str, str]] = []

    def tracking_replace(src, dst):
        replace_calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(model_defaults.os, "replace", tracking_replace)

    content = "logging:\n  level: WARNING\n"
    written_path = write_config_text(content, path)

    assert written_path == path
    assert path.read_text(encoding="utf-8") == content
    assert len(replace_calls) == 1
    assert Path(replace_calls[0][0]).parent == tmp_path
    assert replace_calls[0][1] == str(path)
    assert not list(tmp_path.glob("*.tmp"))


def test_write_config_text_rejects_invalid_config_without_writing(tmp_path):
    path = tmp_path / "config.yaml"

    with pytest.raises(ConfigValidationError):
        write_config_text("rotator: []\n", path)

    assert not path.exists()


class _RecordingComponent:
    def __init__(self, name: str, method: str, events: list[str]) -> None:
        self._name = name
        self._method = method
        self._events = events

    def _record(self) -> None:
        self._events.append(f"{self._name}.{self._method}")


class _SyncComponent(_RecordingComponent):
    def __getattr__(self, item: str):
        if item == self._method:
            return self._record
        raise AttributeError(item)


class _AsyncComponent(_RecordingComponent):
    def __getattr__(self, item: str):
        if item != self._method:
            raise AttributeError(item)

        async def record() -> None:
            self._record()

        return record


def test_shutdown_runtime_order_and_state_cleanup():
    events: list[str] = []
    keepalive = _AsyncComponent("keepalive", "stop", events)
    instance_manager = _SyncComponent("instance_manager", "close", events)
    client = _AsyncComponent("client", "close", events)
    state = SimpleNamespace(
        keepalive=keepalive,
        instance_manager=instance_manager,
        client=client,
        account_service=object(),
        rotator=object(),
    )

    asyncio.run(app_module._shutdown_runtime(state, ()))  # type: ignore[arg-type]

    assert events == ["keepalive.stop", "instance_manager.close", "client.close"]
    assert state.keepalive is None
    assert state.instance_manager is None
    assert state.client is None
    assert state.account_service is None
    assert state.rotator is None


def test_shutdown_runtime_cancels_background_tasks_first():
    events: list[str] = []
    keepalive = _SyncComponent("keepalive", "stop", events)

    async def scenario() -> None:
        task = asyncio.create_task(asyncio.sleep(30))
        state = SimpleNamespace(
            keepalive=keepalive,
            instance_manager=None,
            client=None,
            account_service=None,
            rotator=None,
        )
        await app_module._shutdown_runtime(state, (task,))  # type: ignore[arg-type]
        await asyncio.sleep(0)
        assert task.cancelled()

    asyncio.run(scenario())
    assert events == ["keepalive.stop"]
