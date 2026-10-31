"""Tests for system and configuration routes."""

from __future__ import annotations

import httpx
import pytest

from aistudio_api.api.app import app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_health_endpoint():
    async with _client() as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_auth_check_endpoint():
    async with _client() as client:
        resp = await client.get("/auth/check")
        assert resp.status_code == 200
        data = resp.json()
        assert "auth_enabled" in data


@pytest.mark.asyncio
async def test_spa_endpoints():
    import asyncio

    async with _client() as client:
        responses = await asyncio.gather(
            *[
                client.get(path, headers={"Accept": "text/html"})
                for path in ("/", "/login", "/accounts", "/settings")
            ]
        )
        for resp in responses:
            assert resp.status_code == 200
            assert "<!DOCTYPE html>" in resp.text
            assert "Accept" in resp.headers.get("Vary", "")
            assert "no-store" in resp.headers.get("Cache-Control", "")


@pytest.mark.asyncio
async def test_get_config_endpoint():
    async with _client() as client:
        resp = await client.get("/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "port" in data
        assert "browser_port" in data
        assert "yaml_content" in data


@pytest.mark.asyncio
async def test_update_config_yaml_validation():
    async with _client() as client:
        resp = await client.put(
            "/config/yaml", json={"yaml_content": "invalid: [yaml: broken"}
        )
        assert resp.status_code == 400

        resp_non_dict = await client.put(
            "/config/yaml", json={"yaml_content": "- item1\n- item2"}
        )
        assert resp_non_dict.status_code == 400


@pytest.mark.asyncio
async def test_update_config_yaml_rejects_invalid_rotator_type():
    from aistudio_api.infrastructure.gateway.model_defaults import _resolve_config_path

    config_path = _resolve_config_path(None)
    original = config_path.read_text(encoding="utf-8")

    async with _client() as client:
        resp = await client.put("/config/yaml", json={"yaml_content": "rotator: []\n"})

    assert resp.status_code == 400
    assert "rotator 必须为字典" in resp.json()["error"]["message"]
    assert config_path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_update_config_yaml_rejects_non_dict_logging_block():
    from aistudio_api.infrastructure.gateway.model_defaults import _resolve_config_path

    config_path = _resolve_config_path(None)
    original = config_path.read_text(encoding="utf-8")

    async with _client() as client:
        resp = await client.put("/config/yaml", json={"yaml_content": "logging: []\n"})

    assert resp.status_code == 400
    assert "logging 必须为字典" in resp.json()["error"]["message"]
    assert config_path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_update_config_yaml_applies_normalized_logging(monkeypatch):
    import aistudio_api.api.routes_system as routes_system
    from aistudio_api.infrastructure.gateway.model_defaults import (
        _resolve_config_path,
        invalidate_config_cache,
    )

    config_path = _resolve_config_path(None)
    original = config_path.read_text(encoding="utf-8")
    applied_levels: list[str] = []
    monkeypatch.setattr(
        routes_system, "set_log_level", lambda level: applied_levels.append(level)
    )

    try:
        async with _client() as client:
            resp = await client.put(
                "/config/yaml",
                json={"yaml_content": "logging:\n  level: verbose\n"},
            )

        assert resp.status_code == 200
        assert applied_levels == ["INFO"]
    finally:
        config_path.write_text(original, encoding="utf-8")
        invalidate_config_cache()


@pytest.mark.asyncio
async def test_create_api_key_write_oserror_returns_500(monkeypatch):
    import aistudio_api.api.routes_system as routes_system

    def raise_os_error(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(routes_system, "write_config_text", raise_os_error)

    async with _client() as client:
        resp = await client.post("/api-keys", json={"name": "test", "key": "k-1"})

    assert resp.status_code == 500
    assert "写入配置文件失败" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_update_config_yaml_success_writes_atomically():
    from aistudio_api.infrastructure.gateway.model_defaults import _resolve_config_path

    config_path = _resolve_config_path(None)
    original = config_path.read_text(encoding="utf-8")

    async with _client() as client:
        resp = await client.put("/config/yaml", json={"yaml_content": original})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert config_path.read_text(encoding="utf-8") == original


def test_stats_response_exposes_paid_marker_from_model_access():
    from aistudio_api.api.routes_system import stats_response
    from aistudio_api.api.state import ModelStatsItem, runtime_state

    original = dict(runtime_state.model_stats)
    try:
        runtime_state.model_stats.clear()
        runtime_state.model_stats["gemini-4-argon"] = ModelStatsItem(requests=1)
        runtime_state.model_stats["gemini-3.8-flash"] = ModelStatsItem(requests=1)
        result = stats_response()
        assert result.models["gemini-4-argon"].paid is True
        assert result.models["gemini-3.8-flash"].paid is False
    finally:
        runtime_state.model_stats.clear()
        runtime_state.model_stats.update(original)


@pytest.mark.asyncio
async def test_rotation_status_and_clear_cooldown():
    from unittest.mock import MagicMock

    from aistudio_api.api.state import runtime_state
    from aistudio_api.application.account_rotator import AccountRotator

    mock_rotator = MagicMock(spec=AccountRotator)
    mock_rotator.get_all_stats.return_value = {"acc_1": {"requests": 1}}

    runtime_state.rotator = mock_rotator
    async with _client() as client:
        resp = await client.get("/rotation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "sticky"
        assert "accounts" in data

        resp_clear = await client.post(
            "/rotation/clear-cooldown", json={"account_id": "acc_1"}
        )
        assert resp_clear.status_code == 200
        mock_rotator.clear_cooldown.assert_called_once_with("acc_1", model=None)


@pytest.mark.asyncio
async def test_api_key_route_logs_never_render_key_or_prefix(
    monkeypatch, tmp_path, caplog
):
    import logging
    import shutil
    from pathlib import Path

    import aistudio_api.api.routes_system as routes_system
    import aistudio_api.infrastructure.gateway.model_defaults as model_defaults

    config_path = tmp_path / "config.yaml"
    shutil.copyfile(
        Path(__file__).resolve().parents[2] / "defaults" / "config.yaml",
        config_path,
    )
    monkeypatch.setattr(routes_system, "_resolve_config_path", lambda _p: config_path)
    monkeypatch.setattr(model_defaults, "_resolve_config_path", lambda _p: config_path)
    model_defaults.invalidate_config_cache()
    caplog.set_level(logging.INFO, logger="aistudio.routes.system")

    secret = "sk-b6-SECRETKEY1234567890"
    item = await routes_system.create_api_key(
        routes_system.CreateApiKeyRequest(name="b6-probe", key=secret)
    )
    await routes_system.update_api_key_name(
        item.key, routes_system.UpdateApiKeyRequest(name="b6-renamed")
    )
    await routes_system.delete_api_key(item.key)
    model_defaults.invalidate_config_cache()

    messages = [record.getMessage() for record in caplog.records]
    joined = "\n".join(messages)
    assert any("创建 API Key" in message for message in messages)
    assert any("更新 API Key 备注" in message for message in messages)
    assert any("删除 API Key" in message for message in messages)
    assert secret not in joined
    assert secret[:8] not in joined
    assert "***" in joined
