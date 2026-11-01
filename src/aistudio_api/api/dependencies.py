"""Shared FastAPI dependencies."""

from __future__ import annotations

import secrets
import time
from threading import Lock
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from aistudio_api.config import settings
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.utils.logger import get_logger

from .state import runtime_state

logger = get_logger("auth")

# Read-only model-list probes (e.g. a Docker healthcheck) can repeat every few
# seconds on the same client/path. Keep one WARN per key per window and send the
# rest to DEBUG so the UI log stream stays readable.
_MISSING_KEY_WARN_WINDOW_SECONDS = 300.0
_MISSING_KEY_STATE_LIMIT = 1024
_missing_key_warn_last: dict[tuple[str, str], float] = {}
_missing_key_warn_suppressed: dict[tuple[str, str], int] = {}
_missing_key_warn_lock = Lock()
_monotonic = time.monotonic


def _request_client_ip(request: Request) -> str:
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return host if isinstance(host, str) and host else "unknown"


def _request_path(request: Request) -> str:
    scope = getattr(request, "scope", None)
    if isinstance(scope, dict):
        path = scope.get("path")
        if isinstance(path, str) and path:
            return path
    url = getattr(request, "url", None)
    path = getattr(url, "path", None)
    return path if isinstance(path, str) else ""


def _sanitized_header(request: Request, name: str, *, limit: int | None = None) -> str:
    headers = getattr(request, "headers", None)
    if headers is None:
        return "-"
    try:
        value = headers.get(name)
    except Exception:
        return "-"
    if not isinstance(value, str):
        return "-"
    value = " ".join(value.split())
    if limit is not None:
        value = value[:limit]
    return value or "-"


def _sanitized_referer(request: Request) -> str:
    """Return the Referer with any query/fragment stripped (no secrets logged)."""
    value = _sanitized_header(request, "referer")
    if value == "-":
        return value
    parsed = urlsplit(value)
    if parsed.scheme and parsed.netloc:
        netloc = parsed.netloc.rsplit("@", 1)[-1]
        return f"{parsed.scheme}://{netloc}{parsed.path or '/'}"
    return value.split("?", 1)[0].split("#", 1)[0]


def _missing_key_warning_allowed(client_ip: str, path: str) -> bool:
    key = (client_ip, path)
    now = _monotonic()
    with _missing_key_warn_lock:
        last = _missing_key_warn_last.get(key)
        if last is not None and now - last < _MISSING_KEY_WARN_WINDOW_SECONDS:
            _missing_key_warn_suppressed[key] = (
                _missing_key_warn_suppressed.get(key, 0) + 1
            )
            return False

        # Drop keys outside the dedup window, then enforce a hard cap so a burst
        # of unique (IP, path) pairs inside one window cannot grow the dict
        # without bound.  Leave one slot for the key inserted below.
        cutoff = now - _MISSING_KEY_WARN_WINDOW_SECONDS
        stale = [k for k, ts in _missing_key_warn_last.items() if ts < cutoff]
        for stale_key in stale:
            _missing_key_warn_last.pop(stale_key, None)
            _missing_key_warn_suppressed.pop(stale_key, None)
        while len(_missing_key_warn_last) >= _MISSING_KEY_STATE_LIMIT:
            oldest_key = min(
                _missing_key_warn_last.items(), key=lambda item: item[1]
            )[0]
            _missing_key_warn_last.pop(oldest_key, None)
            _missing_key_warn_suppressed.pop(oldest_key, None)

        _missing_key_warn_last[key] = now
        _missing_key_warn_suppressed.pop(key, None)
        return True


def _suppressed_missing_key_count(client_ip: str, path: str) -> int:
    with _missing_key_warn_lock:
        return _missing_key_warn_suppressed.get((client_ip, path), 0)


def _log_auth_failure(
    request: Request,
    *,
    reason: str,
    dedup_missing_key: bool = False,
) -> None:
    client_ip = _request_client_ip(request)
    path = _request_path(request)
    user_agent = _sanitized_header(request, "user-agent", limit=80)
    referer = _sanitized_referer(request)

    if dedup_missing_key and not _missing_key_warning_allowed(client_ip, path):
        suppressed = _suppressed_missing_key_count(client_ip, path)
        logger.debug(
            "API 鉴权失败（已抑制 %d 次）: 客户端=%s, %s, path=%s, UA=%s, Referer=%s",
            suppressed,
            client_ip,
            reason,
            path or "-",
            user_agent,
            referer,
        )
        return

    logger.warning(
        "API 鉴权失败: 客户端=%s, %s, path=%s, UA=%s, Referer=%s",
        client_ip,
        reason,
        path or "-",
        user_agent,
        referer,
    )


def _extract_bearer_token(request: Request) -> str | None:
    authorization = (request.headers.get("authorization") or "").strip()
    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None

    token = token.strip()
    return token or None


def _extract_request_token(request: Request) -> str | None:
    # 1. Google Gemini standard query param: ?key=...
    query_key = request.query_params.get("key")
    if query_key and query_key.strip():
        return query_key.strip()

    # 2. Google Gemini standard header: x-goog-api-key
    goog_key = (request.headers.get("x-goog-api-key") or "").strip()
    if goog_key:
        return goog_key

    # 3. Standard x-api-key header
    api_key = (request.headers.get("x-api-key") or "").strip()
    if api_key:
        return api_key

    # 4. Bearer token in Authorization header
    return _extract_bearer_token(request)


def _web_session_password() -> str | None:
    """Return the configured admin password, or None when the console is open."""
    if not settings.auth_enabled:
        return None
    password = settings.web_password
    if not password:
        return None
    return password.strip() or None


def _web_session_matches(token: str | None) -> bool:
    password = _web_session_password()
    return bool(token and password and secrets.compare_digest(token, password))


def _is_valid_web_session(request: Request) -> bool:
    """Accept only the explicit Bearer admin credential as a web session."""
    return _web_session_matches(_extract_bearer_token(request))


def require_web_auth(request: Request) -> None:
    """保护管理控制台路由 (通过环境变量 AISTUDIO_WEB_PASSWORD 配置密码)。"""
    if not settings.auth_enabled:
        return

    if _web_session_matches(_extract_request_token(request)):
        return
    client_ip = request.client.host if request.client else "unknown"
    logger.warning("管理控制台鉴权失败: 客户端=%s", client_ip)
    raise HTTPException(
        status_code=401,
        detail="管理控制台鉴权失败，请输入正确的管理密码",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _validate_api_key(request: Request, *, read_only: bool = False) -> None:
    """Shared API key validation: 401 on missing keys, missing token, or bad token.

    ``read_only`` is set for model-list endpoints. Missing-key 401s there are
    deduplicated per client/path so a container healthcheck cannot flood logs.
    """
    from aistudio_api.infrastructure.gateway.model_defaults import (
        get_configured_api_keys,
        get_configured_security_settings,
    )

    if get_configured_security_settings():
        return

    configured_keys = get_configured_api_keys()
    all_valid_keys = configured_keys | settings.api_keys

    # 空 Key 绝不开放：未配置 Key 且免密开关关闭时一律拒绝
    if not all_valid_keys:
        _log_auth_failure(
            request,
            reason="未配置 API Key 且免密开关关闭",
            dedup_missing_key=read_only,
        )
        raise HTTPException(
            status_code=401,
            detail={
                "message": "未配置 API Key 且免密开关（security.api_allow_anonymous）未开启，已拒绝请求",
                "type": "authentication_error",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = _extract_request_token(request)
    if not token:
        _log_auth_failure(
            request,
            reason="缺少 API Key",
            dedup_missing_key=read_only,
        )
        raise HTTPException(
            status_code=401,
            detail={
                "message": "缺少 API Key，请通过 'x-goog-api-key'、'x-api-key'、'key' 查询参数或 'Authorization: Bearer <key>' 提供",
                "type": "authentication_error",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    if token in all_valid_keys:
        return

    _log_auth_failure(request, reason="API Key 无效")
    raise HTTPException(
        status_code=401,
        detail={
            "message": "提供的 API Key 无效",
            "type": "authentication_error",
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_api_key(request: Request) -> None:
    """保护对外 Gemini API 路由 (通过 config.yaml 中的 api_keys 配置)。"""
    _validate_api_key(request)


def require_api_key_or_web_session(request: Request) -> None:
    """Read-only auth for model endpoints: valid API key or Web admin session.

    Generation endpoints must keep using :func:`require_api_key`: the web
    password is deliberately not accepted as an API key outside read-only
    model metadata routes.
    """
    if _is_valid_web_session(request):
        return
    _validate_api_key(request, read_only=True)


def get_client() -> AIStudioClient:
    if runtime_state.client is None:
        logger.error("客户端未初始化，无法处理请求")
        raise HTTPException(
            503,
            detail={"message": "Client not initialized", "type": "service_unavailable"},
        )
    return runtime_state.client


def get_routing_pipeline():
    from typing import cast

    from aistudio_api.application.routing_pipeline import RoutingPipeline

    pipeline = runtime_state.routing_pipeline
    if pipeline is None:
        raise HTTPException(
            503,
            detail={
                "message": "路由管线未初始化",
                "type": "service_unavailable",
            },
        )
    return cast(RoutingPipeline, pipeline)


def get_account_service():
    if runtime_state.account_service is None:
        logger.error("账号服务未初始化，无法处理请求")
        raise HTTPException(
            503,
            detail={
                "message": "Account service not initialized",
                "type": "service_unavailable",
            },
        )
    return runtime_state.account_service


def get_runtime_state():
    return runtime_state
