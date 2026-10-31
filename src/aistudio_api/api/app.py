"""FastAPI application entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

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
            await client.warmup()
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

    from aistudio_api.application.account_keepalive import AccountKeepaliveService
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
        "Client initialized (port=%s, accounts=%d)",
        runtime_state.browser_port,
        len(account_store.list_accounts()),
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
    )

    keepalive = AccountKeepaliveService(
        account_store,
        client,
        account_refresher,
        instance_manager=instance_manager,
    )
    runtime_state.keepalive = keepalive
    await keepalive.start()

    startup_task: asyncio.Task[object] | None = None
    startup_task = asyncio.create_task(_startup_instances(runtime_state, warmup_task))

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
            runtime_state, (warmup_task, startup_task, model_warmup_task)
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
app.include_router(models_router, dependencies=[Depends(require_api_key)])
app.include_router(openai_router, dependencies=[Depends(require_api_key)])
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
    if _is_openai_path(request):
        from aistudio_api.api.format_errors import format_error_response

        return format_error_response(exc, "openai")
    detail = exc.detail
    if isinstance(detail, dict):
        msg = str(detail.get("message") or detail)
    else:
        msg = str(detail)
    status_map = {
        400: "INVALID_ARGUMENT",
        401: "UNAUTHENTICATED",
        403: "PERMISSION_DENIED",
        404: "NOT_FOUND",
        409: "ABORTED",
        429: "RESOURCE_EXHAUSTED",
        503: "UNAVAILABLE",
    }
    code = exc.status_code
    status_str = status_map.get(
        code, "INTERNAL" if code >= 500 else "UNKNOWN"
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

    logger.info("正在启动服务，监听端口 %s", args.port)
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")
