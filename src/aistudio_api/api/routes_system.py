"""System and metadata routes."""

from __future__ import annotations

import contextlib
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from aistudio_api.api.dependencies import get_runtime_state
from aistudio_api.api.response_models import (
    HealthResponse,
    ModelStatsResponse,
    StatsBucketResponse,
    StatsRangeResponse,
    StatsResponse,
    StatsTimeseriesPoint,
    StatsTimeseriesResponse,
)
from aistudio_api.api.state import (
    STATS_SCHEMA_VERSION,
    STATS_TIMEZONE,
    ModelStatsItem,
    StatsCounters,
    normalize_model_name,
)
from aistudio_api.infrastructure.gateway.model_defaults import (
    _resolve_config_path,
    get_features_config,
    invalidate_config_cache,
    update_configured_features,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("routes.system")

if TYPE_CHECKING:
    from aistudio_api.api.state import RuntimeState
    from aistudio_api.infrastructure.gateway.session import BrowserSession

public_router = APIRouter()
protected_router = APIRouter()


def health_response() -> HealthResponse:
    return HealthResponse(status="ok", busy=False)


def _range_response(info: dict[str, object]) -> StatsRangeResponse:
    def _str(key: str, default: str | None = None) -> str | None:
        value = info.get(key)
        return value if isinstance(value, str) else default

    retention = info.get("retention_days")
    return StatsRangeResponse(
        key=_str("key", "all") or "all",
        start=_str("start"),
        end=_str("end"),
        timezone=_str("timezone", "Asia/Shanghai") or "Asia/Shanghai",
        data_start=_str("data_start"),
        retention_days=retention if isinstance(retention, int) else 90,
        partial=bool(info.get("partial")),
    )


def _bucket_response(counter: StatsCounters) -> StatsBucketResponse:
    return StatsBucketResponse(
        requests=counter.requests,
        attempts=counter.attempts,
        success=counter.success,
        rate_limited=counter.rate_limited,
        errors=counter.errors,
        client_requests=counter.client_requests,
        client_success=counter.client_success,
        client_errors=counter.client_errors,
        client_rate_limited=counter.client_rate_limited,
        client_aborted=counter.client_aborted,
        prompt_tokens=counter.prompt_tokens,
        completion_tokens=counter.completion_tokens,
        reasoning_tokens=counter.reasoning_tokens,
        cache_read_tokens=counter.cache_read_tokens,
        cache_write_tokens=counter.cache_write_tokens,
        total_tokens=counter.total_tokens,
        cache_hit_rate=counter.cache_hit_rate,
        count_requests=counter.count_requests,
        count_success=counter.count_success,
        count_errors=counter.count_errors,
        count_input_tokens=counter.count_input_tokens,
        usage_missing=counter.usage_missing,
        last_used=counter.last_used,
    )


def _model_response(
    counter: StatsCounters, *, paid: bool | None = None
) -> ModelStatsResponse:
    if isinstance(counter, ModelStatsItem):
        formats = {
            name: _bucket_response(bucket) for name, bucket in counter.formats.items()
        }
        surfaces = {
            name: _bucket_response(bucket) for name, bucket in counter.surfaces.items()
        }
    else:
        formats = {}
        surfaces = {}
    return ModelStatsResponse(
        **_bucket_response(counter).model_dump(),
        paid=paid,
        formats=formats,
        surfaces=surfaces,
    )


async def _paid_map(session: BrowserSession | None = None) -> dict[str, bool]:
    try:
        from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

        discovered = await model_discovery.get_models(session=session)
    except Exception:
        return {}
    result: dict[str, bool] = {}
    for item in discovered:
        name = normalize_model_name(item.get("id") or item.get("name"))
        value = item.get("paid")
        if name and isinstance(value, bool):
            result[name] = value
    return result


async def stats_response(
    *,
    range_key: str = "all",
    start: str | None = None,
    end: str | None = None,
    session: BrowserSession | None = None,
) -> StatsResponse:
    from aistudio_api.api.state import runtime_state

    try:
        models, range_info = runtime_state.stats_for_range(range_key, start, end)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    paid = await _paid_map(session)
    totals = ModelStatsItem()
    for item in models.values():
        totals.add(item)
    return StatsResponse(
        schema_version=STATS_SCHEMA_VERSION,
        generated_at=datetime.now(STATS_TIMEZONE).isoformat(timespec="seconds"),
        range=_range_response(range_info),
        models={
            name: _model_response(item, paid=paid.get(name))
            for name, item in models.items()
        },
        totals=_model_response(totals),
    )


@public_router.get("/health", response_model=HealthResponse)
async def health():
    return health_response()


@protected_router.get("/api/stats", response_model=StatsResponse)
async def stats(
    range_key: str = Query("all", alias="range"),
    start: str | None = None,
    end: str | None = None,
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> StatsResponse:
    session = runtime_state.client._session if runtime_state.client else None
    return await stats_response(
        range_key=range_key, start=start, end=end, session=session
    )


@protected_router.get("/api/stats/timeseries", response_model=StatsTimeseriesResponse)
async def stats_timeseries(
    range_key: str = Query("7d", alias="range"),
    start: str | None = None,
    end: str | None = None,
    bucket: str = "day",
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> StatsTimeseriesResponse:
    if bucket != "day":
        raise HTTPException(400, detail="only bucket=day is supported")
    try:
        _, range_info = runtime_state.stats_for_range(range_key, start, end)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    points: list[StatsTimeseriesPoint] = []
    raw_start = range_info.get("start")
    raw_end = range_info.get("end")
    if isinstance(raw_start, str) and isinstance(raw_end, str):
        cursor = date.fromisoformat(raw_start)
        final = date.fromisoformat(raw_end)
        while cursor <= final:
            day_models = runtime_state.daily_stats.get(cursor.isoformat())
            totals = ModelStatsItem()
            if day_models:
                for item in day_models.values():
                    totals.add(item)
            points.append(
                StatsTimeseriesPoint(
                    date=cursor.isoformat(),
                    totals=_bucket_response(totals) if day_models else None,
                )
            )
            cursor += timedelta(days=1)
    return StatsTimeseriesResponse(
        generated_at=datetime.now(STATS_TIMEZONE).isoformat(timespec="seconds"),
        range=_range_response(range_info),
        bucket="day",
        points=points,
    )


# ========== 调度与配额管理 API ==========


class ClearCooldownRequest(BaseModel):
    account_id: str | None = None
    model: str | None = None
    surface: str | None = None


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
        }

    return {
        "enabled": True,
        "mode": "sticky",
        "accounts": rotator.get_all_stats(),
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
        if req.surface:
            rotator.clear_cooldown(req.account_id, model=req.model, surface=req.surface)
        else:
            rotator.clear_cooldown(req.account_id, model=req.model)
    else:
        rotator.clear_all_cooldowns()
    logger.info(
        "已清除冷却锁定: 账号=%s, 模型=%s, 面=%s",
        req.account_id,
        req.model,
        req.surface,
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


@protected_router.get("/surface/instances")
async def get_surface_instances(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """页面/Context 实例挂载与预算快照（只读）。"""
    client = runtime_state.client
    if client is None:
        raise HTTPException(503, detail="客户端未初始化")
    snapshot = client.surface_instance_snapshot()
    from aistudio_api.application.routing_pipeline import get_routing_pipeline

    pipeline = get_routing_pipeline()
    if pipeline is not None:
        routing = pipeline.snapshot()
        snapshot["routing"] = routing
        raw_instances = routing.get("instances")
        instances = raw_instances if isinstance(raw_instances, dict) else {}
        snapshot["contexts"] = instances.get("contexts", [])
        snapshot["budgets"] = instances.get("budgets", {})
        snapshot["planned_instances"] = instances.get("planned_instances", 0)
    return snapshot


@protected_router.get("/routing/state")
async def get_routing_state() -> dict[str, object]:
    """路由熔断/预算/Context 只读快照。"""
    from aistudio_api.application.routing_pipeline import get_routing_pipeline

    pipeline = get_routing_pipeline()
    if pipeline is None:
        raise HTTPException(503, detail="路由管线未初始化")
    return pipeline.snapshot()


@protected_router.post("/rotation/next")
async def force_next_account(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """强制切换到下一个可用账号。"""
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

    from aistudio_api.application.account_orchestrator import switch_to_account

    if not await switch_to_account(next_account.id, require_idle=False):
        raise HTTPException(500, detail="切换失败")
    result = account_service.get_account(next_account.id)
    if result is None:
        raise HTTPException(500, detail="切换失败")

    logger.info("已强制切换至下一可用账号: %s (%s)", result.id, result.name)
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


class FeaturesUpdateRequest(BaseModel):
    features: dict[str, object]


@protected_router.get("/config")
async def get_system_config() -> dict[str, object]:
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
        "timeout_stream": settings.timeout_stream,
        "yaml_content": yaml_content,
        "log_level": get_log_level(),
        "dump_requests": is_dump_requests_enabled(),
        "debug_env_active": is_debug_env_active(),
        "dump_to_file": is_dump_to_file_enabled(),
        "dump_dir": str(get_dump_dir()),
        "features": get_features_config(),
    }


@protected_router.put("/config/features")
async def update_features(req: FeaturesUpdateRequest) -> dict[str, object]:
    """结构化更新 features（供 WP7 UI 使用），原子落盘并热重载。"""
    try:
        features = update_configured_features(req.features)
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(500, detail=f"写入 features 失败: {e}") from e
    logger.info("features 已更新: %s", sorted(req.features))
    return {"ok": True, "features": features}


@protected_router.put("/config/yaml")
async def update_config_yaml(req: ConfigYamlUpdateRequest) -> dict[str, object]:
    """更新 config.yaml 文件内容并热重载默认配置。"""

    try:
        parsed = yaml.safe_load(req.yaml_content)
        if parsed is not None and not isinstance(parsed, dict):
            raise HTTPException(400, detail="YAML 顶层必须为字典结构")
    except yaml.YAMLError as e:
        raise HTTPException(400, detail=f"YAML 语法格式错误: {e}") from e
    config_yaml_path = _resolve_config_path(None)
    try:
        config_yaml_path.write_text(req.yaml_content, encoding="utf-8")
        invalidate_config_cache()

        if isinstance(parsed, dict) and "logging" in parsed:
            raw_logging = parsed["logging"]
            if isinstance(raw_logging, dict):
                from aistudio_api.infrastructure.utils.logger import (
                    set_dump_dir,
                    set_dump_requests,
                    set_dump_to_file,
                    set_log_level,
                )

                if "level" in raw_logging:
                    set_log_level(str(raw_logging["level"]))
                if "dump_requests" in raw_logging:
                    set_dump_requests(bool(raw_logging["dump_requests"]))
                if "dump_to_file" in raw_logging:
                    set_dump_to_file(bool(raw_logging["dump_to_file"]))
                if "dump_dir" in raw_logging:
                    set_dump_dir(str(raw_logging["dump_dir"]))
        logger.info("配置文件 config.yaml 已更新并重新加载")
        return {"ok": True, "message": "配置已保存并重载"}
    except Exception as e:
        raise HTTPException(500, detail=f"写入配置文件失败: {e}") from e


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

    config_path.write_text(
        yaml.dump(parsed, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    invalidate_config_cache()

    logger.info("已创建新 API Key: 备注=%s, 密钥=%s", name, new_key[:8] + "...")
    return ApiKeyItemModel(name=name, key=new_key, created_at=created_at)


@protected_router.delete("/api-keys/{key_value}")
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
    config_path.write_text(
        yaml.dump(parsed, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    invalidate_config_cache()

    logger.info("已删除 API Key: %s", key_value[:8] + "...")
    return {"ok": True}


@protected_router.put("/api-keys/{key_value}", response_model=ApiKeyItemModel)
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
    config_path.write_text(
        yaml.dump(parsed, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    invalidate_config_cache()
    logger.info("API Key 备注名已更新: %s -> %s", key_value[:8] + "...", new_name)

    return updated_item
