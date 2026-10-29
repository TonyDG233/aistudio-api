"""P6 keepalive configuration contracts."""

from __future__ import annotations

import logging
from pathlib import Path

from aistudio_api.infrastructure.gateway.model_defaults import (
    get_configured_keepalive_settings,
)


def _config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_keepalive_defaults_are_24h_and_activity_off(tmp_path):
    path = _config(tmp_path, "")

    settings = get_configured_keepalive_settings(path)

    assert settings["auth_update"] is True
    assert settings["auth_update_hours"] == 24.0
    assert settings["metadata_refresh"] is True
    assert settings["metadata_refresh_hours"] == 24.0
    assert settings["metadata_refresh_on_startup"] is True
    assert settings["active_activity"] is False
    assert settings["active_activity_seconds"] == 60.0
    assert "auto_switch" not in settings


def test_keepalive_uses_features_keepalive_block(tmp_path):
    path = _config(
        tmp_path,
        """
features:
  keepalive:
    auth_update: false
    auth_update_hours: 12
    metadata_refresh: false
    metadata_refresh_hours: 6
    metadata_refresh_on_startup: false
    active_activity: true
    active_activity_seconds: 30
""",
    )

    settings = get_configured_keepalive_settings(path)

    assert settings["auth_update"] is False
    assert settings["auth_update_hours"] == 12.0
    assert settings["metadata_refresh"] is False
    assert settings["metadata_refresh_hours"] == 6.0
    assert settings["metadata_refresh_on_startup"] is False
    assert settings["active_activity"] is True
    assert settings["active_activity_seconds"] == 30.0


def test_legacy_keepalive_keys_are_ignored_with_warning(tmp_path, caplog):
    path = _config(
        tmp_path,
        """
features:
  auto_switch: true
  auto_switch_interval_hours: 3
  keepalive_auth_update: false
  keepalive_auth_update_hours: 12
  keepalive_metadata_refresh: false
  keepalive_active_activity: true
""",
    )

    with caplog.at_level(logging.WARNING, logger="aistudio.config"):
        settings = get_configured_keepalive_settings(path)

    assert settings["auth_update"] is True
    assert settings["auth_update_hours"] == 24.0
    assert settings["metadata_refresh"] is True
    assert settings["active_activity"] is False
    assert "auto_switch" not in settings
    messages = [record.message for record in caplog.records]
    assert any("旧版 keepalive 配置键" in message for message in messages)


def test_invalid_keepalive_block_falls_back_to_defaults(tmp_path, caplog):
    path = _config(tmp_path, "features:\n  keepalive: not-a-mapping\n")

    with caplog.at_level(logging.WARNING, logger="aistudio.config"):
        settings = get_configured_keepalive_settings(path)

    assert settings["auth_update"] is True
    assert settings["metadata_refresh_hours"] == 24.0
    assert settings["active_activity"] is False
    messages = [record.message for record in caplog.records]
    assert any("features.keepalive 必须为字典" in message for message in messages)


def test_invalid_keepalive_values_fall_back_per_field(tmp_path):
    path = _config(
        tmp_path,
        """
features:
  keepalive:
    auth_update: "yes"
    auth_update_hours: 0
    metadata_refresh: true
    metadata_refresh_hours: -1
    active_activity: null
""",
    )

    settings = get_configured_keepalive_settings(path)

    assert settings["auth_update"] is True
    assert settings["auth_update_hours"] == 24.0
    assert settings["metadata_refresh"] is True
    assert settings["metadata_refresh_hours"] == 24.0
    assert settings["active_activity"] is False
