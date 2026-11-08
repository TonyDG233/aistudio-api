"""C2a offline UI debug sandbox tests (no server/browser/network)."""

from __future__ import annotations

import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import yaml

import aistudio_api.api.app as app_module
from aistudio_api.api.state import runtime_state
from aistudio_api.api.ui_sandbox import UISandbox
from aistudio_api.config import (
    resolve_config_file,
    resolve_data_dir,
    resolve_ui_debug,
    settings,
)
from aistudio_api.infrastructure.gateway.model_defaults import (
    ConfigValidationError,
    validate_config_mapping,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_module.app), base_url="http://test"
    )


@asynccontextmanager
async def _ui_debug_runtime(monkeypatch):
    monkeypatch.setattr(app_module, "resolve_ui_debug", lambda: True)
    async with app_module.app.router.lifespan_context(app_module.app):
        yield


@pytest.fixture
def ui_debug(monkeypatch):
    return _ui_debug_runtime(monkeypatch)


def test_ui_debug_default_env_override_and_schema(monkeypatch, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("diagnostics:\n  ui_debug: true\n", encoding="utf-8")
    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(path))
    monkeypatch.delenv("AISTUDIO_UI_DEBUG", raising=False)
    assert resolve_ui_debug() is True

    monkeypatch.setenv("AISTUDIO_UI_DEBUG", "1")
    assert resolve_ui_debug() is True
    monkeypatch.setenv("AISTUDIO_UI_DEBUG", "0")
    assert resolve_ui_debug() is False

    default_path = tmp_path / "default.yaml"
    default_path.write_text("api_keys: []\n", encoding="utf-8")
    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(default_path))
    monkeypatch.delenv("AISTUDIO_UI_DEBUG", raising=False)
    assert resolve_ui_debug() is False

    defaults = yaml.safe_load(
        (_REPO_ROOT / "defaults" / "config.yaml").read_text(encoding="utf-8")
    )
    assert defaults["diagnostics"]["ui_debug"] is False
    schema = json.loads(
        (_REPO_ROOT / "defaults" / "config.schema.json").read_text(encoding="utf-8")
    )
    assert schema["properties"]["diagnostics"]["properties"]["ui_debug"] == {
        "type": "boolean",
        "default": False,
        "description": schema["properties"]["diagnostics"]["properties"]["ui_debug"][
            "description"
        ],
    }


def test_ui_debug_config_validation_rejects_non_boolean():
    with pytest.raises(ConfigValidationError, match="ui_debug"):
        validate_config_mapping({"diagnostics": {"ui_debug": "yes"}})


@pytest.mark.asyncio
async def test_ui_debug_lifespan_is_offline_and_temp_only(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("real AIStudioClient must not be constructed")

    monkeypatch.setattr(app_module, "AIStudioClient", _boom)
    monkeypatch.setattr(app_module, "resolve_ui_debug", lambda: True)

    from aistudio_api.config import resolve_data_dir

    data_dir = resolve_data_dir()
    before = set(data_dir.rglob("*"))
    sandbox_root: Path | None = None
    async with app_module.app.router.lifespan_context(app_module.app):
        assert runtime_state.ui_debug is True
        assert runtime_state.client is None
        assert runtime_state.routing_pipeline is None
        assert isinstance(runtime_state.ui_sandbox, UISandbox)
        sandbox = runtime_state.ui_sandbox
        sandbox_root = sandbox.root
        assert sandbox_root.is_dir()
        assert sandbox_root.parent == Path(tempfile.gettempdir())
        assert runtime_state.account_service is sandbox.account_service
        async with _client() as client:
            resp = await client.get("/api/accounts")
        assert resp.status_code == 200
        assert len(resp.json()) == 12
        assert set(data_dir.rglob("*")) == before
    assert sandbox_root is not None
    assert not sandbox_root.exists()
    assert runtime_state.ui_sandbox is None
    assert runtime_state.client is None
    assert set(data_dir.rglob("*")) == before


@pytest.mark.asyncio
async def test_debug_endpoints_and_scenario_actions(ui_debug):
    async with ui_debug, _client() as client:
        state = (await client.get("/api/debug/ui-state")).json()
        assert state["ui_debug"] is True
        assert state["ui_debug_scenario"] == "all_states"
        accounts = {item["id"]: item for item in state["accounts"]}
        assert accounts["acc_pro_expired"]["expired"] is True
        assert accounts["acc_pro_session"]["session_expired"] is True
        assert accounts["acc_ultra_cooldown"]["auth_cooldown"] > 0
        assert accounts["acc_ultra_cooldown"]["quota_exhausted"] is False
        assert accounts["acc_free_quota"]["is_available"] is False
        assert accounts["acc_free_quota"]["quota_exhausted"] is True
        assert accounts["acc_free_nomail"]["email"] is None
        assert len(accounts["acc_plus_prod"]["email"]) > 30
        assert {account["tier"] for account in accounts.values()} >= {
            "Free",
            "Plus",
            "Pro",
            "Ultra",
            "Ultra_20X",
        }

        inflight = (
            await client.post(
                "/api/debug/ui-scenario",
                json={"action": "instance_inflight", "target_id": "inst-1"},
            )
        ).json()
        inst1 = next(
            item
            for item in inflight["instances"]["instances"]
            if item["id"] == "inst-1"
        )
        assert inst1["in_flight"] == 2

        expired = (
            await client.post(
                "/api/debug/ui-scenario",
                json={"action": "account_expired", "target_id": "acc_free_ready"},
            )
        ).json()
        by_id = {item["id"]: item for item in expired["accounts"]}
        assert by_id["acc_free_ready"]["expired"] is True

        cooldown = (
            await client.post(
                "/api/debug/ui-scenario",
                json={"action": "instance_cooldown", "target_id": "inst-1"},
            )
        ).json()
        inst1 = next(
            item
            for item in cooldown["instances"]["instances"]
            if item["id"] == "inst-1"
        )
        assert inst1["state"] == "cooldown"

        partial = (
            await client.post(
                "/api/debug/ui-scenario", json={"action": "keepalive_partial"}
            )
        ).json()
        assert partial["keepalive"]["last_error"]

        narrow = (
            await client.post(
                "/api/debug/ui-scenario", json={"scenario": "narrow_edge"}
            )
        ).json()
        assert narrow["ui_debug_scenario"] == "narrow_edge"
        assert len(narrow["accounts"]) == 3

        empty = (
            await client.post("/api/debug/ui-scenario", json={"scenario": "empty"})
        ).json()
        assert empty["accounts"] == []
        assert empty["instances"]["instances"] == []

        mass = (
            await client.post(
                "/api/debug/ui-scenario", json={"scenario": "mass_accounts"}
            )
        ).json()
        assert len(mass["accounts"]) == 48


@pytest.mark.asyncio
async def test_accounts_rest_closed_loop_and_batch_toggle(ui_debug):
    async with ui_debug, _client() as client:
        accounts = (await client.get("/api/accounts")).json()
        active = (await client.get("/api/accounts/active")).json()
        assert active["id"] == "acc_pro_prod"

        disabled = await client.post(
            "/api/accounts/batch-toggle",
            json={"ids": ["acc_pro_prod", "missing-account"], "enabled": False},
        )
        assert disabled.status_code == 200
        payload = disabled.json()
        assert payload["toggled_count"] == 1
        assert payload["error_count"] == 1
        assert payload["results"][1]["error"]

        accounts = {
            item["id"]: item for item in (await client.get("/api/accounts")).json()
        }
        assert accounts["acc_pro_prod"]["enabled"] is False
        new_active = (await client.get("/api/accounts/active")).json()
        assert new_active["id"] != "acc_pro_prod"

        instances = (await client.get("/api/instances")).json()
        bound = {item["account_id"] for item in instances["instances"]}
        assert "acc_pro_prod" not in bound

        toggled = await client.post(
            "/api/accounts/acc_pro_prod/toggle", json={"enabled": True}
        )
        assert toggled.status_code == 200
        assert toggled.json()["enabled"] is True

        detected_email = (
            await client.post("/api/accounts/acc_free_nomail/detect-email")
        ).json()
        assert detected_email["email"]
        detected_tier = (
            await client.post("/api/accounts/acc_pro_prod/detect-tier")
        ).json()
        assert detected_tier["tier"] == "Pro"

        refreshed = await client.post(
            "/api/accounts/refresh", json={"all": True, "refresh_email": True}
        )
        assert refreshed.status_code == 200
        assert refreshed.json()["refreshed_count"] > 0

        activated = await client.post("/api/accounts/acc_plus_prod/activate")
        assert activated.status_code == 200

        groups = (await client.get("/api/accounts/groups/list")).json()
        assert {"default", "production", "vip", "legacy"} <= set(groups)
        presets = (await client.get("/api/accounts/presets/list")).json()
        assert any(item["id"] == "preset_all" for item in presets)

        deleted = await client.post(
            "/api/accounts/batch-delete", json={"ids": ["acc_free_nomail", "nope"]}
        )
        assert deleted.json()["deleted"] == 1

        cookies = json.dumps(
            [
                {
                    "name": "SID",
                    "value": "ui-debug",
                    "domain": ".google.com",
                    "path": "/",
                }
            ]
        )
        imported = await client.post(
            "/api/accounts/import-cookies",
            json={"cookies": cookies, "name": "Imported", "tier": "Pro"},
        )
        assert imported.status_code == 200
        assert imported.json()["cookie_count"] == 1

        bundle = await client.post(
            "/api/accounts/import-bundle",
            json={
                "accounts": [
                    {
                        "name": "Bundle One",
                        "email": "bundle@example.com",
                        "cookies": cookies,
                        "tier": "Plus",
                    }
                ]
            },
        )
        assert bundle.status_code == 200
        assert bundle.json()["imported_count"] == 1


@pytest.mark.asyncio
async def test_instances_fields_selector_and_actions(ui_debug):
    async with ui_debug, _client() as client:
        listing = (await client.get("/api/instances")).json()
        states = {item["state"] for item in listing["instances"]}
        assert {
            "active",
            "unstarted",
            "cooldown",
            "invalid",
            "starting",
            "retiring",
            "sleeping",
            "reclaiming",
        } <= states
        sample = listing["instances"][0]
        assert sample["account_email"]
        assert sample["account_tier"]
        assert sample["fingerprint"]
        assert sample["tier_selector"]["min_tier"]

        sleeping = next(
            item for item in listing["instances"] if item["state"] == "sleeping"
        )
        assert sleeping["account_id"]
        assert sleeping["fingerprint"]

        duplicate = await client.post(
            "/api/instances", json={"account_id": "acc_pro_prod"}
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"]["code"] == "instance_already_exists"

        unbound_restart = await client.post("/api/instances/inst-2/restart")
        assert unbound_restart.status_code == 409
        unbound_wake = await client.post("/api/instances/inst-2/wake")
        assert unbound_wake.status_code == 409
        assert "账号" in unbound_wake.json()["detail"]["message"]

        created = await client.post(
            "/api/instances",
            json={
                "account_id": "acc_pro_session",
                "surface": "chat",
                "only_tier": "Pro",
            },
        )
        assert created.status_code == 201, created.text
        created_payload = created.json()
        assert created_payload["account_tier"] == "Pro"
        assert created_payload["account_email"] == "pro.session@example.com"
        assert created_payload["fingerprint"]
        assert created_payload["tier_selector"]["only_tier"] == "Pro"
        instance_id = created_payload["id"]

        switched = await client.post(
            f"/api/instances/{instance_id}/switch", json={"surface": "build"}
        )
        assert switched.json()["surface"] == "build"

        restarted = await client.post(f"/api/instances/{instance_id}/restart")
        assert restarted.json()["state"] == "active"

        reassigned = await client.post(
            f"/api/instances/{instance_id}/reassign",
            json={"account_id": "acc_cookie_shared_b"},
        )
        assert reassigned.json()["account_id"] == "acc_cookie_shared_b"

        bound = await client.post(
            "/api/instances/inst-2/reassign",
            json={"account_id": "acc_free_nomail"},
        )
        assert bound.status_code == 200
        assert bound.json()["account_id"] == "acc_free_nomail"

        woken = await client.post("/api/instances/inst-7/wake")
        assert woken.status_code == 200
        assert woken.json()["state"] == "active"

        invalid = await client.post(
            "/api/instances",
            json={"account_id": "acc_free_ready", "min_tier": "Pro"},
        )
        assert invalid.status_code == 409

        closed = await client.delete(f"/api/instances/{instance_id}")
        assert closed.status_code == 202
        assert closed.json()["state"] == "reclaiming"


@pytest.mark.asyncio
async def test_keepalive_and_system_routes(ui_debug):
    async with ui_debug, _client() as client:
        status = (await client.get("/api/keepalive")).json()
        assert status["available"] is True
        assert "settings" in status

        run = await client.post("/api/keepalive/run", json={"task": "metadata_refresh"})
        assert run.status_code == 200
        assert run.json()["ok"] is True

        unknown = await client.post("/api/keepalive/run", json={"task": "nope"})
        assert unknown.status_code == 400

        rotation = (await client.get("/rotation")).json()
        assert rotation["ui_debug"] is True
        assert rotation["ui_debug_scenario"] == "all_states"
        assert "acc_pro_prod" in rotation["accounts"]

        stats = await client.get("/stats")
        assert stats.status_code == 200
        assert stats.json()["quota_scope"] == "account"

        next_account = await client.post("/rotation/next")
        assert next_account.status_code == 200
        assert next_account.json()["ui_debug"] is True

        health = (await client.get("/health")).json()
        assert health["ui_debug"] is True

        config = (await client.get("/config")).json()
        assert config["ui_debug"] is True
        assert config["ui_debug_scenario"] == "all_states"

        system_status = await client.get("/api/system/status")
        assert system_status.status_code == 200
        assert system_status.json()["ui_debug"] is True
        assert system_status.json()["ui_debug_scenario"] == "all_states"


@pytest.mark.asyncio
async def test_debug_endpoints_404_when_ui_debug_false(monkeypatch):
    from aistudio_api.api import dependencies

    monkeypatch.setattr(dependencies.settings, "web_password", "secret")
    saved_debug = runtime_state.ui_debug
    saved_sandbox = runtime_state.ui_sandbox
    runtime_state.ui_debug = False
    runtime_state.ui_sandbox = None
    try:
        async with _client() as client:
            get_resp = await client.get("/api/debug/ui-state")
            post_resp = await client.post(
                "/api/debug/ui-scenario", json={"scenario": "empty"}
            )
        assert get_resp.status_code == 404
        assert post_resp.status_code == 404
    finally:
        runtime_state.ui_debug = saved_debug
        runtime_state.ui_sandbox = saved_sandbox


@pytest.mark.asyncio
async def test_debug_endpoints_require_web_auth_when_enabled(ui_debug, monkeypatch):
    monkeypatch.setattr(settings, "web_password", "secret")
    async with ui_debug, _client() as client:
        denied = await client.get("/api/debug/ui-state")
        assert denied.status_code == 401
        allowed = await client.get(
            "/api/debug/ui-state", headers={"Authorization": "Bearer secret"}
        )
        assert allowed.status_code == 200
        assert allowed.json()["ui_debug"] is True


@pytest.mark.asyncio
async def test_ui_debug_model_routes_are_offline_free_and_paid(ui_debug, monkeypatch):
    from aistudio_api.infrastructure.account import account_store as account_store_mod
    from aistudio_api.infrastructure.gateway import model_discovery as discovery_mod

    def _disk_boom(*_args, **_kwargs):
        raise AssertionError("real AccountStore must not be constructed")

    async def _network_boom(*_args, **_kwargs):
        raise AssertionError("real model discovery network must not run")

    monkeypatch.setattr(settings, "web_password", "secret")
    headers = {"Authorization": "Bearer secret"}
    async with ui_debug, _client() as client:
        # The sandbox AccountStore already exists; any later construction is a leak.
        monkeypatch.setattr(
            account_store_mod.AccountStore, "__init__", _disk_boom
        )
        monkeypatch.setattr(
            discovery_mod.ModelDiscoveryService, "_fetch_via_http", _network_boom
        )
        monkeypatch.setattr(
            discovery_mod.ModelDiscoveryService, "_fetch_via_page", _network_boom
        )

        gemini = await client.get("/v1beta/models", headers=headers)
        assert gemini.status_code == 200, gemini.text
        gemini_ids = {
            item["name"].removeprefix("models/") for item in gemini.json()["models"]
        }
        assert {"gemini-3.7-flash", "gemini-4-argon"} <= gemini_ids

        admin = await client.get("/api/models", headers=headers)
        assert admin.status_code == 200, admin.text
        admin_ids = {
            item["name"].removeprefix("models/") for item in admin.json()["models"]
        }
        assert {"gemini-3.7-flash", "gemini-4-argon"} <= admin_ids

        openai = await client.get("/v1/models", headers=headers)
        assert openai.status_code == 200, openai.text
        openai_ids = {item["id"] for item in openai.json()["data"]}
        assert {"gemini-3.7-flash", "gemini-4-argon"} <= openai_ids

        one_gemini = await client.get(
            "/v1beta/models/gemini-4-argon", headers=headers
        )
        assert one_gemini.status_code == 200
        assert one_gemini.json()["name"] == "models/gemini-4-argon"

        one_openai = await client.get("/v1/models/gemini-4-argon", headers=headers)
        assert one_openai.status_code == 200
        assert one_openai.json()["id"] == "gemini-4-argon"


@pytest.mark.asyncio
async def test_ui_debug_config_keys_and_logs_use_temp_paths(monkeypatch):
    repo_config = _REPO_ROOT / "config.yaml"
    repo_accounts = _REPO_ROOT / "data" / "accounts"

    def _snapshot(root: Path) -> dict[str, tuple[int, int]] | None:
        if not root.exists():
            return None
        return {
            str(path.relative_to(root)): (path.stat().st_mtime_ns, path.stat().st_size)
            for path in root.rglob("*")
            if path.is_file()
        }

    config_before = (
        (repo_config.stat().st_mtime_ns, repo_config.read_bytes())
        if repo_config.is_file()
        else None
    )
    accounts_before = _snapshot(repo_accounts)

    data_dir = resolve_data_dir()
    monkeypatch.setattr(app_module, "resolve_ui_debug", lambda: True)
    monkeypatch.setattr(settings, "web_password", "secret")
    headers = {"Authorization": "Bearer secret"}
    temp_config: Path | None = None
    temp_data: Path | None = None
    async with app_module.app.router.lifespan_context(app_module.app):
        from aistudio_api.infrastructure.utils.log_broadcast import resolve_log_dir

        temp_config = resolve_config_file()
        temp_data = resolve_data_dir()
        assert temp_config != repo_config
        assert temp_data != data_dir
        assert temp_config.is_file()
        assert resolve_log_dir().parent == temp_data
        async with _client() as client:
            config = (await client.get("/config", headers=headers)).json()
            assert config["ui_debug"] is True
            assert config["yaml_content"]

            created = await client.post(
                "/api-keys", json={"name": "ui-debug-key"}, headers=headers
            )
            assert created.status_code == 200, created.text
            assert "ui-debug-key" in temp_config.read_text(encoding="utf-8")

            export = await client.get("/api/logs/export?days=1", headers=headers)
            assert export.status_code == 404

        assert Path(settings.config_file) == temp_config
        assert Path(settings.data_dir) == temp_data
    assert temp_config is not None and not temp_config.exists()
    assert temp_data is not None and not temp_data.exists()
    assert Path(settings.config_file) != temp_config
    assert Path(settings.data_dir) != temp_data
    config_after = (
        (repo_config.stat().st_mtime_ns, repo_config.read_bytes())
        if repo_config.is_file()
        else None
    )
    assert config_after == config_before
    assert _snapshot(repo_accounts) == accounts_before


@pytest.mark.asyncio
async def test_reassign_drains_inflight_and_binds_idle_slots(ui_debug):
    async with ui_debug, _client() as client:
        listing = (await client.get("/api/instances")).json()
        inst1 = next(item for item in listing["instances"] if item["id"] == "inst-1")
        assert inst1["in_flight"] == 1

        drained = await client.post(
            "/api/instances/inst-1/reassign",
            json={"account_id": "acc_pro_session"},
        )
        assert drained.status_code == 200, drained.text
        assert drained.json()["account_id"] == "acc_pro_session"
        assert drained.json()["in_flight"] == 0

        bound = await client.post(
            "/api/instances/inst-2/reassign",
            json={"account_id": "acc_free_nomail"},
        )
        assert bound.status_code == 200, bound.text
        assert bound.json()["state"] == "active"

        sleeping = await client.post(
            "/api/instances/inst-7/reassign",
            json={"account_id": "acc_cookie_shared_a"},
        )
        assert sleeping.status_code == 200, sleeping.text
        assert sleeping.json()["account_id"] == "acc_cookie_shared_a"
        assert sleeping.json()["state"] == "active"

        rejected = await client.post(
            "/api/instances/inst-2/reassign",
            json={"account_id": "acc_cookie_shared_a"},
        )
        assert rejected.status_code == 409
        detail = rejected.json()["detail"]
        assert detail["code"] == "instance_already_exists"
        assert "已绑定实例" in detail["message"]


@pytest.mark.asyncio
async def test_empty_scenario_and_quick_actions(ui_debug):
    async with ui_debug, _client() as client:
        empty = (
            await client.post(
                "/api/debug/ui-scenario", json={"scenario": "empty"}
            )
        ).json()
        assert empty["accounts"] == []
        assert empty["instances"]["instances"] == []

        invalid = await client.post(
            "/api/debug/ui-scenario", json={"action": "instance_inflight"}
        )
        assert invalid.status_code == 400

        restored = (
            await client.post(
                "/api/debug/ui-scenario", json={"scenario": "all_states"}
            )
        ).json()
        assert restored["accounts"]
        acted = (
            await client.post(
                "/api/debug/ui-scenario",
                json={"action": "instance_inflight", "target_id": "inst-1"},
            )
        ).json()
        inst1 = next(
            item for item in acted["instances"]["instances"] if item["id"] == "inst-1"
        )
        assert inst1["in_flight"] == 2

        reset = (
            await client.post("/api/debug/ui-scenario", json={"action": "reset"})
        ).json()
        inst1 = next(
            item for item in reset["instances"]["instances"] if item["id"] == "inst-1"
        )
        assert inst1["in_flight"] == 1


@pytest.mark.asyncio
async def test_batch_delete_and_toggle_zero_results(ui_debug):
    async with ui_debug, _client() as client:
        deleted = await client.post(
            "/api/accounts/batch-delete", json={"ids": ["nope-1", "nope-2"]}
        )
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] == 0

        toggled = await client.post(
            "/api/accounts/batch-toggle",
            json={"ids": ["nope-1", "nope-2"], "enabled": False},
        )
        assert toggled.status_code == 200
        payload = toggled.json()
        assert payload["toggled_count"] == 0
        assert payload["error_count"] == 2


@pytest.mark.asyncio
async def test_recovered_account_ignores_invalid_stale_binding(ui_debug):
    async with ui_debug, _client() as client:
        recovered = await client.post(
            "/rotation/clear-cooldown", json={"account_id": "acc_pro_expired"}
        )
        assert recovered.status_code == 200

        listing = (await client.get("/api/instances")).json()
        stale = next(item for item in listing["instances"] if item["id"] == "inst-4")
        assert stale["state"] == "invalid"
        assert stale["account_id"] == "acc_pro_expired"

        rebound = await client.post(
            "/api/instances/inst-9/reassign",
            json={"account_id": "acc_pro_expired"},
        )
        assert rebound.status_code == 200, rebound.text
        assert rebound.json()["account_id"] == "acc_pro_expired"
        assert rebound.json()["state"] == "active"

        usable = await client.post(
            "/api/instances/inst-9/reassign",
            json={"account_id": "acc_pro_session"},
        )
        assert usable.status_code == 200, usable.text


@pytest.mark.asyncio
async def test_recover_and_detect_clear_expired_and_session_state(ui_debug):
    async with ui_debug, _client() as client:
        expired = (await client.get("/api/accounts")).json()
        by_id = {item["id"]: item for item in expired}
        assert by_id["acc_pro_expired"]["expired"] is True
        assert by_id["acc_pro_session"]["session_expired"] is True

        recovered = await client.post(
            "/rotation/clear-cooldown", json={"account_id": "acc_pro_expired"}
        )
        assert recovered.status_code == 200
        cleared = {
            item["id"]: item for item in (await client.get("/api/accounts")).json()
        }
        assert cleared["acc_pro_expired"]["expired"] is False
        activated = await client.post("/api/accounts/acc_pro_expired/activate")
        assert activated.status_code == 200

        detected = await client.post("/api/accounts/acc_pro_session/detect-tier")
        assert detected.status_code == 200
        refreshed = {
            item["id"]: item for item in (await client.get("/api/accounts")).json()
        }
        assert refreshed["acc_pro_session"]["session_expired"] is False
        bound = await client.post(
            "/api/instances/inst-4/reassign",
            json={"account_id": "acc_pro_session"},
        )
        assert bound.status_code == 200, bound.text


@pytest.mark.asyncio
async def test_engine_config_switch_and_health_in_sandbox(ui_debug):
    async with ui_debug, _client() as client:
        config = (await client.get("/config")).json()
        document = yaml.safe_load(config["yaml_content"])
        engine = document["engine"]
        assert engine["mode"] == "browser"

        health = (await client.post("/api/engine/health-check")).json()
        assert health["ok"] is True
        assert health["engine"] == "browser"

        engine["mode"] = "browserless"
        engine["browserless"]["signer"]["auto_start"] = False
        patched = await client.patch("/config", json={"updates": {"engine": engine}})
        assert patched.status_code == 200, patched.text

        status = (await client.get("/api/system/status")).json()
        assert status["engine"] == "browserless"
        assert status["engine_phase"] == "idle"

        listing = (await client.get("/api/instances")).json()
        assert listing["engine"] == "browserless"
        assert listing["engine_target"] == "browserless"

        probe = (await client.post("/api/engine/health-check")).json()
        assert probe["ok"] is True
        assert probe["engine"] == "browserless"
        assert probe["workers"] >= 1
        assert probe["maxWorkers"] >= 1
        assert probe["elapsed_ms"] >= 0

        failing = (
            await client.post(
                "/api/debug/ui-scenario", json={"action": "engine_health_fail"}
            )
        ).json()
        assert failing["engine_health"]["ok"] is False
        fail_probe = (await client.post("/api/engine/health-check")).json()
        assert fail_probe["ok"] is False
        assert fail_probe["error"]

        await client.post("/api/debug/ui-scenario", json={"action": "engine_switching"})
        switching = (await client.get("/api/instances")).json()
        assert switching["engine_phase"] == "rebuilding"
        assert switching["engine_target"] == "browser"

        await client.post(
            "/api/debug/ui-scenario", json={"action": "engine_switch_done"}
        )
        done = (await client.post("/api/engine/health-check")).json()
        assert done["engine"] == "browser"
