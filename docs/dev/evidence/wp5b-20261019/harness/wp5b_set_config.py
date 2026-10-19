"""Patch runtime config.yaml features for WP5b real-machine stages.

Usage:
    uv run python wp5b_set_config.py payload.json
    uv run python wp5b_set_config.py restore

The payload is {"features": {...}} merged into the current features mapping.
Only the git-ignored root config.yaml is touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

CONFIG = Path(r"D:\LLM\self_programming\aistudio-api\config.yaml")

BASE = {
    "build_surface": False,
    "route_by_tier": False,
    "channels": {
        "chat": {"enabled": True, "max_instances": 2},
        "build": {"enabled": False, "max_instances": 2},
        "official": {"enabled": False, "max_instances": 0},
    },
    "routing": {
        "max_switches": 2,
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
    },
    "slots": [],
    "groups": [],
}


def main() -> None:
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    features = data.setdefault("features", {})
    if sys.argv[1] == "restore":
        features.update(BASE)
    else:
        payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        features.update(payload.get("features") or {})
    CONFIG.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print("features:", json.dumps(features, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()