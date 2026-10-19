"""Keepalive feature configuration contracts."""

from __future__ import annotations

import pytest
import yaml

from aistudio_api.infrastructure.gateway.model_defaults import (
    get_features_config,
    update_configured_features,
)


def test_update_features_roundtrip_and_normalization(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("features:\n  auto_switch: false\n", encoding="utf-8")

    updated = update_configured_features(
        {
            "keepalive_auth_update": False,
            "keepalive_auth_update_hours": 12,
            "keepalive_metadata_refresh_hours": 0,
        },
        config_path=config_path,
    )

    assert updated["keepalive_auth_update"] is False
    assert updated["keepalive_auth_update_hours"] == 12.0
    assert updated["keepalive_metadata_refresh_hours"] == 0.1
    assert get_features_config(config_path)["keepalive_auth_update"] is False
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["features"]["keepalive_auth_update"] is False


def test_update_features_rejects_unknown_keys(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("features: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unknown feature keys"):
        update_configured_features({"totally_unknown": True}, config_path=config_path)
