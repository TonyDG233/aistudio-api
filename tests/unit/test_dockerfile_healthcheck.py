"""Static guard: every container healthcheck must hit the auth-free /health route."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]

_SOURCES = ("Dockerfile", "docker-compose.yml", "docker-compose.deploy.yml")
_FORBIDDEN_PROBES = ("/v1beta/models", "/v1/models")


def _dockerfile_healthcheck_block(text: str) -> str:
    start = text.index("HEALTHCHECK")
    # The HEALTHCHECK instruction ends at the next blank line / comment.
    block = text[start:]
    return block.split("\n\n", 1)[0]


def _compose_healthcheck_blocks(text: str) -> list[str]:
    data = yaml.safe_load(text) or {}
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        return []
    blocks: list[str] = []
    for service in services.values():
        if not isinstance(service, dict):
            continue
        healthcheck = service.get("healthcheck")
        if isinstance(healthcheck, dict):
            blocks.append(json.dumps(healthcheck, ensure_ascii=False))
    return blocks


def _healthcheck_blocks() -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for name in _SOURCES:
        path = _REPO_ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if name == "Dockerfile":
            if "HEALTHCHECK" in text:
                blocks.append((name, _dockerfile_healthcheck_block(text)))
            continue
        blocks.extend(
            (name, block) for block in _compose_healthcheck_blocks(text)
        )
    return blocks


def test_all_healthchecks_target_auth_free_health_route():
    blocks = _healthcheck_blocks()
    assert blocks, "expected at least one healthcheck definition"

    for name, block in blocks:
        assert "/health" in block, f"{name} healthcheck must probe /health: {block}"
        for probe in _FORBIDDEN_PROBES:
            assert probe not in block, (
                f"{name} healthcheck must not probe {probe}: {block}"
            )


def test_dockerfile_healthcheck_keeps_interval():
    block = _dockerfile_healthcheck_block(
        (_REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    )
    assert "curl -fsS http://localhost:8080/health" in block
    assert "--interval=30s" in block


def test_compose_healthchecks_keep_interval():
    for name in ("docker-compose.yml", "docker-compose.deploy.yml"):
        path = _REPO_ROOT / name
        if not path.exists():
            continue
        for block in _compose_healthcheck_blocks(
            path.read_text(encoding="utf-8")
        ):
            assert '"interval": "30s"' in block, f"{name} healthcheck: {block}"
