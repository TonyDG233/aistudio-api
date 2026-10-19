"""Behavior contracts for WP5 phase-2 features: channels/routing/slots/groups."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

from aistudio_api.infrastructure.gateway.model_defaults import (
    get_features_config,
    invalidate_config_cache,
)


def _features(config_path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", get_features_config(config_path))


def _write(config_path: Path, body: str) -> None:
    config_path.write_text(body, encoding="utf-8")
    invalidate_config_cache()


def test_channels_routing_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "defaults.yaml"
    _write(config_path, "api_keys: []\n")
    features = _features(config_path)

    assert features["channels"] == {
        "chat": {
            "enabled": True,
            "max_instances": 2,
            "max_pages": 1,
            "quota_pool": None,
        },
        "build": {
            "enabled": False,
            "max_instances": 2,
            "max_pages": 1,
            "quota_pool": None,
        },
        "official": {
            "enabled": False,
            "max_instances": 0,
            "max_pages": 0,
            "quota_pool": None,
        },
    }
    assert features["routing"] == {
        "max_switches": 2,
        "global_max_pages": 2,
        "max_contexts": 2,
        "global_max_instances": 2,
        "max_realms": 2,
        "realm_mode": True,
        "borrow": False,
        "prewarm": False,
        "circuit_breaker": {
            "window_seconds": 600,
            "failure_threshold": 5,
            "cooldown_seconds": 300,
        },
    }
    assert features["slots"] == []
    assert features["build_surface"] is False


def test_build_surface_is_legacy_alias_for_channels_build(tmp_path: Path) -> None:
    config_path = tmp_path / "legacy.yaml"
    _write(config_path, "features:\n  build_surface: true\n")
    features = _features(config_path)
    assert features["channels"]["build"]["enabled"] is True
    assert features["build_surface"] is True


def test_explicit_channels_build_overrides_legacy_alias(tmp_path: Path) -> None:
    config_path = tmp_path / "explicit.yaml"
    _write(
        config_path,
        """
features:
  build_surface: true
  channels:
    build:
      enabled: false
      max_instances: 4
""",
    )
    features = _features(config_path)
    assert features["channels"]["build"] == {
        "enabled": False,
        "max_instances": 4,
        "max_pages": 4,
        "quota_pool": None,
    }
    assert features["build_surface"] is False


def test_channels_aliases_and_bad_entries(tmp_path: Path) -> None:
    config_path = tmp_path / "aliases.yaml"
    _write(
        config_path,
        """
features:
  channels:
    pg: {enabled: false, max_instances: 5}
    official: {enabled: true, max_instances: 99}
    bogus: {enabled: true}
""",
    )
    channels = _features(config_path)["channels"]
    assert channels["chat"] == {
        "enabled": False,
        "max_instances": 5,
        "max_pages": 5,
        "quota_pool": None,
    }
    assert channels["official"] == {
        "enabled": False,
        "max_instances": 0,
        "max_pages": 0,
        "quota_pool": None,
    }
    assert "bogus" not in channels


def test_routing_values_are_clamped(tmp_path: Path) -> None:
    config_path = tmp_path / "routing.yaml"
    _write(
        config_path,
        """
features:
  routing:
    max_switches: 99
    global_max_pages: 0
    max_contexts: 99
    realm_mode: false
    borrow: true
    prewarm: yes
    circuit_breaker:
      window_seconds: 1
      failure_threshold: 0
      cooldown_seconds: 999999
""",
    )
    routing = _features(config_path)["routing"]
    assert routing["max_switches"] == 10
    assert routing["global_max_pages"] == 1
    assert routing["max_contexts"] == 16
    assert routing["global_max_instances"] == 1
    assert routing["max_realms"] == 16
    assert routing["realm_mode"] is False
    assert routing["borrow"] is True
    assert routing["prewarm"] is True
    assert routing["circuit_breaker"] == {
        "window_seconds": 60,
        "failure_threshold": 1,
        "cooldown_seconds": 3600,
    }


def test_slots_are_normalized_and_deduped(tmp_path: Path) -> None:
    config_path = tmp_path / "slots.yaml"
    _write(
        config_path,
        """
features:
  slots:
    - {account_id: acc_a, channel: pg, enabled: true, note: pro-chat}
    - {account_id: acc_a, channel: chat}
    - {account_id: acc_b, channel: build, enabled: false}
    - {account_id: "", channel: chat}
    - {account_id: acc_c, channel: bogus}
    - "bad"
""",
    )
    slots = _features(config_path)["slots"]
    assert slots == [
        {
            "account_id": "acc_a",
            "channel": "chat",
            "enabled": True,
            "note": "pro-chat",
        },
        {"account_id": "acc_b", "channel": "build", "enabled": False, "note": ""},
    ]


def test_group_enabled_and_weight_are_normalized(tmp_path: Path) -> None:
    config_path = tmp_path / "groups.yaml"
    _write(
        config_path,
        """
features:
  groups:
    - {name: disabled, enabled: false, weight: 5}
    - {name: low, weight: 0}
    - {name: high, weight: 999}
    - {name: bad, weight: nope}
""",
    )
    groups = _features(config_path)["groups"]
    assert [g["enabled"] for g in groups] == [False, True, True, True]
    assert [g["weight"] for g in groups] == [5.0, 0.1, 100.0, 1.0]


def test_routing_config_hot_reload(tmp_path: Path) -> None:
    config_path = tmp_path / "hot_routing.yaml"
    _write(
        config_path,
        "features:\n  routing:\n    max_switches: 1\n    realm_mode: false\n",
    )
    assert _features(config_path)["routing"]["max_switches"] == 1
    assert _features(config_path)["routing"]["realm_mode"] is False

    first_mtime = config_path.stat().st_mtime
    config_path.write_text(
        "features:\n  routing:\n    max_switches: 4\n    realm_mode: true\n",
        encoding="utf-8",
    )
    os.utime(config_path, (first_mtime + 30, first_mtime + 30))
    invalidate_config_cache()

    routing = _features(config_path)["routing"]
    assert routing["max_switches"] == 4
    assert routing["realm_mode"] is True
