"""Upstream error classification and per-attempt handling.

This module is protocol-agnostic: callers render the resulting
``HTTPException`` / exception into their own protocol shape.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError

from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_orchestrator import (
    MAX_RETRIES,
    record_rotator_event,
    try_switch_account,
)
from aistudio_api.domain.errors import (
    AistudioError,
    AuthError,
    RequestError,
    SessionExpiredError,
    UsageLimitExceeded,
    is_browser_session_hang_error,
    is_transient_rpc_error,
    parse_upstream_grpc_error,
)
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.utils.logger import get_logger

if TYPE_CHECKING:
    from aistudio_api.application.routing_pipeline import (
        RouteContext,
        RoutingPipeline,
    )

logger = get_logger("upstream.errors")

WIRE_TO_GEMINI_FINISH_REASON: dict[int, str] = {
    0: "FINISH_REASON_UNSPECIFIED",
    1: "STOP",
    2: "MAX_TOKENS",
    3: "SAFETY",
    4: "RECITATION",
    5: "LANGUAGE",
    6: "OTHER",
    7: "BLOCKLIST",
    8: "PROHIBITED_CONTENT",
    9: "SPII",
    10: "MALFORMED_FUNCTION_CALL",
    11: "IMAGE_SAFETY",
    12: "UNEXPECTED_TOOL_CALL",
    13: "TOO_MANY_TOOL_CALLS",
    14: "IMAGE_PROHIBITED_CONTENT",
    15: "NO_IMAGE",
    16: "IMAGE_RECITATION",
    17: "IMAGE_OTHER",
}

_GEMINI_STATUS_BY_HTTP: dict[int, str] = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    429: "RESOURCE_EXHAUSTED",
    500: "INTERNAL",
    503: "UNAVAILABLE",
}


def to_gemini_finish_reason(wire_code: int | None) -> str:
    if wire_code is None:
        return "STOP"
    return WIRE_TO_GEMINI_FINISH_REASON.get(wire_code, "FINISH_REASON_UNSPECIFIED")


def clean_upstream_error_message(raw_msg: str) -> str:
    """Extract a clean, readable error message from Google/JSPB upstream error strings."""
    if not raw_msg:
        return ""
    text = raw_msg.strip()
    if "[,[" not in text and "[null,[" not in text and not text.startswith("[,"):
        return text
    inner = text
    while True:
        match = re.match(r"^HTTP\s+\d+:\s*(.*)$", inner)
        if match:
            inner = match.group(1).strip()
        else:
            break
    _, msg = parse_upstream_grpc_error(inner)
    return msg or inner


def classify_gemini_error_payload(exc: Exception) -> tuple[int, str, str]:
    """Extract standard Gemini HTTP status code, message, and status string."""
    if isinstance(exc, SessionExpiredError):
        return (
            401,
            "All accounts have expired sessions. Please import fresh cookies.",
            "UNAUTHENTICATED",
        )
    if isinstance(exc, AuthError):
        return 403, str(exc), "PERMISSION_DENIED"
    if isinstance(exc, UsageLimitExceeded):
        return 429, str(exc), "RESOURCE_EXHAUSTED"
    if isinstance(exc, (ValueError, RequestValidationError)):
        return 400, str(exc), "INVALID_ARGUMENT"
    if isinstance(exc, HTTPException):
        status = exc.status_code
        detail = exc.detail
        raw_msg = detail.get("message") if isinstance(detail, dict) else detail
        msg = str(raw_msg) if raw_msg is not None else ""
        clean_msg = clean_upstream_error_message(msg)
        return (
            status,
            clean_msg,
            _GEMINI_STATUS_BY_HTTP.get(
                status, "INTERNAL" if status >= 500 else "UNKNOWN"
            ),
        )
    if isinstance(exc, RequestError):
        status = exc.status if exc.status > 0 else 500
        clean_msg = clean_upstream_error_message(str(exc))
        return (
            status,
            clean_msg,
            _GEMINI_STATUS_BY_HTTP.get(
                status, "INTERNAL" if status >= 500 else "UNKNOWN"
            ),
        )
    if isinstance(exc, AistudioError):
        return 500, str(exc), "INTERNAL"

    return 500, str(exc), "INTERNAL"


async def handle_attempt_exception(
    exc: Exception,
    *,
    attempt: int,
    model_path: str,
    normalized_model: str | None,
    client: AIStudioClient,
    has_yielded_data: bool = False,
    surface: str = "chat",
    api_format: str = "gemini",
    routing: RoutingPipeline | None = None,
    route_ctx: RouteContext | None = None,
    lease=None,
) -> bool:
    """Handle per-attempt error classification and account switching.

    Without ``routing`` this keeps the v1 behavior. With ``routing`` account
    switches are decided by the routing pipeline: switch actions consume
    ``max_switches``, while same-account retries only consume ``MAX_RETRIES``.

    Returns True if the operation should be retried, or raises HTTPException / re-raises.
    """
    target_model = normalized_model or model_path
    account_svc = runtime_state.account_service
    active_acc = account_svc.get_active_account() if account_svc else None
    failed_id = getattr(lease, "account_id", None) or (
        active_acc.id if active_acc else None
    )

    def switch(reason: str) -> bool:
        if routing is None or route_ctx is None:
            return False
        routing.record_switch(route_ctx, reason)
        return True

    def retry(reason: str) -> None:
        if routing is not None and route_ctx is not None:
            routing.record_retry(route_ctx, reason)

    if isinstance(exc, ValueError):
        raise HTTPException(
            400, detail={"message": str(exc), "type": "bad_request"}
        ) from exc

    if isinstance(exc, SessionExpiredError):
        logger.warning("账号会话失效，已被重定向到登录页: %s", exc)
        client.clear_templates()
        record_rotator_event(
            "auth_error",
            model=target_model,
            surface=surface,
            account_id=failed_id,
        )
        if not has_yielded_data:
            if routing is not None:
                switch("session_expired")
                logger.info("路由管线切换账号重试 (%d/%d)", attempt + 1, MAX_RETRIES)
                return True
            if await try_switch_account(
                model=target_model,
                failed_account_id=failed_id,
                is_auth_error=True,
                is_session_expired=True,
                surface=surface,
            ):
                logger.info(
                    "已切换至其他可用健康账号重试 (%d/%d)",
                    attempt + 1,
                    MAX_RETRIES,
                )
                return True
        raise HTTPException(
            401,
            detail={
                "message": "All accounts encountered login redirect or expired sessions. Please import fresh cookies.",
                "type": "auth_error",
            },
        ) from exc

    if isinstance(exc, AuthError):
        logger.warning("账号 403 权限拒绝: %s", exc)
        client.clear_templates()
        record_rotator_event(
            "auth_error",
            model=target_model,
            surface=surface,
            account_id=failed_id,
        )
        if not has_yielded_data:
            if routing is not None:
                switch("auth_error")
                logger.info("路由管线切换账号重试 (%d/%d)", attempt + 1, MAX_RETRIES)
                return True
            if await try_switch_account(
                model=target_model,
                failed_account_id=failed_id,
                is_auth_error=True,
                surface=surface,
            ):
                logger.info("切换至可用账号重试 (%d/%d)", attempt + 1, MAX_RETRIES)
                return True
        raise HTTPException(
            403, detail={"message": str(exc), "type": "auth_error"}
        ) from exc

    if isinstance(exc, UsageLimitExceeded):
        runtime_state.record(target_model, "rate_limited", api_format=api_format)
        record_rotator_event(
            "rate_limited",
            model=target_model,
            surface=surface,
            account_id=failed_id,
        )
        if not has_yielded_data:
            if routing is not None:
                switch("rate_limited")
                logger.info(
                    "429 触发限额，路由管线切换账号 (%d/%d): model=%s",
                    attempt + 1,
                    MAX_RETRIES,
                    target_model,
                )
                return True
            if await try_switch_account(
                model=target_model, failed_account_id=failed_id, surface=surface
            ):
                logger.info(
                    "429 触发限额，切换账号 (%d/%d): model=%s",
                    attempt + 1,
                    MAX_RETRIES,
                    target_model,
                )
                return True
        logger.warning("全部账号在模型 %s 上均处于冷却状态", target_model)
        raise HTTPException(
            429,
            detail={
                "message": f"All accounts reached daily quota for model '{target_model}'. Quota resets at 00:00 PST.",
                "type": "rate_limit_exceeded",
            },
        ) from exc

    if (
        isinstance(exc, RequestError)
        and exc.status == 204
        and attempt == 0
        and not has_yielded_data
    ):
        logger.warning("上游收到 204，清理模板缓存后重试一次")
        client.clear_templates()
        retry("empty_response_204")
        return True

    # Google 上游偶发随机 RPC 路由 404；未产生输出时立即重试，严禁切号。
    if (
        is_transient_rpc_error(exc)
        and not has_yielded_data
        and attempt < MAX_RETRIES - 1
    ):
        logger.warning(
            "检测到上游偶发 404 (Ambiguous RPC method)，当场立即重试 (%d/%d): %s",
            attempt + 1,
            MAX_RETRIES,
            clean_upstream_error_message(str(exc)),
        )
        client.clear_templates()
        retry("transient_rpc_404")
        return True

    if is_browser_session_hang_error(exc):
        logger.warning(
            "模板或 BotGuard 捕获超时 (%d/%d): %s，切换账号重试",
            attempt + 1,
            MAX_RETRIES,
            exc,
        )
        client.clear_templates()
        if routing is not None and route_ctx is not None:
            if not has_yielded_data and route_ctx.budget.can_switch():
                switch("browser_hang")
                logger.info("路由管线切换账号重试 (%d/%d)", attempt + 1, MAX_RETRIES)
                return True
            if attempt < 2 and not has_yielded_data:
                retry("browser_hang")
                if client._session is not None:
                    await client._session._close_internal()
                return True
        if not has_yielded_data and await try_switch_account(
            model=target_model,
            failed_account_id=failed_id,
            is_auth_error=False,
            surface=surface,
        ):
            logger.info("已切换账号重试 (%d/%d)", attempt + 1, MAX_RETRIES)
            return True
        if attempt < 2 and not has_yielded_data:
            if client._session is not None:
                await client._session._close_internal()
            return True
    if isinstance(exc, RequestError):
        clean_msg = clean_upstream_error_message(str(exc))
        status_code = exc.status if exc.status > 0 else 500
        error_type = (
            "bad_request"
            if status_code == 400
            else "not_found"
            if status_code == 404
            else "upstream_error"
        )
        logger.warning("上游请求错误 (%d): %s", status_code, clean_msg)
        raise HTTPException(
            status_code, detail={"message": clean_msg, "type": error_type}
        ) from exc

    if isinstance(exc, AistudioError):
        runtime_state.record(target_model, "errors", api_format=api_format)
        record_rotator_event(
            "error", model=target_model, surface=surface, account_id=failed_id
        )
        logger.warning("上游服务端异常: %s", exc)
        raise HTTPException(
            500, detail={"message": str(exc), "type": "server_error"}
        ) from exc

    runtime_state.record(target_model, "errors", api_format=api_format)
    record_rotator_event(
        "error", model=target_model, surface=surface, account_id=failed_id
    )
    logger.error("未知严重异常: %s", exc)
    logger.debug("异常堆栈详情:", exc_info=True)
    raise HTTPException(
        500, detail={"message": str(exc), "type": "server_error"}
    ) from exc
