"""System and metadata routes."""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from aistudio_api.api.dependencies import get_runtime_state
from aistudio_api.api.response_models import (
    HealthResponse,
    StatsResponse,
)
from aistudio_api.config import resolve_default_config_template
from aistudio_api.infrastructure.gateway.model_defaults import (
    ConfigValidationError,
    _default_config,
    _resolve_config_path,
    engine_settings_hash,
    get_configured_logging_settings,
    merge_config_documents,
    validate_config_mapping,
    validate_engine_settings,
    write_config_text,
)
from aistudio_api.infrastructure.utils.logger import (
    get_logger,
    set_dump_dir,
    set_dump_requests,
    set_dump_to_file,
    set_log_level,
)

logger = get_logger("routes.system")

if TYPE_CHECKING:
    from aistudio_api.api.state import RuntimeState

public_router = APIRouter()
protected_router = APIRouter()


def _ui_debug_scenario(runtime_state: RuntimeState) -> str | None:
    sandbox = runtime_state.ui_sandbox
    if runtime_state.ui_debug and sandbox is not None:
        return str(getattr(sandbox, "scenario", "") or "") or None
    return None


def health_response() -> HealthResponse:
    from aistudio_api.api.state import runtime_state

    return HealthResponse(
        status="ok",
        busy=False,
        ui_debug=bool(runtime_state.ui_debug),
        ui_debug_scenario=_ui_debug_scenario(runtime_state),
    )


def _format_flag(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "false", "0", "off", "no"}
    return bool(value)


def formats_status(runtime_state: RuntimeState) -> dict[str, bool]:
    """Return the four format switches; Gemini is always enabled."""
    sandbox = runtime_state.ui_sandbox if runtime_state.ui_debug else None
    getter = getattr(sandbox, "formats_payload", None)
    if callable(getter):
        payload = getter()
        if isinstance(payload, dict):
            return {str(key): bool(value) for key, value in payload.items()}
    from aistudio_api.infrastructure.gateway.model_defaults import (
        get_configured_formats,
    )

    raw = get_configured_formats()
    return {
        "gemini": True,
        "openai": _format_flag(raw.get("openai", True)),
        "claude": _format_flag(raw.get("claude", True)),
        "responses": _format_flag(raw.get("responses", True)),
    }


def default_text_model(runtime_state: RuntimeState) -> str:
    getter = getattr(runtime_state.ui_sandbox, "default_text_model", None)
    if runtime_state.ui_debug and callable(getter):
        value = getter()
        if value:
            return str(value)
    from aistudio_api.config import DEFAULT_TEXT_MODEL

    return DEFAULT_TEXT_MODEL


def stats_response(days: int | None = None) -> StatsResponse:
    """Render ``GET /stats`` for the real runtime or the UI debug sandbox.

    ``days`` is ``None`` for the legacy all-time summary plus the last 90 days
    of daily points; ``7|30|90`` scopes the summary to that window while still
    returning consecutive daily points (missing days are zero-filled here).
    """
    from aistudio_api.api.response_models import AccountBreakdownItem
    from aistudio_api.api.state import (
        CountTokensStats,
        build_stats_payload,
        runtime_state,
    )
    from aistudio_api.domain.model_access import is_paid_model
    from aistudio_api.infrastructure.gateway.model_defaults import resolve_tier_quota

    sandbox = runtime_state.ui_sandbox if runtime_state.ui_debug else None
    source = getattr(sandbox, "model_stats", None)
    is_sandbox = isinstance(source, dict)
    raw_stats = source if is_sandbox else runtime_state.model_stats
    daily_source = getattr(sandbox, "daily_stats", None) if is_sandbox else None
    raw_daily = (
        daily_source if isinstance(daily_source, dict) else runtime_state.daily_stats
    )
    count_source = getattr(sandbox, "count_tokens", None) if is_sandbox else None
    count_stats = (
        count_source
        if isinstance(count_source, CountTokensStats)
        else runtime_state.count_tokens
    )
    rotator = runtime_state.rotator
    account_svc = runtime_state.account_service
    accounts_list = account_svc.list_accounts() if account_svc else []

    def _model_extra(name: str) -> dict[str, object]:
        clean_m = name.removeprefix("models/").lower()
        breakdown: list[AccountBreakdownItem] = []
        if rotator:
            from aistudio_api.application.account_rotator import (
                get_pacific_date_key,
                split_quota_key,
            )

            current_la = get_pacific_date_key()
            for acc in accounts_list:
                st = rotator.get_account_stats(acc.id)
                if st:
                    st._check_date_reset(current_la)
                    for quota_key_name, m_daily in st.daily_models.items():
                        surface, daily_model = split_quota_key(quota_key_name)
                        if daily_model != clean_m:
                            continue
                        quota = resolve_tier_quota(acc.tier, daily_model)
                        used_pct = round((m_daily.requests / max(1, quota)) * 100, 1)
                        cd_rem = (
                            max(0, int(m_daily.cooldown_until - time.time()))
                            if m_daily.cooldown_until > time.time()
                            else 0
                        )
                        breakdown.append(
                            AccountBreakdownItem(
                                account_id=acc.id,
                                name=acc.name,
                                auth_user=acc.auth_user,
                                email=acc.email,
                                tier=acc.tier,
                                group=acc.group,
                                surface=surface,
                                enabled=acc.enabled,
                                today_requests=m_daily.requests,
                                today_success=m_daily.success,
                                today_rate_limited=m_daily.rate_limited,
                                today_tokens=m_daily.total_tokens,
                                today_reasoning_tokens=m_daily.reasoning_tokens,
                                configured_quota=quota,
                                quota_used_percent=used_pct,
                                is_available=(
                                    acc.enabled
                                    and not acc.expired
                                    and cd_rem == 0
                                    and not st.session_expired
                                ),
                                cooldown_remaining=cd_rem,
                            )
                        )
        return {
            "paid": is_paid_model(name),
            "account_breakdown": breakdown,
        }

    payload = build_stats_payload(
        model_stats=raw_stats,
        daily_stats=raw_daily,
        count_tokens=count_stats,
        days=days,
        quota_scope=rotator.quota_scope if rotator else "account",
        model_extra=_model_extra,
    )
    return StatsResponse.model_validate(payload)


@public_router.get("/health", response_model=HealthResponse)
async def health():
    return health_response()


_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
_TAMPERMONKEY_SCRIPT_PATH = _TOOLS_DIR / "aistudio-cookie-grabber.user.js"


@public_router.get("/api/tools/aistudio-cookie-grabber.user.js")
@public_router.get("/tools/aistudio-cookie-grabber.user.js")
async def get_tampermonkey_script() -> Response:
    """提供油猴多账号同步脚本在线安装与更新。"""
    if not _TAMPERMONKEY_SCRIPT_PATH.is_file():
        raise HTTPException(status_code=404, detail="Tampermonkey script not found")
    script_text = _TAMPERMONKEY_SCRIPT_PATH.read_text(encoding="utf-8")
    return Response(
        content=script_text,
        media_type="text/javascript; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@protected_router.get("/stats", response_model=StatsResponse)
async def stats(days: int | None = Query(default=None, ge=1, le=90)):
    return stats_response(days)


@protected_router.get("/api/system/status")
async def system_status(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """Read-only runtime status used by the web console/debug menu."""
    account_service = runtime_state.account_service
    manager = runtime_state.instance_manager
    accounts = 0
    if account_service is not None:
        try:
            accounts = len(account_service.list_accounts())
        except Exception:
            accounts = 0
    instances = 0
    if manager is not None:
        try:
            instances = len(getattr(manager, "records", lambda: ())())
        except Exception:
            instances = 0
    engine_status: dict[str, object] = _sandbox_status(runtime_state)
    switcher = runtime_state.engine_switcher
    getter: Any = getattr(switcher, "status", None)
    if callable(getter):
        try:
            raw_engine_status = getter()
        except Exception as exc:
            logger.debug("engine status unavailable: %s", exc)
        else:
            if isinstance(raw_engine_status, dict):
                engine_status = {
                    str(key): value for key, value in raw_engine_status.items()
                }
    if not engine_status:
        settings_block = _sandbox_engine_settings(runtime_state) or (
            runtime_state.engine_settings or {}
        )
        engine_status = {
            "engine": str(settings_block.get("mode") or "browser"),
            "engine_phase": "idle",
            "engine_target": str(settings_block.get("mode") or "browser"),
        }
    return {
        "status": "ok",
        "busy": bool(getattr(runtime_state, "ui_sandbox", None) is not None),
        "ui_debug": bool(runtime_state.ui_debug),
        "ui_debug_scenario": _ui_debug_scenario(runtime_state),
        "accounts": accounts,
        "instances": instances,
        "formats": formats_status(runtime_state),
        "default_text_model": default_text_model(runtime_state),
        **engine_status,
    }


@protected_router.post("/api/engine/health-check")
async def engine_health_check(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """Probe the active engine runtime (WAA signer health for browserless)."""
    started = time.perf_counter()
    sandbox = runtime_state.ui_sandbox if runtime_state.ui_debug else None
    sandbox_probe: Any = getattr(sandbox, "engine_health", None)
    if callable(sandbox_probe):
        try:
            sandbox_payload: Any = sandbox_probe()
        except Exception as exc:
            return {
                "ok": False,
                "engine": "browserless",
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "workers": 0,
                "maxWorkers": 0,
                "version": "",
                "error": str(exc),
            }
        if isinstance(sandbox_payload, dict):
            payload = {str(key): value for key, value in sandbox_payload.items()}
            payload.setdefault(
                "elapsed_ms", round((time.perf_counter() - started) * 1000, 2)
            )
            payload.setdefault("error", None)
            return payload
    runtime = runtime_state.engine_runtime
    probe: Any = getattr(runtime, "health", None)
    if not callable(probe):
        mode = str((runtime_state.engine_settings or {}).get("mode") or "browser")
        return {
            "ok": mode == "browser",
            "engine": mode,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "workers": 0,
            "maxWorkers": 0,
            "version": "",
            "error": None if mode == "browser" else "engine runtime unavailable",
        }
    try:
        result: Any = await cast("Any", probe)()
    except Exception as exc:
        return {
            "ok": False,
            "engine": "browserless",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "workers": 0,
            "maxWorkers": 0,
            "version": "",
            "error": str(exc),
        }
    as_dict = getattr(result, "as_dict", None)
    raw_payload: Any = as_dict() if callable(as_dict) else {}
    payload: dict[str, object] = (
        {str(key): value for key, value in raw_payload.items()}
        if isinstance(raw_payload, dict)
        else {}
    )
    payload["engine"] = "browserless"
    payload["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    payload.setdefault("error", None)
    return payload


@protected_router.post("/config/waa/health-check")
async def waa_health_check_alias(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """Backwards/2.1-B friendly alias for the engine health probe."""
    return await engine_health_check(runtime_state)


# ========== 调度与配额管理 API ==========


class ClearCooldownRequest(BaseModel):
    account_id: str | None = None
    model: str | None = None


@protected_router.get("/rotation")
async def get_rotation_status(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """获取账号黏性调度与配额状态。"""
    rotator = runtime_state.rotator
    if rotator is None:
        return {
            "enabled": False,
            "mode": "sticky",
            "message": "调度器未初始化",
            "accounts": {},
            "ui_debug": bool(runtime_state.ui_debug),
            "ui_debug_scenario": _ui_debug_scenario(runtime_state),
        }

    return {
        "enabled": True,
        "mode": "sticky",
        "quota_scope": rotator.quota_scope,
        "accounts": rotator.get_all_stats(),
        "ui_debug": bool(runtime_state.ui_debug),
        "ui_debug_scenario": _ui_debug_scenario(runtime_state),
    }


@protected_router.post("/rotation/clear-cooldown")
async def clear_cooldown(
    req: ClearCooldownRequest,
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """清除指定账号或全局的 429 锁定状态。"""
    rotator = runtime_state.rotator
    if rotator is None:
        raise HTTPException(503, detail="调度器未初始化")

    if req.account_id:
        rotator.clear_cooldown(req.account_id, model=req.model)
        service = runtime_state.account_service
        store = getattr(service, "store", None)
        if (
            store is not None
            and service is not None
            and service.get_account(req.account_id) is not None
        ):
            store.update_account_refresh(req.account_id, expired=False)
    else:
        rotator.clear_all_cooldowns()
    logger.info(
        "已清除冷却状态: 账号=%s, 模型=%s",
        req.account_id or "全部",
        req.model or "全部",
    )

    return {"ok": True, "accounts": rotator.get_all_stats()}


@protected_router.post("/rotation/cleanup-ghosts")
async def cleanup_ghosts(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """主动对齐并彻底清除已删除账号在调度器中残留的幽灵状态。"""
    rotator = runtime_state.rotator
    if rotator is None:
        raise HTTPException(503, detail="调度器未初始化")

    cleaned_ids = rotator.cleanup_ghost_accounts()
    return {
        "ok": True,
        "cleaned_count": len(cleaned_ids),
        "cleaned_ids": cleaned_ids,
        "accounts": rotator.get_all_stats(),
    }


@protected_router.get("/rotation/accounts")
async def get_rotation_accounts(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, dict[str, object]]:
    """获取所有账号的调度与配额统计。"""
    rotator = runtime_state.rotator
    if rotator is None:
        raise HTTPException(503, detail="调度器未初始化")

    return rotator.get_all_stats()


@protected_router.post("/rotation/next")
async def force_next_account(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """强制切换到下一个可用账号。"""
    sandbox = runtime_state.ui_sandbox
    if runtime_state.ui_debug and sandbox is not None:
        next_account = getattr(sandbox, "next_account", None)
        if not callable(next_account):
            raise HTTPException(404, detail="没有可用的账号")
        account = cast(Any, next_account)()
        if account is None:
            raise HTTPException(404, detail="没有可用的账号")
        logger.info("UI 调试沙箱强制切换账号: id=%s", account.id)
        return {
            "ok": True,
            "account": {
                "id": account.id,
                "name": account.name,
                "email": account.email,
            },
            "ui_debug": True,
        }

    rotator = runtime_state.rotator
    if rotator is None:
        raise HTTPException(503, detail="调度器未初始化")

    next_account = await rotator.get_next_account()
    if next_account is None:
        raise HTTPException(404, detail="没有可用的账号")

    account_service = runtime_state.account_service
    client = runtime_state.client

    if account_service is None or client is None or client._session is None:
        raise HTTPException(503, detail="服务未就绪")

    result = await account_service.activate_account(
        next_account.id,
        client._session,
    )

    if result is None:
        raise HTTPException(500, detail="切换失败")

    logger.info("强制切换到下一个账号: id=%s, name=%s", result.id, result.name)
    return {
        "ok": True,
        "account": {
            "id": result.id,
            "name": result.name,
            "email": result.email,
        },
    }


# ========== 系统与模型配置 API ==========


class ConfigYamlUpdateRequest(BaseModel):
    yaml_content: str


def _sandbox_status(runtime_state: RuntimeState) -> dict[str, object]:
    sandbox = runtime_state.ui_sandbox if runtime_state.ui_debug else None
    getter = getattr(sandbox, "engine_status", None)
    if not callable(getter):
        return {}
    try:
        raw_status = getter()
    except Exception as exc:
        logger.debug("sandbox engine status unavailable: %s", exc)
        return {}
    if not isinstance(raw_status, dict):
        return {}
    return {str(key): value for key, value in raw_status.items()}


def _sandbox_engine_settings(runtime_state: RuntimeState) -> dict[str, object] | None:
    sandbox = runtime_state.ui_sandbox if runtime_state.ui_debug else None
    getter = getattr(sandbox, "engine_settings", None)
    if not callable(getter):
        return None
    try:
        raw_settings = getter()
    except Exception as exc:
        logger.debug("sandbox engine settings unavailable: %s", exc)
        return None
    return raw_settings if isinstance(raw_settings, dict) else None


def _sync_sandbox_engine(
    runtime_state: RuntimeState, target_engine: dict[str, object]
) -> dict[str, object] | None:
    """Apply the simulated switch after a successful sandbox config write."""
    sandbox = runtime_state.ui_sandbox if runtime_state.ui_debug else None
    setter = getattr(sandbox, "set_engine", None)
    if not callable(setter):
        return None
    try:
        result = setter(target_engine)
    except Exception as exc:
        logger.warning("sandbox engine switch simulation failed: %s", exc)
        return None
    return result if isinstance(result, dict) else None


def _relax_sandbox_engine(config: dict[str, object]) -> bool:
    """Offline sandbox must switch engines without a real signer binary.

    Only the local subprocess signer requires ``binary_path`` to exist.  When
    ui_debug is on and the binary is absent, disable auto-start in the sandbox
    copy so validation and the simulated switch still succeed.  Nothing real is
    ever launched.
    """
    engine = config.get("engine")
    if not isinstance(engine, dict):
        return False
    browserless = engine.get("browserless")
    if not isinstance(browserless, dict):
        return False
    signer = browserless.get("signer")
    if not isinstance(signer, dict):
        return False
    if str(signer.get("mode") or "local_subprocess") != "local_subprocess":
        return False
    if signer.get("auto_start") is not True:
        return False
    from aistudio_api.infrastructure.waa.local_daemon import resolve_binary_path

    raw_path = str(signer.get("binary_path") or "waa-signer/bin/waa-signer")
    try:
        binary = resolve_binary_path(raw_path)
    except Exception:
        binary = None
    if binary is not None and binary.is_file():
        return False
    signer["auto_start"] = False
    return True


def _engine_settings_for_mapping(parsed: dict[str, object]) -> dict[str, object]:
    """Validate the engine block of a merged config document (422 on failure)."""
    raw_instances = parsed.get("instances")
    specs: list[dict[str, object]] = []
    if isinstance(raw_instances, list):
        for item in raw_instances:
            if isinstance(item, dict):
                try:
                    specs.append({"count": max(1, int(item.get("count", 1)))})
                except (TypeError, ValueError):
                    specs.append({"count": 1})
    raw_max = parsed.get("max_contexts")
    max_contexts = (
        int(raw_max)
        if isinstance(raw_max, int) and not isinstance(raw_max, bool) and raw_max > 0
        else None
    )
    return validate_engine_settings(
        parsed.get("engine"), specs=specs, max_contexts=max_contexts
    )


async def _run_engine_switch(
    runtime_state: RuntimeState,
    *,
    target_engine: dict[str, object],
    target_config_text: str,
) -> dict[str, object] | None:
    """Apply the transactional engine switch; ``None`` means plain write.

    Returns the switch result when a switch actually ran (the config has already
    been atomically written by the commit step), or ``None`` for ui-debug /
    unchanged-engine paths where the caller writes the config normally.
    """
    switcher = runtime_state.engine_switcher
    if switcher is None:
        return None
    current = getattr(switcher, "current_settings", None)
    if not isinstance(current, dict):
        current = runtime_state.engine_settings or {}
    if engine_settings_hash(current) == engine_settings_hash(target_engine):
        return None
    from aistudio_api.application.engine_switch import (
        EngineSwitchConflictError,
        EngineSwitchError,
    )

    try:
        result = await switcher.switch(  # type: ignore[attr-defined]
            target_engine, target_config_text=target_config_text
        )
    except EngineSwitchConflictError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except EngineSwitchError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc
    except ConfigValidationError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return {
        "switched": bool(getattr(result, "switched", False)),
        "engine": getattr(result, "engine", target_engine.get("mode")),
        "engine_epoch": getattr(result, "epoch", 0),
        "rebuilt": getattr(result, "rebuilt", 0),
        "total": getattr(result, "total", 0),
    }


def _apply_logging_runtime(parsed: object) -> None:
    """Apply the persisted logging block to the live logger runtime."""
    if not isinstance(parsed, dict) or "logging" not in parsed:
        return
    normalized_logging = get_configured_logging_settings()
    set_log_level(str(normalized_logging.get("level", "INFO")))
    set_dump_requests(bool(normalized_logging.get("dump_requests", False)))
    set_dump_to_file(bool(normalized_logging.get("dump_to_file", False)))
    set_dump_dir(str(normalized_logging.get("dump_dir", "dumps")))


@protected_router.get("/config")
async def get_system_config(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """获取系统运行配置与 config.yaml 内容。"""
    from aistudio_api.config import settings
    from aistudio_api.infrastructure.utils.logger import (
        get_dump_dir,
        get_log_level,
        is_debug_env_active,
        is_dump_requests_enabled,
        is_dump_to_file_enabled,
    )

    config_yaml_path = _resolve_config_path(None)
    yaml_content = ""
    if config_yaml_path.exists():
        with contextlib.suppress(Exception):
            yaml_content = config_yaml_path.read_text(encoding="utf-8")
    engine_settings = _sandbox_engine_settings(runtime_state) or (
        runtime_state.engine_settings or {}
    )
    return {
        "port": settings.port,
        "browser_port": settings.browser_port,
        "browser_headless": settings.browser_headless,
        "engine": str(engine_settings.get("mode") or "browser"),
        "engine_settings": engine_settings,
        "proxy_configured": bool(settings.proxy_url),
        "auth_enabled": settings.auth_enabled,
        "yaml_content": yaml_content,
        "log_level": get_log_level(),
        "dump_requests": is_dump_requests_enabled(),
        "debug_env_active": is_debug_env_active(),
        "dump_to_file": is_dump_to_file_enabled(),
        "dump_dir": str(get_dump_dir()),
        "ui_debug": bool(runtime_state.ui_debug),
        "ui_debug_scenario": _ui_debug_scenario(runtime_state),
    }


@protected_router.put("/config/yaml")
async def update_config_yaml(
    req: ConfigYamlUpdateRequest,
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """更新 config.yaml 文件内容并热重载默认配置。"""

    try:
        parsed = yaml.safe_load(req.yaml_content)
        if parsed is not None and not isinstance(parsed, dict):
            raise HTTPException(400, detail="YAML 顶层必须为字典结构")
    except yaml.YAMLError as e:
        raise HTTPException(400, detail=f"YAML 语法格式错误: {e}") from e
    config_text = req.yaml_content
    merged_for_engine_raw = merge_config_documents(_default_config(), parsed or {})
    merged_for_engine = (
        cast("dict[str, object]", merged_for_engine_raw)
        if isinstance(merged_for_engine_raw, dict)
        else {}
    )
    if runtime_state.ui_debug and _relax_sandbox_engine(merged_for_engine):
        relaxed = dict(parsed or {})
        engine_block = merged_for_engine.get("engine")
        if isinstance(engine_block, dict):
            relaxed["engine"] = engine_block
            config_text = yaml.dump(relaxed, allow_unicode=True, sort_keys=False)
    try:
        target_engine = _engine_settings_for_mapping(merged_for_engine)
    except ConfigValidationError as e:
        raise HTTPException(422, detail=str(e)) from e
    switcher = runtime_state.engine_switcher
    current_engine = getattr(switcher, "current_settings", None)
    engine_changes = (
        switcher is not None
        and isinstance(current_engine, dict)
        and engine_settings_hash(current_engine) != engine_settings_hash(target_engine)
    )
    if engine_changes:
        try:
            validate_config_mapping(parsed or {})
        except ConfigValidationError as e:
            raise HTTPException(400, detail=str(e)) from e
    switched = await _run_engine_switch(
        runtime_state,
        target_engine=target_engine,
        target_config_text=config_text,
    )
    if switched is None:
        try:
            write_config_text(config_text)
        except ConfigValidationError as e:
            raise HTTPException(400, detail=str(e)) from e
        except OSError as e:
            raise HTTPException(500, detail=f"写入配置文件失败: {e}") from e
        _sync_sandbox_engine(runtime_state, target_engine)

    try:
        _apply_logging_runtime(parsed)
    except Exception as e:
        raise HTTPException(500, detail=f"应用日志配置失败: {e}") from e
    logger.info("配置文件 config.yaml 已更新并重新加载")
    payload: dict[str, object] = {"ok": True, "message": "配置已保存并重载"}
    if switched is not None:
        payload["engine_switch"] = switched
    return payload


class ConfigPatchRequest(BaseModel):
    updates: dict[str, object]


@protected_router.patch("/config")
async def patch_config(
    req: ConfigPatchRequest,
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """按 section 局部更新 config.yaml，保留未提交键（含 D-09 诊断开关）。

    前端按卡片提交局部对象，例如 ``{"updates": {"logging": {...}}}``；服务端读取
    当前最新磁盘 YAML（ui_debug 下为沙盒临时路径）后递归合并，列表整体替换，
    再走 ``validate_config_mapping`` 校验与原子写回。未提交的顶层/嵌套键原样保留。
    """
    config_path = _resolve_config_path(None)
    try:
        content = (
            config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        )
    except OSError as e:
        raise HTTPException(500, detail=f"读取配置文件失败: {e}") from e

    try:
        parsed = yaml.safe_load(content) or {}
    except yaml.YAMLError as e:
        raise HTTPException(400, detail=f"YAML 语法格式错误: {e}") from e
    if not isinstance(parsed, dict):
        raise HTTPException(400, detail="YAML 顶层必须为字典结构")

    merged = merge_config_documents(parsed, req.updates)
    if not isinstance(merged, dict):
        raise HTTPException(400, detail="配置合并结果必须为字典结构")
    if runtime_state.ui_debug:
        _relax_sandbox_engine(merged)

    target_text = yaml.dump(merged, allow_unicode=True, sort_keys=False)
    try:
        target_engine = _engine_settings_for_mapping(merged)
    except ConfigValidationError as e:
        raise HTTPException(422, detail=str(e)) from e
    switcher = runtime_state.engine_switcher
    current_engine = getattr(switcher, "current_settings", None)
    engine_changes = (
        switcher is not None
        and isinstance(current_engine, dict)
        and engine_settings_hash(current_engine) != engine_settings_hash(target_engine)
    )
    if engine_changes:
        try:
            validate_config_mapping(merged)
        except ConfigValidationError as e:
            raise HTTPException(400, detail=str(e)) from e

    switched = await _run_engine_switch(
        runtime_state,
        target_engine=target_engine,
        target_config_text=target_text,
    )
    if switched is None:
        try:
            write_config_text(target_text, config_path=config_path)
        except ConfigValidationError as e:
            raise HTTPException(400, detail=str(e)) from e
        except OSError as e:
            raise HTTPException(500, detail=f"写入配置文件失败: {e}") from e
        _sync_sandbox_engine(runtime_state, target_engine)

    try:
        _apply_logging_runtime(merged)
    except Exception as e:
        raise HTTPException(500, detail=f"应用日志配置失败: {e}") from e

    logger.info("配置已局部更新: %s", ", ".join(sorted(req.updates)))
    payload = await get_system_config(runtime_state)
    payload["ok"] = True
    payload["message"] = "配置已保存并热重载"
    if switched is not None:
        payload["engine_switch"] = switched
    return payload


@protected_router.post("/config/reset")
async def reset_config(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """用官方 defaults/config.yaml 覆盖当前配置，保留已创建的 API Key。

    - 读取 ``defaults/config.yaml`` 模板（值语义，不继承用户改动）
    - 保留当前磁盘配置中的 ``api_keys``（已创建的客户端密钥不丢）
    - ui_debug 沙盒下强制 ``diagnostics.ui_debug: true``，且只写临时配置
    - 校验 + 原子写回 + 刷新配置缓存，返回最新完整 SystemConfig
    """
    config_path = _resolve_config_path(None)
    try:
        template_path = resolve_default_config_template()
        template_text = template_path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise HTTPException(500, detail=f"默认配置模板不存在: {e}") from e
    except OSError as e:
        raise HTTPException(500, detail=f"读取默认配置模板失败: {e}") from e

    try:
        template = yaml.safe_load(template_text) or {}
    except yaml.YAMLError as e:
        raise HTTPException(500, detail=f"默认配置模板 YAML 解析失败: {e}") from e
    if not isinstance(template, dict):
        raise HTTPException(500, detail="默认配置模板顶层必须为字典结构")

    preserved_keys: object = []
    if config_path.exists():
        try:
            current = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            current = {}
        if isinstance(current, dict) and isinstance(current.get("api_keys"), list):
            preserved_keys = current["api_keys"]
    template["api_keys"] = preserved_keys

    if runtime_state.ui_debug:
        diagnostics = template.get("diagnostics")
        diagnostics_map = dict(diagnostics) if isinstance(diagnostics, dict) else {}
        diagnostics_map["ui_debug"] = True
        template["diagnostics"] = diagnostics_map
        _relax_sandbox_engine(template)

    target_text = yaml.dump(template, allow_unicode=True, sort_keys=False)
    try:
        target_engine = _engine_settings_for_mapping(template)
    except ConfigValidationError as e:
        raise HTTPException(422, detail=str(e)) from e
    switcher = runtime_state.engine_switcher
    current_engine = getattr(switcher, "current_settings", None)
    if (
        switcher is not None
        and isinstance(current_engine, dict)
        and engine_settings_hash(current_engine) != engine_settings_hash(target_engine)
    ):
        try:
            validate_config_mapping(template)
        except ConfigValidationError as e:
            raise HTTPException(400, detail=str(e)) from e

    switched = await _run_engine_switch(
        runtime_state,
        target_engine=target_engine,
        target_config_text=target_text,
    )
    if switched is None:
        try:
            write_config_text(target_text, config_path=config_path)
        except ConfigValidationError as e:
            raise HTTPException(400, detail=str(e)) from e
        except OSError as e:
            raise HTTPException(500, detail=f"写入配置文件失败: {e}") from e
        _sync_sandbox_engine(runtime_state, target_engine)

    try:
        _apply_logging_runtime(template)
    except Exception as e:
        raise HTTPException(500, detail=f"应用日志配置失败: {e}") from e

    logger.info("配置已重置为 defaults/config.yaml 默认值")
    payload = await get_system_config(runtime_state)
    payload["ok"] = True
    payload["message"] = "已重置为官方默认配置并热重载"
    if switched is not None:
        payload["engine_switch"] = switched
    return payload


class LoggingConfigRequest(BaseModel):
    level: str | None = None
    dump_requests: bool | None = None
    dump_to_file: bool | None = None
    dump_dir: str | None = None


@protected_router.put("/config/logging")
async def update_logging_config(req: LoggingConfigRequest) -> dict[str, object]:
    """更新系统日志级别与请求转储配置，并持久化到 config.yaml。"""
    from aistudio_api.infrastructure.gateway.model_defaults import (
        update_configured_logging_settings,
    )
    from aistudio_api.infrastructure.utils.logger import (
        get_dump_dir,
        get_log_level,
        is_debug_env_active,
        is_dump_requests_enabled,
        is_dump_to_file_enabled,
        set_dump_dir,
        set_dump_requests,
        set_dump_to_file,
        set_log_level,
    )

    if req.level is not None:
        set_log_level(req.level)
    if req.dump_requests is not None:
        set_dump_requests(req.dump_requests)
    if req.dump_to_file is not None:
        set_dump_to_file(req.dump_to_file)
    if req.dump_dir is not None:
        set_dump_dir(req.dump_dir)

    update_configured_logging_settings(
        level=req.level,
        dump_requests=req.dump_requests,
        dump_to_file=req.dump_to_file,
        dump_dir=req.dump_dir,
    )
    logger.info(
        "日志配置已更新: 级别=%s, 请求转储=%s, 文件转储=%s, 目录=%s",
        req.level,
        req.dump_requests,
        req.dump_to_file,
        req.dump_dir,
    )

    return {
        "ok": True,
        "log_level": get_log_level(),
        "dump_requests": is_dump_requests_enabled(),
        "debug_env_active": is_debug_env_active(),
        "dump_to_file": is_dump_to_file_enabled(),
        "dump_dir": str(get_dump_dir()),
    }


# ========== API Key 备注与密钥管理 ==========


class ApiKeyItemModel(BaseModel):
    name: str = "API Key"
    key: str = ""
    created_at: str | None = None


class CreateApiKeyRequest(BaseModel):
    name: str = "API Key"
    key: str | None = None


class UpdateApiKeyRequest(BaseModel):
    name: str


@protected_router.get("/api-keys", response_model=list[ApiKeyItemModel])
async def list_api_keys() -> list[ApiKeyItemModel]:
    """获取 config.yaml 中配置的 API Keys 列表。"""
    from aistudio_api.infrastructure.gateway.model_defaults import (
        get_configured_api_key_items,
    )

    items = get_configured_api_key_items()
    return [
        ApiKeyItemModel(
            name=item.get("name") or "API Key",
            key=item.get("key") or "",
            created_at=item.get("created_at") or None,
        )
        for item in items
    ]


@protected_router.post("/api-keys", response_model=ApiKeyItemModel)
async def create_api_key(req: CreateApiKeyRequest) -> ApiKeyItemModel:
    """在 config.yaml 中添加新的 API Key。"""
    import secrets
    from datetime import UTC, datetime

    new_key = (
        req.key.strip()
        if req.key and req.key.strip()
        else f"sk-aistudio-{secrets.token_hex(16)}"
    )
    created_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    name = req.name.strip() if req.name.strip() else "API Key"

    config_path = _resolve_config_path(None)
    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    parsed = yaml.safe_load(content) or {}
    if not isinstance(parsed, dict):
        parsed = {}

    raw_keys = parsed.get("api_keys")
    new_list: list[dict[str, str]] = []
    if isinstance(raw_keys, list):
        for item in raw_keys:
            if isinstance(item, dict):
                new_list.append(
                    {
                        "name": str(item.get("name") or "API Key"),
                        "key": str(item.get("key") or ""),
                        "created_at": str(item.get("created_at") or ""),
                    }
                )
            elif isinstance(item, str) and item.strip():
                new_list.append(
                    {"name": "API Key", "key": item.strip(), "created_at": ""}
                )

    new_list.append({"name": name, "key": new_key, "created_at": created_at})
    parsed["api_keys"] = new_list

    try:
        write_config_text(
            yaml.dump(parsed, allow_unicode=True, sort_keys=False),
            config_path=config_path,
        )
    except ConfigValidationError as e:
        raise HTTPException(400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(500, detail=f"写入配置文件失败: {e}") from e

    logger.info("创建 API Key: 备注=%s, 密钥=***", name)
    return ApiKeyItemModel(name=name, key=new_key, created_at=created_at)


@protected_router.delete("/api-keys/{key_value:path}")
async def delete_api_key(key_value: str) -> dict[str, bool]:
    """在 config.yaml 中删除指定 API Key。"""

    config_path = _resolve_config_path(None)
    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    parsed = yaml.safe_load(content) or {}
    if not isinstance(parsed, dict):
        raise HTTPException(404, detail="未找到 API Key")

    raw_keys = parsed.get("api_keys")
    new_list: list[dict[str, str]] = []
    found = False
    if isinstance(raw_keys, list):
        for item in raw_keys:
            item_key = str(item.get("key") if isinstance(item, dict) else item).strip()
            if item_key == key_value:
                found = True
                continue
            if isinstance(item, dict):
                new_list.append(
                    {
                        "name": str(item.get("name") or "API Key"),
                        "key": item_key,
                        "created_at": str(item.get("created_at") or ""),
                    }
                )
            elif item_key:
                new_list.append({"name": "API Key", "key": item_key, "created_at": ""})

    if not found:
        raise HTTPException(404, detail="未找到该 API Key")

    parsed["api_keys"] = new_list
    try:
        write_config_text(
            yaml.dump(parsed, allow_unicode=True, sort_keys=False),
            config_path=config_path,
        )
    except ConfigValidationError as e:
        raise HTTPException(400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(500, detail=f"写入配置文件失败: {e}") from e

    logger.info("删除 API Key: 密钥=***")
    return {"ok": True}


@protected_router.put("/api-keys/{key_value:path}", response_model=ApiKeyItemModel)
async def update_api_key_name(
    key_value: str, req: UpdateApiKeyRequest
) -> ApiKeyItemModel:
    """在 config.yaml 中更新指定 API Key 的备注名。"""

    config_path = _resolve_config_path(None)
    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    parsed = yaml.safe_load(content) or {}
    if not isinstance(parsed, dict):
        raise HTTPException(404, detail="未找到 API Key")

    raw_keys = parsed.get("api_keys")
    new_list: list[dict[str, str]] = []
    updated_item: ApiKeyItemModel | None = None
    new_name = req.name.strip() if req.name.strip() else "API Key"

    if isinstance(raw_keys, list):
        for item in raw_keys:
            item_key = str(item.get("key") if isinstance(item, dict) else item).strip()
            created = str(item.get("created_at") if isinstance(item, dict) else "")
            if item_key == key_value:
                updated_item = ApiKeyItemModel(
                    name=new_name, key=item_key, created_at=created or None
                )
                new_list.append(
                    {"name": new_name, "key": item_key, "created_at": created}
                )
            else:
                name_val = str(
                    item.get("name") if isinstance(item, dict) else "API Key"
                )
                new_list.append(
                    {"name": name_val, "key": item_key, "created_at": created}
                )

    if not updated_item:
        raise HTTPException(404, detail="未找到该 API Key")

    parsed["api_keys"] = new_list
    try:
        write_config_text(
            yaml.dump(parsed, allow_unicode=True, sort_keys=False),
            config_path=config_path,
        )
    except ConfigValidationError as e:
        raise HTTPException(400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(500, detail=f"写入配置文件失败: {e}") from e
    logger.info("更新 API Key 备注: 密钥=***, 备注=%s", new_name)

    return updated_item
