"""Apply/restore the WP5 v3 real-machine feature matrix.

Usage:
    uv run python wp5v3_set_config.py apply
    uv run python wp5v3_set_config.py restore
    uv run python wp5v3_set_config.py show

Only the git-ignored root ``config.yaml`` is touched. The previous file is
backed up under ``%TEMP%/wp5v3-config-before.yaml``.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(r"D:\LLM\self_programming\aistudio-api")
CONFIG = ROOT / "config.yaml"
BACKUP = Path(os.environ.get("TEMP", ".")) / "wp5v3-config-before.yaml"

V3_FEATURES: dict[str, object] = {
    "build_surface": True,
    "route_by_tier": False,
    "account_refresh_concurrency": 3,
    "auto_switch": False,
    "auto_switch_interval_hours": 3.0,
    "keepalive_auth_update": True,
    "keepalive_auth_update_hours": 24.0,
    "keepalive_metadata_refresh": True,
    "keepalive_metadata_refresh_hours": 24.0,
    "keepalive_metadata_refresh_on_startup": False,
    "keepalive_active_activity": False,
    "keepalive_active_activity_seconds": 60.0,
    "channels": {
        "chat": {"enabled": True, "max_pages": 1, "quota_pool": "pro"},
        "build": {"enabled": True, "max_pages": 1, "quota_pool": "free"},
        "official": {"enabled": False, "max_pages": 0, "quota_pool": None},
    },
    "routing": {
        "max_switches": 2,
        "global_max_pages": 2,
        "max_contexts": 2,
        "borrow": False,
        "prewarm": False,
        "circuit_breaker": {
            "window_seconds": 600,
            "failure_threshold": 5,
            "cooldown_seconds": 300,
        },
    },
    "slots": [
        {"account_id": "acc_46223636", "channel": "chat", "enabled": True},
        {"account_id": "acc_b1a36ccf", "channel": "build", "enabled": True},
    ],
    "groups": [
        {
            "name": "pro",
            "enabled": True,
            "weight": 1.0,
            "match_tier": ["pro", "ultra", "plus"],
            "allowed_surfaces": ["chat"],
            "allowed_quota_pools": ["pro"],
            "max_pages": 1,
        },
        {
            "name": "free",
            "enabled": True,
            "weight": 1.0,
            "match_tier": ["free"],
            "allowed_surfaces": ["build"],
            "allowed_quota_pools": ["free"],
            "max_pages": 1,
        },
    ],
}


def _switch_features() -> dict[str, object]:
    features = copy.deepcopy(V3_FEATURES)
    channels = features["channels"]
    assert isinstance(channels, dict)
    # Free accounts serve chat so rotation can exercise three free accounts;
    # the pro group is intentionally ineligible for this phase.
    channels["chat"]["quota_pool"] = "free"
    channels["build"]["quota_pool"] = "pro"
    features["groups"] = [
        {
            "name": "pro",
            "enabled": True,
            "weight": 1.0,
            "match_tier": ["pro", "ultra", "plus"],
            "allowed_surfaces": ["chat"],
            "allowed_quota_pools": ["pro"],
            "max_pages": 2,
        },
        {
            "name": "free",
            "enabled": True,
            "weight": 1.0,
            "match_tier": ["free"],
            "allowed_surfaces": ["chat", "build"],
            "allowed_quota_pools": ["free"],
            "max_pages": 2,
        },
    ]
    features["slots"] = []
    return features


def _force429_features() -> dict[str, object]:
    features = copy.deepcopy(V3_FEATURES)
    channels = features["channels"]
    assert isinstance(channels, dict)
    channels["chat"]["quota_pool"] = None
    channels["build"]["quota_pool"] = None
    features["groups"] = [
        {
            "name": "all",
            "enabled": True,
            "weight": 1.0,
            "match_tier": ["free", "pro", "ultra", "plus"],
            "allowed_surfaces": ["chat", "build"],
            "allowed_quota_pools": [],
            "max_pages": 2,
        }
    ]
    features["slots"] = []
    return features


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "show"
    if action in ("apply", "switch", "force429"):
        if not BACKUP.exists():
            BACKUP.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        data = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
        features = data.setdefault("features", {})
        features.clear()
        if action == "switch":
            selected = _switch_features()
        elif action == "force429":
            selected = _force429_features()
        else:
            selected = V3_FEATURES
        features.update(selected)
        CONFIG.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    elif action == "restore":
        if not BACKUP.exists():
            raise SystemExit(f"backup not found: {BACKUP}")
        CONFIG.write_text(BACKUP.read_text(encoding="utf-8"), encoding="utf-8")
    elif action != "show":
        raise SystemExit(f"unknown action: {action}")

    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    features = data.get("features") or {}
    print(json.dumps(features, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()