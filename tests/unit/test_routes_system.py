"""Tests for system and configuration routes."""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

import httpx
import pytest

from aistudio_api.api.app import app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.fixture(autouse=True)
def _reset_manual_refresh_gate(monkeypatch):
    from aistudio_api.api import routes_system

    monkeypatch.setattr(routes_system, "_last_manual_refresh_started_at", None)


@pytest.mark.asyncio
async def test_health_endpoint():
    async with _client() as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_system_status_includes_verify_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aistudio_api.api import routes_system

    monkeypatch.setattr(
        routes_system, "verify_capability", lambda: (False, "no-desktop")
    )
    async with _client() as client:
        resp = await client.get("/api/system/status")
    assert resp.status_code == 200
    assert resp.json()["verify"] == {"supported": False, "reason": "no-desktop"}


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
    # Keep the runtime dump override untouched: only the level is under test.
    monkeypatch.setattr(routes_system, "set_dump_requests", lambda _value: None)
    monkeypatch.setattr(routes_system, "set_dump_to_file", lambda _value: None)
    monkeypatch.setattr(routes_system, "set_dump_dir", lambda _value: None)

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
async def test_rotation_status_and_clear_cooldown(monkeypatch):
    from unittest.mock import MagicMock

    from aistudio_api.api.state import runtime_state
    from aistudio_api.application.account_rotator import AccountRotator

    mock_rotator = MagicMock(spec=AccountRotator)
    mock_rotator.get_all_stats.return_value = {"acc_1": {"requests": 1}}

    monkeypatch.setattr(runtime_state, "rotator", mock_rotator)
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
async def test_models_refresh_requires_web_auth(monkeypatch):
    from aistudio_api.config import settings

    monkeypatch.setattr(settings, "web_password", "route-secret")

    async with _client() as client:
        resp = await client.post("/api/models/refresh")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_models_refresh_returns_source_and_count(monkeypatch):
    from aistudio_api.config import settings
    from aistudio_api.infrastructure.gateway import model_discovery as discovery_module

    monkeypatch.setattr(settings, "web_password", "route-secret")
    monkeypatch.setattr(discovery_module.model_discovery, "last_source", "live_signer")
    monkeypatch.setattr(discovery_module.model_discovery, "last_error", None)
    seen: dict[str, object] = {}

    async def _fake_get_models(**kwargs: object) -> list[dict[str, object]]:
        seen.update(kwargs)
        return [{"id": "model-a"}, {"id": "model-b"}]

    monkeypatch.setattr(
        discovery_module.model_discovery, "get_models", _fake_get_models
    )

    async with _client() as client:
        resp = await client.post(
            "/api/models/refresh",
            headers={"Authorization": "Bearer route-secret"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "live_signer"
    assert data["model_count"] == 2
    assert "error" not in data
    assert seen["force_refresh"] is True
    assert seen["reset_backoff"] is True


@pytest.mark.asyncio
async def test_models_refresh_timeout_returns_error_field(monkeypatch):
    from aistudio_api.api import routes_system
    from aistudio_api.config import settings
    from aistudio_api.infrastructure.gateway import model_discovery as discovery_module

    monkeypatch.setattr(settings, "web_password", "route-secret")
    monkeypatch.setattr(routes_system, "_MODEL_REFRESH_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(discovery_module.model_discovery, "last_source", "snapshot")

    async def _hang(**kwargs: object) -> list[dict[str, object]]:
        del kwargs
        await asyncio.sleep(5)
        return []

    monkeypatch.setattr(discovery_module.model_discovery, "get_models", _hang)

    async with _client() as client:
        resp = await client.post(
            "/api/models/refresh",
            headers={"Authorization": "Bearer route-secret"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "snapshot"
    assert isinstance(data["model_count"], int)
    assert "超时" in data["error"]


@pytest.mark.asyncio
async def test_models_refresh_failure_returns_error_field_not_500(monkeypatch):
    from aistudio_api.config import settings
    from aistudio_api.infrastructure.gateway import model_discovery as discovery_module

    monkeypatch.setattr(settings, "web_password", "route-secret")
    monkeypatch.setattr(discovery_module.model_discovery, "last_source", "fallback")

    async def _boom(**kwargs: object) -> list[dict[str, object]]:
        del kwargs
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(discovery_module.model_discovery, "get_models", _boom)

    async with _client() as client:
        resp = await client.post(
            "/api/models/refresh",
            headers={"Authorization": "Bearer route-secret"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "fallback"
    assert isinstance(data["model_count"], int)
    assert "RuntimeError" in data["error"]
    assert "upstream exploded" in data["error"]


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


@pytest.mark.asyncio
async def test_models_refresh_is_rate_limited_without_touching_backoff(monkeypatch):
    from aistudio_api.config import settings
    from aistudio_api.infrastructure.gateway import model_discovery as discovery_module

    monkeypatch.setattr(settings, "web_password", "route-secret")
    monkeypatch.setattr(discovery_module.model_discovery, "last_source", "snapshot")
    monkeypatch.setattr(
        discovery_module.model_discovery,
        "_cached_models",
        [{"id": "cached-a"}, {"id": "cached-b"}, {"id": "cached-c"}],
    )
    retry_at = time.time() + 7200
    monkeypatch.setattr(discovery_module.model_discovery, "_next_retry_at", retry_at)
    calls: list[dict[str, object]] = []

    async def _fake_get_models(**kwargs: object) -> list[dict[str, object]]:
        calls.append(kwargs)
        return [{"id": "model-a"}, {"id": "model-b"}]

    monkeypatch.setattr(
        discovery_module.model_discovery, "get_models", _fake_get_models
    )

    async with _client() as client:
        first = await client.post(
            "/api/models/refresh",
            headers={"Authorization": "Bearer route-secret"},
        )
        second = await client.post(
            "/api/models/refresh",
            headers={"Authorization": "Bearer route-secret"},
        )

    assert first.status_code == 200
    assert "error" not in first.json()
    assert second.status_code == 200
    data = second.json()
    assert data["source"] == "snapshot"
    assert data["model_count"] == 3
    assert "手动刷新过于频繁" in data["error"]
    assert "秒后再试" in data["error"]
    assert len(calls) == 1
    assert discovery_module.model_discovery._next_retry_at == retry_at


@pytest.mark.asyncio
async def test_models_refresh_uses_sandbox_payload_only_in_ui_debug(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aistudio_api.api import routes_system
    from aistudio_api.infrastructure.gateway import model_discovery as discovery_module

    sandbox_payload = {"source": "live_signer", "model_count": 10}
    sandbox_calls: list[int] = []

    def _models_refresh_payload() -> dict[str, object]:
        sandbox_calls.append(1)
        return dict(sandbox_payload)

    sandbox = SimpleNamespace(models_refresh_payload=_models_refresh_payload)
    get_models = AsyncMock(side_effect=AssertionError("must not run in ui_debug"))
    monkeypatch.setattr(discovery_module.model_discovery, "get_models", get_models)

    debug_state = SimpleNamespace(ui_debug=True, ui_sandbox=sandbox)
    debug_result = await routes_system.refresh_models(
        runtime_state=cast(Any, debug_state)
    )

    assert debug_result == sandbox_payload
    assert sandbox_calls == [1]
    assert get_models.await_count == 0

    async def _fake_get_models(**kwargs: object) -> list[dict[str, object]]:
        del kwargs
        return [{"id": "real-a"}]

    monkeypatch.setattr(discovery_module.model_discovery, "last_source", "live_signer")
    monkeypatch.setattr(discovery_module.model_discovery, "last_error", None)
    monkeypatch.setattr(
        discovery_module.model_discovery, "get_models", _fake_get_models
    )
    prod_state = SimpleNamespace(ui_debug=False, ui_sandbox=sandbox)
    prod_result = await routes_system.refresh_models(
        runtime_state=cast(Any, prod_state)
    )

    assert sandbox_calls == [1]
    assert prod_result == {"source": "live_signer", "model_count": 1}


# ------------------------------------------------------------ exit pool status

_EXIT_ENV_VALUES = {
    "AISTUDIO_EXIT_MODE": "external",
    "AISTUDIO_EXIT_ENDPOINT": "http://172.17.0.1:41999",
    "AISTUDIO_EXIT_TOKEN": "secret-token",
    "AISTUDIO_EXIT_HMAC_KEY": "hmac-key",
}

_EXIT_POOL_DOWN = {
    "manager_ok": False,
    "healthy": 0,
    "total": 0,
    "quarantined": 0,
    "disabled": 0,
    "bound_accounts": 0,
    "last_switch_at": 0,
}


def _enable_external_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _EXIT_ENV_VALUES.items():
        monkeypatch.setenv(name, value)


def _exit_resolver_with(handler: Any) -> Any:
    from aistudio_api.infrastructure.exit_pool import ExitPoolConfig, ExitResolver

    config = ExitPoolConfig.from_env()
    assert config is not None, "external env must be set"
    transport = httpx.MockTransport(handler)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    return ExitResolver(config, client_factory=factory)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("proxy_url", "expected_mode"),
    [(None, "direct"), ("socks5://127.0.0.1:1080", "single")],
)
async def test_get_config_without_external_mode_skips_manager(
    monkeypatch: pytest.MonkeyPatch, proxy_url: str | None, expected_mode: str
) -> None:
    from aistudio_api.api.state import runtime_state
    from aistudio_api.config import settings
    from aistudio_api.infrastructure.exit_pool import resolver as resolver_module

    monkeypatch.setattr(settings, "proxy_url", proxy_url)
    monkeypatch.setattr(runtime_state, "ui_debug", False)
    builds: list[object] = []

    def _forbidden(*args: object, **kwargs: object) -> object:
        builds.append(args)
        raise AssertionError("resolver must not be built when the env is off")

    monkeypatch.setattr(resolver_module, "ExitResolver", _forbidden)

    async with _client() as client:
        resp = await client.get("/config")

    assert resp.status_code == 200
    data = resp.json()
    assert data["proxy_mode"] == expected_mode
    assert data["exit_pool"] is None
    assert builds == []


@pytest.mark.asyncio
async def test_get_config_external_pool_returns_whitelisted_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aistudio_api.infrastructure.exit_pool import resolver as resolver_module

    _enable_external_exit(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/status"
        assert request.headers["authorization"] == "Bearer secret-token"
        return httpx.Response(
            200,
            json={
                "manager_ok": True,
                "exits": {
                    "healthy": 2,
                    "total": 3,
                    "quarantined": 1,
                    "disabled": 0,
                    "unknown": 9,
                },
                "bound_accounts": 5,
                "last_switch_at": 1700000000,
                "secret": "must-not-leak",
            },
        )

    resolver = _exit_resolver_with(handler)
    monkeypatch.setattr(resolver_module, "get_exit_resolver", lambda: resolver)
    try:
        async with _client() as client:
            data = (await client.get("/config")).json()
    finally:
        await resolver.aclose()

    assert data["proxy_mode"] == "external_pool"
    assert data["exit_pool"] == {
        "manager_ok": True,
        "healthy": 2,
        "total": 3,
        "quarantined": 1,
        "disabled": 0,
        "bound_accounts": 5,
        "last_switch_at": 1700000000,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http_503", "timeout"])
async def test_get_config_external_manager_down_reports_manager_ok_false(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from aistudio_api.infrastructure.exit_pool import resolver as resolver_module

    _enable_external_exit(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("manager slow", request=request)
        return httpx.Response(503, content=b"")

    resolver = _exit_resolver_with(handler)
    monkeypatch.setattr(resolver_module, "get_exit_resolver", lambda: resolver)
    try:
        async with _client() as client:
            data = (await client.get("/config")).json()
    finally:
        await resolver.aclose()

    assert data["proxy_mode"] == "external_pool"
    assert data["exit_pool"] == _EXIT_POOL_DOWN


@pytest.mark.asyncio
async def test_get_config_ignores_sandbox_query_outside_ui_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aistudio_api.api.state import runtime_state
    from aistudio_api.config import settings
    from aistudio_api.infrastructure.exit_pool import resolver as resolver_module

    monkeypatch.setattr(settings, "proxy_url", None)
    monkeypatch.setattr(runtime_state, "ui_debug", False)
    monkeypatch.setattr(resolver_module, "get_exit_resolver", lambda: None)

    async with _client() as client:
        data = (
            await client.get(
                "/config", params={"proxy_mode": "external_pool", "manager_ok": "1"}
            )
        ).json()

    assert data["proxy_mode"] == "direct"
    assert data["exit_pool"] is None
