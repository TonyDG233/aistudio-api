"""FastAPI application entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import secrets
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from aistudio_api.api.error_mapping import error_payload
from aistudio_api.api.format_errors import format_for_path
from aistudio_api.application.instance_manager import InstanceError
from aistudio_api.application.signature_cache import signature_cache
from aistudio_api.config import resolve_data_dir
from aistudio_api.domain.errors import AistudioError, RoutingError, TierRequiredError
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.model_defaults import (
    get_configured_logging_settings,
    validate_config,
)
from aistudio_api.infrastructure.utils.logger import (
    dump_request_exchange,
    get_logger,
    is_dump_requests_enabled,
    setup_logging,
)

from .dependencies import require_api_key, require_web_auth
from .routes_accounts import router as accounts_router
from .routes_gemini import router as gemini_router
from .routes_instances import router as instances_router
from .routes_keepalive import router as keepalive_router
from .routes_logs import protected_router as logs_protected_router
from .routes_models import router as models_router
from .routes_openai import router as openai_router
from .routes_system import (
    protected_router as system_protected_router,
    public_router as system_public_router,
)
from .state import RuntimeState, runtime_state

if TYPE_CHECKING:
    from aistudio_api.infrastructure.account.account_store import AccountStore

_log_cfg = get_configured_logging_settings()
setup_logging(str(_log_cfg.get("level", "INFO")))
logger = get_logger("server")


async def _call_lifecycle_method(target: object | None, method_name: str) -> None:
    """调用可选组件的 stop/close，兼容同步与异步实现。"""
    if target is None:
        return
    method = getattr(target, method_name, None)
    if not callable(method):
        return
    try:
        result = method()
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        logger.debug(
            "生命周期方法 %s.%s 失败: %s", type(target).__name__, method_name, exc
        )


async def _warmup_control_session(client: AIStudioClient) -> None:
    """Legacy pool-disabled warmup: opens the control Page and BotGuard once.

    Pool-enabled startup must never call this: pooled sessions are the only
    request instances, and a control Page would double the BotGuard probes.
    """
    for attempt in range(3):
        try:
            logger.debug("开始预热浏览器后端: attempt=%d", attempt + 1)
            await client.warmup()
            logger.debug("浏览器预热就绪: attempt=%d", attempt + 1)
            return
        except Exception as exc:
            if attempt < 2:
                await asyncio.sleep(1.0)
                continue
            logger.warning("浏览器预热失败: %s", exc)


def _start_control_warmup(
    client: AIStudioClient, *, pool_enabled: bool
) -> asyncio.Task[object] | None:
    """Return the legacy control warmup task, or None when the pool is enabled."""
    if pool_enabled:
        return None
    return asyncio.create_task(_warmup_control_session(client))


async def _startup_instances(
    state: RuntimeState,
    warmup_task: asyncio.Task[object] | None,
) -> None:
    """Start pooled request instances; legacy control warmup only when present."""
    if warmup_task is not None:
        try:
            await warmup_task
        except Exception as exc:
            logger.debug("实例预启动前预热失败: %s", exc)
    manager = state.instance_manager
    if manager is None:
        return
    try:
        await manager.startup()  # type: ignore[attr-defined]
    except Exception as exc:
        logger.warning("实例预启动失败: %s", exc)


async def _refresh_model_catalog_once(state: RuntimeState) -> int:
    """Refresh ListModels from READY pool sessions only (never the control Page)."""
    from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

    sessions = state.discovery_sessions()
    models = await model_discovery.get_models(sessions=sessions, force_refresh=True)
    logger.info(
        "模型发现预热完成: source=%s count=%d",
        model_discovery.last_source,
        len(models),
    )
    return len(models)


TIER_PROBE_FRESHNESS_SECONDS = 3 * 86400


def _checked_at_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


async def _refresh_startup_account_metadata(
    account_store: AccountStore,
    *,
    covered_ids: set[str] | None = None,
) -> None:
    """Probe startup tier/email, skipping keepalive-covered and fresh Free accounts.

    ``covered_ids`` are accounts already handled by the keepalive startup
    metadata refresh. A verified Free tier whose ``tier_checked_at`` is within
    three days skips the benefit-tier HTTP probe; a missing email is still
    refreshed.
    """
    import json

    from aistudio_api.infrastructure.account.account_info_refresher import (
        is_verified_free_tier_fresh,
    )
    from aistudio_api.infrastructure.account.cookie_parser import (
        probe_account_benefit_tier,
        probe_account_email,
    )

    covered = covered_ids or set()
    now_ts = time.time()
    accounts_to_check = [
        account
        for account in account_store.list_accounts()
        if account.id not in covered
        and (
            not account.email
            or (checked := _checked_at_ts(account.tier_checked_at)) is None
            or (now_ts - checked > TIER_PROBE_FRESHNESS_SECONDS)
        )
    ]
    if not accounts_to_check:
        logger.debug("启动探测：账号已被 keepalive 覆盖或最近已校验，跳过")
        return

    logger.info(
        "开始自动校验账号订阅等级与邮箱 (共 %d 个账号待检测)...",
        len(accounts_to_check),
    )
    upgraded_count = 0
    checked_count = 0
    skipped_tier_count = 0

    for account in accounts_to_check:
        auth_path = account_store.get_auth_path(account.id)
        if auth_path is None or not auth_path.is_file():
            continue
        try:
            auth_data = json.loads(auth_path.read_text(encoding="utf-8"))
            raw_cookies = auth_data.get("cookies") or []
            cookie_dict = {
                str(cookie.get("name") or ""): str(cookie.get("value") or "")
                for cookie in raw_cookies
                if isinstance(cookie, dict) and cookie.get("name")
            }
            if not cookie_dict:
                continue
            checked_count += 1
            if not account.email:
                email = await probe_account_email(
                    cookie_dict, auth_user=account.auth_user or "0"
                )
                if email:
                    account_store.update_account_email(account.id, email)
            if is_verified_free_tier_fresh(account, now=now_ts):
                skipped_tier_count += 1
                logger.debug(
                    "订阅等级 3 天内已校验且为 Free，跳过启动 tier 探测: id=%s",
                    account.id,
                )
                continue
            probed_tier = await probe_account_benefit_tier(
                cookie_dict, auth_user=account.auth_user or "0"
            )
            resolved_tier = probed_tier or "Free"
            account_store.update_account_tier(
                account.id,
                resolved_tier,
                checked_at=datetime.now(UTC).isoformat(),
            )
            if resolved_tier != "Free" and account.tier == "Free":
                upgraded_count += 1
                logger.info(
                    "账号订阅等级自动升级: id=%s (u/%s) -> %s",
                    account.id,
                    account.auth_user,
                    resolved_tier,
                )
        except Exception as exc:
            logger.debug("账号 %s 订阅检测异常: %s", account.id, exc)

    logger.info(
        "账号订阅校验完成: 检测=%d 个, 跳过 tier=%d 个, 发现付费订阅=%d 个",
        checked_count,
        skipped_tier_count,
        upgraded_count,
    )


async def _shutdown_runtime(
    state: RuntimeState,
    tasks: tuple[asyncio.Task[object] | None, ...] = (),
) -> None:
    """固定关闭顺序：取消后台任务 → keepalive → instance_manager → client。"""
    cancelled: list[asyncio.Task[object]] = []
    for task in tasks:
        if task is not None and not task.done():
            task.cancel()
            cancelled.append(task)
    if cancelled:
        await asyncio.gather(*cancelled, return_exceptions=True)

    await _call_lifecycle_method(state.keepalive, "stop")
    await _call_lifecycle_method(state.instance_manager, "close")
    await _call_lifecycle_method(state.client, "close")

    state.keepalive = None
    state.instance_manager = None
    state.routing_pipeline = None
    state.client = None
    state.account_service = None
    state.account_refresher = None
    state.rotator = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_config()
    signature_cache.configure_path(resolve_data_dir() / "signature_cache.json")

    from aistudio_api.application.account_keepalive import (
        AccountKeepaliveService,
        KeepaliveSettings,
    )
    from aistudio_api.application.account_rotator import init_rotator
    from aistudio_api.application.account_service import AccountService
    from aistudio_api.infrastructure.account.account_info_refresher import (
        AccountInfoRefresher,
    )
    from aistudio_api.infrastructure.account.account_store import AccountStore
    from aistudio_api.infrastructure.browser.browser_engine import (
        install_process_cleanup_handlers,
    )

    install_process_cleanup_handlers()

    client = AIStudioClient(
        port=runtime_state.browser_port,
    )
    runtime_state.client = client

    # 初始化账号管理服务
    account_store = AccountStore()
    account_service = AccountService(account_store)
    runtime_state.account_service = account_service
    # P1 接线刷新器；P6 由 keepalive metadata_refresh 统一调度 tier/email。
    account_refresher = AccountInfoRefresher(account_store)
    runtime_state.account_refresher = account_refresher

    # 初始化黏性账号调度器
    rotator = init_rotator(account_store)
    runtime_state.rotator = rotator

    for a in account_store.list_accounts():
        st = rotator.get_account_stats(a.id)
        if st is not None and not st.session_expired and st.auth_cooldown > 0:
            st.auth_cooldown = 0.0
    active_auth_path = account_store.get_active_auth_path()
    if active_auth_path is not None and client.session is not None:
        client.session._auth_file = str(active_auth_path)
    logger.info(
        "服务初始化完成: 监听浏览器调试端口=%d, 已加载账号数=%d",
        runtime_state.browser_port,
        len(account_store.list_accounts()),
    )
    logger.debug(
        "账号初始状态: %s",
        [
            f"{a.id}(u/{a.auth_user},tier={a.tier},enabled={a.enabled})"
            for a in account_store.list_accounts()
        ],
    )

    # P3-D04：请求实例池由独立的 InstanceManager 管理；控制面不占池容量。
    from aistudio_api.application.instance_manager import InstanceManager
    from aistudio_api.infrastructure.gateway.instance_pool import InstancePool
    from aistudio_api.infrastructure.gateway.model_defaults import (
        get_configured_instance_specs,
        get_configured_max_contexts,
    )

    # 显式分流：多实例池启用时只允许 InstanceManager.startup() 预热池会话；
    # 控制面 client.session 不得在启动时自动开页/跑 BotGuard。只有配置中
    # 完全没有任何实例（真正的 pool-disabled legacy 模式）才允许控制面预热。
    pool_specs = get_configured_instance_specs()
    pool_enabled = bool(pool_specs)
    if pool_enabled:
        logger.info(
            "实例池已启用 (%d 组配置)：跳过控制面预热，启动仅由 "
            "InstanceManager.startup() 激活池会话",
            len(pool_specs),
        )
    else:
        logger.info("实例池未配置：使用 legacy 控制面预热（不创建池会话）")
    warmup_task = _start_control_warmup(client, pool_enabled=pool_enabled)

    instance_pool = InstancePool(
        client.session.ensure_cdp_client,
        specs_provider=get_configured_instance_specs,
        max_contexts_provider=get_configured_max_contexts,
        on_slot_retired=lambda _realm_id: client.clear_templates(),
    )
    instance_manager = InstanceManager(
        instance_pool,
        account_store,
    )
    runtime_state.instance_manager = instance_manager

    from aistudio_api.application.routing_pipeline import RoutingPipeline

    runtime_state.routing_pipeline = RoutingPipeline(
        instance_manager=instance_manager,
        account_store=account_store,
        rotator=rotator,
        stats_recorder=runtime_state.record,
    )

    keepalive = AccountKeepaliveService(
        account_store,
        client,
        account_refresher,
        instance_manager=instance_manager,
    )
    runtime_state.keepalive = keepalive
    # App owns the first metadata refresh below so the two startup probes can
    # coordinate: keepalive reports covered accounts and app-side skips them.
    await keepalive.start(startup_metadata_done=True)

    startup_task: asyncio.Task[object] | None = None
    startup_task = asyncio.create_task(_startup_instances(runtime_state, warmup_task))

    # 启动订阅/邮箱自检：先跑 keepalive metadata refresh，再对未被覆盖的账号
    # 做应用侧探测；3 天内已验证 Free 的账号跳过 benefit-tier 探测。
    tier_task: asyncio.Task[object] | None = None

    async def _startup_metadata_and_tiers() -> None:
        covered_ids: set[str] = set()
        settings = KeepaliveSettings.from_config()
        if settings.metadata_refresh and settings.metadata_refresh_on_startup:
            result = await keepalive.run_startup_metadata_refresh()
            raw_accounts = result.get("accounts")
            if result.get("reason") in ("checked", "no_due_accounts"):
                if isinstance(raw_accounts, (list, tuple)):
                    covered_ids = {str(account_id) for account_id in raw_accounts}
            else:
                logger.debug(
                    "keepalive 启动 metadata 刷新未覆盖批次: reason=%s",
                    result.get("reason"),
                )
        await _refresh_startup_account_metadata(
            account_store, covered_ids=covered_ids
        )

    tier_task = asyncio.create_task(_startup_metadata_and_tiers())

    # Model discovery warmup: wait until the instance pool has a READY session
    # (preferring the Pro/paid lane), then refresh the live ListModels catalog
    # and repeat on the cache TTL. Pool sessions only: never the control session
    # and never a new Page/BotGuard path.
    model_warmup_task: asyncio.Task[object] | None = None

    async def _model_warmup():
        from aistudio_api.infrastructure.gateway.model_discovery import (
            model_discovery,
        )

        if startup_task is not None:
            try:
                await startup_task
            except Exception as exc:
                logger.debug("模型发现预热前实例启动失败: %s", exc)
        while True:
            try:
                await _refresh_model_catalog_once(runtime_state)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("模型发现预热失败: %s", exc)
            await asyncio.sleep(model_discovery.cache_ttl)

    model_warmup_task = asyncio.create_task(_model_warmup())

    try:
        yield
    finally:
        logger.info("服务正在关闭")
        await _shutdown_runtime(
            runtime_state, (warmup_task, startup_task, tier_task, model_warmup_task)
        )


app = FastAPI(title="AI Studio API", lifespan=lifespan)
app.include_router(system_public_router)
app.include_router(system_protected_router, dependencies=[Depends(require_web_auth)])
app.include_router(
    accounts_router, prefix="/api", dependencies=[Depends(require_web_auth)]
)
app.include_router(accounts_router, dependencies=[Depends(require_web_auth)])
app.include_router(
    instances_router, prefix="/api", dependencies=[Depends(require_web_auth)]
)
app.include_router(
    keepalive_router, prefix="/api", dependencies=[Depends(require_web_auth)]
)
app.include_router(gemini_router, dependencies=[Depends(require_api_key)])
app.include_router(models_router)
app.include_router(openai_router)
app.include_router(logs_protected_router, dependencies=[Depends(require_web_auth)])
app.include_router(
    logs_protected_router, prefix="/api", dependencies=[Depends(require_web_auth)]
)


def _is_openai_path(request: Request) -> bool:
    return format_for_path(request.url.path) == "openai"


@app.exception_handler(TierRequiredError)
@app.exception_handler(RoutingError)
async def routing_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """D-04/D-06: tier 403 and routing 503 are rendered by the protocol adapter."""
    protocol = "openai" if _is_openai_path(request) else "gemini"
    status, body = error_payload(exc, protocol)
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(AistudioError)
async def aistudio_error_handler(request: Request, exc: Exception) -> JSONResponse:
    protocol = "openai" if _is_openai_path(request) else "gemini"
    status, body = error_payload(exc, protocol)
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        msg = str(detail.get("message") or detail)
    else:
        msg = str(detail)
    code = exc.status_code
    if code >= 500:
        logger.error(
            "HTTP 异常: status=%d, message=%s, path=%s",
            code,
            msg,
            request.url.path,
        )
    elif code in (401, 403, 429):
        logger.warning(
            "HTTP 权限/限额异常: status=%d, message=%s, path=%s",
            code,
            msg,
            request.url.path,
        )
    else:
        logger.info(
            "HTTP 业务异常: status=%d, message=%s, path=%s",
            code,
            msg,
            request.url.path,
        )
    if _is_openai_path(request):
        from aistudio_api.api.format_errors import format_error_response

        return format_error_response(exc, "openai")
    status_map = {
        400: "INVALID_ARGUMENT",
        401: "UNAUTHENTICATED",
        403: "PERMISSION_DENIED",
        404: "NOT_FOUND",
        409: "ABORTED",
        429: "RESOURCE_EXHAUSTED",
        503: "UNAVAILABLE",
    }
    status_str = status_map.get(code, "INTERNAL" if code >= 500 else "UNKNOWN")
    return JSONResponse(
        status_code=code,
        content={
            "error": {
                "code": code,
                "message": msg,
                "status": status_str,
            }
        },
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(InstanceError)
async def instance_exception_handler(
    _request: Request, exc: InstanceError
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": exc.code, "message": str(exc)}},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    if _is_openai_path(request):
        from aistudio_api.api.format_errors import format_error_response

        return format_error_response(exc, "openai")
    code, msg, status_str = 400, str(exc), "INVALID_ARGUMENT"
    # RequestValidationError.errors() does not support include_input here, so
    # project only non-input fields to keep prompts/body fragments out of logs.
    safe_errors = [
        {
            "loc": error.get("loc"),
            "type": error.get("type"),
            "msg": error.get("msg"),
        }
        for error in exc.errors()
        if isinstance(error, dict)
    ]
    logger.warning(
        "请求参数校验失败: path=%s, errors=%s", request.url.path, safe_errors
    )
    return JSONResponse(
        status_code=code,
        content={
            "error": {
                "code": code,
                "message": msg,
                "status": status_str,
            }
        },
    )


@app.middleware("http")
async def logging_and_dump_middleware(request, call_next):
    """统一请求生命周期日志与请求转储 (DEBUG) 中间件。"""

    start_time = time.perf_counter()
    path = request.url.path

    is_static = path.startswith(("/static", "/assets")) or path in ("/favicon.ico",)
    dump_enabled = is_dump_requests_enabled() and not is_static

    req_id = ""
    body_text = None
    client_str = ""
    headers_dict = None
    query_dict = None

    if dump_enabled:
        req_id = f"req_{secrets.token_hex(4)}"
        client_str = (
            f"{request.client.host}:{request.client.port}"
            if request.client
            else "unknown"
        )
        headers_dict = dict(request.headers)
        query_dict = dict(request.query_params)
        try:
            body_bytes = await request.body()
            body_text = body_bytes.decode("utf-8", errors="replace")
        except Exception:
            body_text = "(failed to read request body)"

    try:
        response = await call_next(request)
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        if dump_enabled:
            dump_request_exchange(
                req_id=req_id,
                method=request.method,
                url=str(request.url),
                client=client_str,
                headers=headers_dict or {},
                query_params=query_dict or {},
                body_text=body_text,
                status_code=500,
                elapsed_ms=elapsed_ms,
                response_text=f"Internal Server Error (Exception: {exc})",
            )
        else:
            logger.error(
                "HTTP %s %s -> 500 (%.1fms, %s)",
                request.method,
                path,
                elapsed_ms,
                exc,
            )
        raise exc

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    content_type = response.headers.get("content-type", "")
    is_event_stream = "text/event-stream" in content_type

    if dump_enabled:
        resp_text = None
        if is_event_stream:
            resp_text = "(streaming response in progress)"
        else:
            try:
                # 读取非流式响应体并重构 iterator 以便正常返回给客户端
                chunks = [chunk async for chunk in response.body_iterator]
                resp_bytes = b"".join(chunks)
                resp_text = resp_bytes.decode("utf-8", errors="replace")

                async def _replay_iterator():
                    for c in chunks:
                        yield c

                response.body_iterator = _replay_iterator()
            except Exception:
                resp_text = "(failed to read response body)"

        dump_request_exchange(
            req_id=req_id,
            method=request.method,
            url=str(request.url),
            client=client_str,
            headers=headers_dict or {},
            query_params=query_dict or {},
            body_text=body_text,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
            response_headers=dict(response.headers),
            response_text=resp_text,
            is_stream=is_event_stream,
        )
    elif not is_static:
        if path == "/health" and response.status_code == 200:
            # Health probes are polled continuously; keep success out of the
            # INFO UI stream, but retain diagnostics.
            logger.debug(
                "HTTP %s %s -> %d (%.1fms)",
                request.method,
                path,
                response.status_code,
                elapsed_ms,
            )
        else:
            logger.info(
                "HTTP %s %s -> %d (%.1fms)",
                request.method,
                path,
                response.status_code,
                elapsed_ms,
            )

    return response


# 挂载前端静态资源与 SPA 路由支持
static_dir = Path(__file__).resolve().parents[1] / "static"
index_html_path = static_dir / "index.html"

if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")


@app.get("/auth/check")
async def auth_check():
    """检查认证状态，用于前端判断是否需要登录。"""
    from aistudio_api.config import settings

    return {"auth_enabled": settings.auth_enabled}


@app.middleware("http")
async def spa_navigation_middleware(request: Request, call_next):
    """统一处理前端 SPA 单页应用导航：浏览器页面直达/刷新响应 index.html，API 请求直通 JSON。"""
    accept = request.headers.get("accept", "")
    is_html_nav = (
        request.method == "GET"
        and "text/html" in accept
        and "application/json" not in accept
    )
    is_api = request.url.path.startswith(
        (
            "/api",
            "/v1beta",
            "/auth",
            "/assets",
            "/static",
            "/health",
            "/stats",
            "/rotation",
            "/config",
            "/api-keys",
            "/logs",
        )
    )
    is_accounts_spa = request.url.path in ("/accounts", "/accounts/")
    is_logs_spa = request.url.path in ("/logs", "/logs/")
    if (
        is_html_nav
        and (is_accounts_spa or is_logs_spa or not is_api)
        and index_html_path.is_file()
    ):
        response = FileResponse(index_html_path)
        response.headers["Vary"] = "Accept"
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        return response

    response = await call_next(request)

    if request.url.path.startswith(("/accounts", "/api/accounts")) or is_html_nav:
        existing_vary = response.headers.get("Vary")
        if existing_vary:
            if "Accept" not in [v.strip() for v in existing_vary.split(",")]:
                response.headers["Vary"] = f"{existing_vary}, Accept"
        else:
            response.headers["Vary"] = "Accept"
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )

    return response


@app.get("/")
async def root_index():
    """根路径兜底响应。"""
    if index_html_path.is_file():
        response = FileResponse(index_html_path)
        response.headers["Vary"] = "Accept"
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        return response
    return JSONResponse({"message": "AI Studio API Web UI"})


def main():
    from aistudio_api.config import settings

    parser = argparse.ArgumentParser(description="AI Studio API Server")
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--browser-port", type=int, default=settings.browser_port)
    args = parser.parse_args()

    runtime_state.browser_port = args.browser_port

    import uvicorn

    logger.info("启动服务: 监听端口=%d, 浏览器调试端口=%d", args.port, runtime_state.browser_port)
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")
