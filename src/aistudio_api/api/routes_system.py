"""System and metadata routes."""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING, Any, cast

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aistudio_api.api.dependencies import get_runtime_state
from aistudio_api.api.response_models import (
    HealthResponse,
    ModelStatsResponse,
    StatsResponse,
    StatsTotalsResponse,
)
from aistudio_api.config import resolve_default_config_template
from aistudio_api.infrastructure.gateway.model_defaults import (
    ConfigValidationError,
    _resolve_config_path,
    get_configured_logging_settings,
    merge_config_documents,
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


def stats_response() -> StatsResponse:
    from aistudio_api.api.response_models import AccountBreakdownItem
    from aistudio_api.api.state import runtime_state
    from aistudio_api.domain.model_access import is_paid_model
    from aistudio_api.infrastructure.gateway.model_defaults import resolve_tier_quota

    stats = dict(runtime_state.model_stats)
    rotator = runtime_state.rotator
    account_svc = runtime_state.account_service
    accounts_list = account_svc.list_accounts() if account_svc else []

    totals = StatsTotalsResponse(
        requests=sum(s.requests for s in stats.values()),
        success=sum(s.success for s in stats.values()),
        rate_limited=sum(s.rate_limited for s in stats.values()),
        errors=sum(s.errors for s in stats.values()),
        prompt_tokens=sum(s.prompt_tokens for s in stats.values()),
        completion_tokens=sum(s.completion_tokens for s in stats.values()),
        reasoning_tokens=sum(s.reasoning_tokens for s in stats.values()),
        cached_tokens=sum(s.cached_tokens for s in stats.values()),
        total_tokens=sum(s.total_tokens for s in stats.values()),
    )

    models = {}
    for name, s in stats.items():
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
                        used_pct = round(
                            (m_daily.requests / max(1, quota)) * 100, 1
                        )
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

        models[name] = ModelStatsResponse(
            requests=s.requests,
            success=s.success,
            rate_limited=s.rate_limited,
            errors=s.errors,
            prompt_tokens=s.prompt_tokens,
            completion_tokens=s.completion_tokens,
            reasoning_tokens=s.reasoning_tokens,
            cached_tokens=s.cached_tokens,
            total_tokens=s.total_tokens,
            finish_reasons=dict(s.finish_reasons),
            last_used=s.last_used,
            paid=is_paid_model(name),
            account_breakdown=breakdown,
        )
    return StatsResponse(
        models=models,
        totals=totals,
        quota_scope=rotator.quota_scope if rotator else "account",
    )


@public_router.get("/health", response_model=HealthResponse)
async def health():
    return health_response()


@protected_router.get("/stats", response_model=StatsResponse)
async def stats():
    return stats_response()


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
    return {
        "status": "ok",
        "busy": bool(getattr(runtime_state, "ui_sandbox", None) is not None),
        "ui_debug": bool(runtime_state.ui_debug),
        "ui_debug_scenario": _ui_debug_scenario(runtime_state),
        "accounts": accounts,
        "instances": instances,
    }


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
    logger.info("已清除冷却状态: 账号=%s, 模型=%s", req.account_id or "全部", req.model or "全部")

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
    return {
        "port": settings.port,
        "browser_port": settings.browser_port,
        "browser_headless": settings.browser_headless,
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
async def update_config_yaml(req: ConfigYamlUpdateRequest) -> dict[str, object]:
    """更新 config.yaml 文件内容并热重载默认配置。"""

    try:
        parsed = yaml.safe_load(req.yaml_content)
        if parsed is not None and not isinstance(parsed, dict):
            raise HTTPException(400, detail="YAML 顶层必须为字典结构")
    except yaml.YAMLError as e:
        raise HTTPException(400, detail=f"YAML 语法格式错误: {e}") from e
    try:
        write_config_text(req.yaml_content)
    except ConfigValidationError as e:
        raise HTTPException(400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(500, detail=f"写入配置文件失败: {e}") from e

    try:
        _apply_logging_runtime(parsed)
    except Exception as e:
        raise HTTPException(500, detail=f"应用日志配置失败: {e}") from e
    logger.info("配置文件 config.yaml 已更新并重新加载")
    return {"ok": True, "message": "配置已保存并重载"}


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

    try:
        write_config_text(
            yaml.dump(merged, allow_unicode=True, sort_keys=False),
            config_path=config_path,
        )
    except ConfigValidationError as e:
        raise HTTPException(400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(500, detail=f"写入配置文件失败: {e}") from e

    try:
        _apply_logging_runtime(merged)
    except Exception as e:
        raise HTTPException(500, detail=f"应用日志配置失败: {e}") from e

    logger.info("配置已局部更新: %s", ", ".join(sorted(req.updates)))
    payload = await get_system_config(runtime_state)
    payload["ok"] = True
    payload["message"] = "配置已保存并热重载"
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

    try:
        write_config_text(
            yaml.dump(template, allow_unicode=True, sort_keys=False),
            config_path=config_path,
        )
    except ConfigValidationError as e:
        raise HTTPException(400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(500, detail=f"写入配置文件失败: {e}") from e

    try:
        _apply_logging_runtime(template)
    except Exception as e:
        raise HTTPException(500, detail=f"应用日志配置失败: {e}") from e

    logger.info("配置已重置为 defaults/config.yaml 默认值")
    payload = await get_system_config(runtime_state)
    payload["ok"] = True
    payload["message"] = "已重置为官方默认配置并热重载"
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
