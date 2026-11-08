"""POST /config/reset: official defaults, API key preservation and sandbox isolation."""

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
from aistudio_api.config import settings

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
def sandbox_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
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
async def test_reset_restores_defaults_and_preserves_api_keys(sandbox_config: Path):
    current = _read_yaml(sandbox_config)
    current["api_keys"] = [
        {"name": "kept", "key": "sk-kept-123", "created_at": "2026-01-01"}
    ]
    current["max_contexts"] = 9
    _as_dict(current["security"])["api_allow_anonymous"] = True
    _as_dict(current["logging"])["level"] = "DEBUG"
    _as_dict(_as_dict(current["features"])["atg"])["mode"] = "prefer"
    sandbox_config.write_text(
        yaml.dump(current, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    async with _client() as client:
        resp = await client.post("/config/reset")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert isinstance(payload["yaml_content"], str)

    reset = _read_yaml(sandbox_config)
    defaults = _read_yaml(_DEFAULTS_PATH)
    assert reset["api_keys"] == current["api_keys"]
    assert reset["max_contexts"] == defaults["max_contexts"]
    assert _as_dict(reset["security"])["api_allow_anonymous"] is False
    assert _as_dict(reset["logging"])["level"] == "INFO"
    assert _as_dict(_as_dict(reset["features"])["atg"])["mode"] == "off"
    # Response carries the fresh full config.
    response_doc = yaml.safe_load(payload["yaml_content"]) or {}
    assert _as_dict(response_doc)["api_keys"] == current["api_keys"]


@pytest.mark.asyncio
async def test_reset_requires_web_auth(sandbox_config: Path, monkeypatch):
    monkeypatch.setattr(settings, "web_password", "reset-secret")

    async with _client() as client:
        unauthenticated = await client.post("/config/reset")
        authenticated = await client.post(
            "/config/reset", headers={"Authorization": "Bearer reset-secret"}
        )

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200


@pytest.mark.asyncio
async def test_reset_missing_template_returns_500_without_writing(
    sandbox_config: Path, monkeypatch
):
    before = sandbox_config.read_text(encoding="utf-8")

    def _missing_template() -> Path:
        raise FileNotFoundError("defaults/config.yaml not found")

    monkeypatch.setattr(
        routes_system, "resolve_default_config_template", _missing_template
    )

    async with _client() as client:
        resp = await client.post("/config/reset")

    assert resp.status_code == 500
    assert "默认配置模板不存在" in resp.json()["error"]["message"]
    assert sandbox_config.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_reset_write_failure_returns_500(sandbox_config: Path, monkeypatch):
    def _write_failure(*args, **kwargs) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(routes_system, "write_config_text", _write_failure)

    async with _client() as client:
        resp = await client.post("/config/reset")

    assert resp.status_code == 500
    assert "写入配置文件失败" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_reset_in_ui_debug_keeps_ui_debug_and_never_touches_real_config(
    monkeypatch,
):
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

        async with _client() as client:
            await client.patch(
                "/config",
                json={
                    "updates": {
                        "api_keys": [
                            {
                                "name": "sandbox-kept",
                                "key": "sk-sandbox-kept",
                                "created_at": "2026-01-01",
                            }
                        ],
                        "max_contexts": 6,
                    }
                },
            )
            resp = await client.post("/config/reset")

        assert resp.status_code == 200
        reset = _read_yaml(temp_path)
        assert reset["max_contexts"] == 2
        assert _as_dict(reset["diagnostics"])["ui_debug"] is True
        assert reset["api_keys"] == [
            {
                "name": "sandbox-kept",
                "key": "sk-sandbox-kept",
                "created_at": "2026-01-01",
            }
        ]
        assert resp.json()["ui_debug"] is True

        if real_before is None:
            assert not real_path.exists()
        else:
            assert real_path.read_text(encoding="utf-8") == real_before

    assert not temp_path.exists()
