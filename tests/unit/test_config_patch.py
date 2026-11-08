"""PATCH /config section merge, D-09 fidelity and ui_debug isolation tests."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import httpx
import pytest
import yaml

import aistudio_api.api.app as app_module
import aistudio_api.api.routes_system as routes_system
import aistudio_api.infrastructure.gateway.model_defaults as model_defaults
from aistudio_api.api.app import app

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULTS_PATH = _REPO_ROOT / "defaults" / "config.yaml"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _read_yaml(path: Path) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert isinstance(loaded, dict)
    return cast("dict[str, object]", loaded)


def _as_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


@pytest.fixture
def sandbox_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "config.yaml"
    shutil.copyfile(_DEFAULTS_PATH, path)
    monkeypatch.setattr(routes_system, "_resolve_config_path", lambda _p: path)
    monkeypatch.setattr(model_defaults, "_resolve_config_path", lambda _p: path)
    for name in (
        "set_log_level",
        "set_dump_requests",
        "set_dump_to_file",
        "set_dump_dir",
    ):
        monkeypatch.setattr(routes_system, name, lambda *args, **kwargs: None)
    model_defaults.invalidate_config_cache()
    yield path
    model_defaults.invalidate_config_cache()


@pytest.mark.asyncio
async def test_patch_config_merges_section_and_preserves_unknown_keys(
    sandbox_config: Path,
):
    raw = _read_yaml(sandbox_config)
    raw["custom_block"] = {"keep": True, "nested": {"flag": 1}}
    diagnostics = _as_dict(raw["diagnostics"])
    diagnostics["hash_include_code"] = False
    diagnostics["custom_probe"] = "keep-me"
    sandbox_config.write_text(
        yaml.dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    async with _client() as client:
        resp = await client.patch(
            "/config",
            json={
                "updates": {
                    "security": {"api_allow_anonymous": True},
                    "browser": {"fingerprint_spoof_enabled": False},
                }
            },
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert isinstance(payload["yaml_content"], str)

    on_disk = _read_yaml(sandbox_config)
    assert _as_dict(on_disk["security"])["api_allow_anonymous"] is True
    assert _as_dict(on_disk["browser"])["fingerprint_spoof_enabled"] is False
    assert on_disk["custom_block"] == {"keep": True, "nested": {"flag": 1}}
    assert _as_dict(on_disk["diagnostics"])["hash_include_code"] is False
    assert _as_dict(on_disk["diagnostics"])["custom_probe"] == "keep-me"
    # Untouched sections stay intact.
    assert on_disk["instances"] == raw["instances"]
    assert on_disk["model_defaults"] == raw["model_defaults"]


@pytest.mark.asyncio
async def test_patch_config_logging_only_keeps_d09_switch(sandbox_config: Path):
    raw = _read_yaml(sandbox_config)
    diagnostics = _as_dict(raw["diagnostics"])
    diagnostics["hash_include_code"] = False
    sandbox_config.write_text(
        yaml.dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    async with _client() as client:
        resp = await client.patch(
            "/config",
            json={
                "updates": {
                    "logging": {"level": "DEBUG", "dump_requests": True},
                    "diagnostics": {"ui_debug": True},
                }
            },
        )

    assert resp.status_code == 200
    on_disk = _read_yaml(sandbox_config)
    logging_block = _as_dict(on_disk["logging"])
    assert logging_block["level"] == "DEBUG"
    assert logging_block["dump_requests"] is True
    # Unsubmitted logging keys and the D-09 diagnostic switch survive.
    assert logging_block["dump_to_file"] is False
    assert logging_block["dump_dir"] == "dumps"
    assert _as_dict(on_disk["diagnostics"])["hash_include_code"] is False
    assert _as_dict(on_disk["diagnostics"])["ui_debug"] is True

    response_yaml = yaml.safe_load(resp.json()["yaml_content"]) or {}
    assert _as_dict(response_yaml["diagnostics"])["hash_include_code"] is False


@pytest.mark.asyncio
async def test_patch_config_replaces_lists(sandbox_config: Path):
    before = _read_yaml(sandbox_config)
    assert len(before["instances"]) == 2  # type: ignore[arg-type]

    async with _client() as client:
        resp = await client.patch(
            "/config",
            json={
                "updates": {
                    "instances": [
                        {"count": 3, "surface": "chat", "only_tier": "Free"}
                    ]
                }
            },
        )

    assert resp.status_code == 200
    on_disk = _read_yaml(sandbox_config)
    assert on_disk["instances"] == [
        {"count": 3, "surface": "chat", "only_tier": "Free"}
    ]
    assert on_disk["max_contexts"] == before["max_contexts"]


@pytest.mark.asyncio
async def test_patch_config_nested_features_and_model_sections_merge(
    sandbox_config: Path,
):
    before = _read_yaml(sandbox_config)

    async with _client() as client:
        protocol = await client.patch(
            "/config",
            json={
                "updates": {
                    "features": {
                        "formats": {"claude": True},
                        "atg": {"mode": "prefer", "empty_retries": 5},
                    }
                }
            },
        )
        scheduling = await client.patch(
            "/config",
            json={
                "updates": {
                    "rotator": {"quota_scope": "account_surface"},
                    "max_contexts": 4,
                }
            },
        )
        model = await client.patch(
            "/config",
            json={
                "updates": {
                    "model_defaults": {
                        "models": {
                            "gemini-patch-probe": {
                                "generation_config_defaults": {
                                    "temperature": 0.2
                                }
                            }
                        }
                    }
                }
            },
        )

    assert protocol.status_code == 200
    assert scheduling.status_code == 200
    assert model.status_code == 200

    on_disk = _read_yaml(sandbox_config)
    features = _as_dict(on_disk["features"])
    formats = _as_dict(features["formats"])
    atg = _as_dict(features["atg"])
    assert formats["claude"] is True
    # Sibling protocol keys and unrelated features survive the nested merge.
    assert formats["openai"] == _as_dict(_as_dict(before["features"])["formats"])["openai"]
    assert atg["mode"] == "prefer"
    assert atg["empty_retries"] == 5
    assert atg["recovery"] == "repair"
    assert features["routing"] == _as_dict(before["features"])["routing"]
    assert features["keepalive"] == _as_dict(before["features"])["keepalive"]

    rotator = _as_dict(on_disk["rotator"])
    assert rotator["quota_scope"] == "account_surface"
    assert rotator["cooldowns"] == _as_dict(before["rotator"])["cooldowns"]
    assert on_disk["max_contexts"] == 4

    model_defaults = _as_dict(on_disk["model_defaults"])
    assert model_defaults["profiles"] == _as_dict(before["model_defaults"])["profiles"]
    assert "gemini-patch-probe" in _as_dict(model_defaults["models"])


@pytest.mark.asyncio
async def test_patch_config_rejects_invalid_updates_without_writing(
    sandbox_config: Path,
):
    before = sandbox_config.read_text(encoding="utf-8")

    async with _client() as client:
        bad_rotator = await client.patch(
            "/config", json={"updates": {"rotator": []}}
        )
        bad_instances = await client.patch(
            "/config",
            json={
                "updates": {
                    "instances": [
                        {"count": 1, "surface": "chat", "only_tier": "Diamond"}
                    ]
                }
            },
        )

    assert bad_rotator.status_code == 400
    assert "rotator 必须为字典" in bad_rotator.json()["error"]["message"]
    assert bad_instances.status_code == 400
    assert "only_tier" in bad_instances.json()["error"]["message"]
    assert sandbox_config.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_patch_config_ui_debug_never_touches_real_config(monkeypatch):
    real_path = _REPO_ROOT / "config.yaml"
    real_before = real_path.read_text(encoding="utf-8") if real_path.exists() else None

    for name in (
        "set_log_level",
        "set_dump_requests",
        "set_dump_to_file",
        "set_dump_dir",
    ):
        monkeypatch.setattr(routes_system, name, lambda *args, **kwargs: None)

    monkeypatch.setattr(app_module, "resolve_ui_debug", lambda: True)
    async with app_module.app.router.lifespan_context(app_module.app):
        temp_path = Path(os.environ["AISTUDIO_CONFIG_PATH"])
        assert temp_path != real_path
        assert temp_path.is_file()

        async with _client() as client:
            resp = await client.patch(
                "/config",
                json={"updates": {"logging": {"level": "DEBUG"}}},
            )

        assert resp.status_code == 200
        temp_config = _read_yaml(temp_path)
        assert _as_dict(temp_config["logging"])["level"] == "DEBUG"
        if real_before is None:
            assert not real_path.exists()
        else:
            assert real_path.read_text(encoding="utf-8") == real_before

    assert not temp_path.exists()
