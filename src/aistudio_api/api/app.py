"""FastAPI application entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from aistudio_api.application.api_service_gemini import classify_gemini_error_payload
from aistudio_api.application.instance_manager import InstanceError
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.model_defaults import (
    get_configured_logging_settings,
)
from aistudio_api.infrastructure.utils.logger import (
    dump_request_exchange,
    get_logger,
    is_dump_requests_enabled,
    setup_logging,
)

from .dependencies import require_api_key, require_web_auth
from .format_errors import format_error_response, format_for_path
from .routes_accounts import router as accounts_router
from .routes_claude import router as claude_router
from .routes_gemini import router as gemini_router
from .routes_instances import router as instances_router
from .routes_keepalive import router as keepalive_router
from .routes_logs import protected_router as logs_protected_router
from .routes_models import router as models_router
from .routes_openai import router as openai_router
from .routes_responses import router as responses_router
from .routes_system import (
    protected_router as system_protected_router,
    public_router as system_public_router,
)
from .state import runtime_state

_log_cfg = get_configured_logging_settings()
setup_logging(str(_log_cfg.get("level", "INFO")))
logger = get_logger("server")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from aistudio_api.application.account_keepalive import AccountKeepaliveService
    from aistudio_api.application.account_rotator import init_rotator
    from aistudio_api.application.account_service import AccountService
    from aistudio_api.config import settings
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

    # WP5 v4：用户可见实例（一个同时登录账号会话）由独立 manager 管理。
    from aistudio_api.application.instance_manager import InstanceManager

    runtime_state.instance_manager = InstanceManager(
        client.instance_pool, account_store
    )

    # 初始化黏性账号调度器
    rotator = init_rotator(account_store)
    runtime_state.rotator = rotator

    account_refresher = AccountInfoRefresher(account_store)
    runtime_state.account_refresher = account_refresher

    logger.info(
        "Client initialized (port=%s, accounts=%d)",
        runtime_state.browser_port,
        len(account_store.list_accounts()),
    )

    # 后台预热浏览器，避免首次请求延迟
    warmup_task = None

    async def _warmup():
        for attempt in range(3):
            try:
                active = account_service.get_active_account()
                if active is not None:
                    await account_service.activate_account(
                        active.id,
                        client.session,
                        client=client,
                    )
                else:
                    await client.warmup()
                return
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(1.0)
                    continue
                logger.warning("浏览器预热失败: %s", e)

    warmup_task = asyncio.create_task(_warmup())

    async def _model_warmup():
        from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

        while True:
            try:
                await model_discovery.get_models(
                    session=client.session, force_refresh=True
                )
            except Exception as e:
                logger.warning("模型发现预热失败: %s", e)
            await asyncio.sleep(model_discovery.cache_ttl)

    model_warmup_task = asyncio.create_task(_model_warmup())

    keepalive = AccountKeepaliveService(
        account_store,
        account_service,
        rotator,
        client,
        account_refresher,
    )
    runtime_state.keepalive = keepalive
    await keepalive.start()

    idle_task = None
    if settings.browser_idle_timeout > 0:

        async def _idle_monitor():
            while True:
                await asyncio.sleep(10.0)
                try:
                    if runtime_state.client and getattr(
                        runtime_state.client, "_session", None
                    ):
                        await runtime_state.client._session.check_idle_timeout()
                except Exception:
                    pass

        idle_task = asyncio.create_task(_idle_monitor())

    yield
    logger.info("服务正在关闭")
    if warmup_task and not warmup_task.done():
        warmup_task.cancel()
    if model_warmup_task and not model_warmup_task.done():
        model_warmup_task.cancel()
    if idle_task and not idle_task.done():
        idle_task.cancel()
    if runtime_state.keepalive is not None:
        await runtime_state.keepalive.stop()
    if client:
        try:
            await client.close()
        except Exception as e:
            logger.debug("关闭客户端失败: %s", e)
    runtime_state.instance_manager = None
    runtime_state.client = None
    runtime_state.account_service = None
    runtime_state.rotator = None
    runtime_state.account_refresher = None
    runtime_state.keepalive = None


app = FastAPI(title="AI Studio API", lifespan=lifespan)
app.include_router(system_public_router)
app.include_router(system_protected_router, dependencies=[Depends(require_web_auth)])
app.include_router(logs_protected_router, dependencies=[Depends(require_web_auth)])
app.include_router(
    keepalive_router,
    prefix="/api",
    dependencies=[Depends(require_web_auth)],
)
app.include_router(
    accounts_router, prefix="/api", dependencies=[Depends(require_web_auth)]
)
app.include_router(
    instances_router, prefix="/api", dependencies=[Depends(require_web_auth)]
)
app.include_router(accounts_router, dependencies=[Depends(require_web_auth)])
app.include_router(gemini_router, dependencies=[Depends(require_api_key)])
app.include_router(models_router, dependencies=[Depends(require_api_key)])
app.include_router(openai_router, dependencies=[Depends(require_api_key)])
app.include_router(responses_router, dependencies=[Depends(require_api_key)])
app.include_router(claude_router, dependencies=[Depends(require_api_key)])


@app.exception_handler(InstanceError)
async def instance_exception_handler(
    _request: Request, exc: InstanceError
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": exc.code, "message": str(exc)}},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    api_format = format_for_path(request.url.path)
    if api_format != "gemini":
        return format_error_response(exc, api_format)
    code, msg, status_str = classify_gemini_error_payload(exc)
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


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    api_format = format_for_path(request.url.path)
    if api_format != "gemini":
        return format_error_response(exc, api_format)
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
            "/v1",
            "/v1beta",
            "/auth",
            "/assets",
            "/static",
            "/health",
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
