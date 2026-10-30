"""Shared FastAPI dependencies."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

from aistudio_api.config import settings
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.utils.logger import get_logger

from .state import runtime_state

logger = get_logger("auth")


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
    authorization = (request.headers.get("authorization") or "").strip()
    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None

    token = token.strip()
    return token or None


def require_web_auth(request: Request) -> None:
    """保护管理控制台路由 (通过环境变量 AISTUDIO_WEB_PASSWORD 配置密码)。"""
    if not settings.auth_enabled:
        return

    token = _extract_request_token(request)
    if (
        token
        and settings.web_password
        and settings.web_password.strip()
        and secrets.compare_digest(token, settings.web_password)
    ):
        return
    client_ip = request.client.host if request.client else "unknown"
    logger.warning("管理控制台鉴权失败: 来自 %s 的密码无效", client_ip)
    raise HTTPException(
        status_code=401,
        detail="管理控制台鉴权失败，请输入正确的管理密码",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_api_key(request: Request) -> None:
    """保护对外 Gemini API 路由 (通过 config.yaml 中的 api_keys 配置)。"""
    from aistudio_api.infrastructure.gateway.model_defaults import (
        get_configured_api_keys,
        get_configured_security_settings,
    )

    if get_configured_security_settings():
        return

    configured_keys = get_configured_api_keys()
    all_valid_keys = configured_keys | settings.api_keys
    client_ip = request.client.host if request.client else "unknown"

    # 空 Key 绝不开放：未配置 Key 且免密开关关闭时一律拒绝
    if not all_valid_keys:
        logger.warning(
            "API 鉴权失败: 未配置 API Key 且免密开关关闭，拒绝来自 %s 的请求",
            client_ip,
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
        logger.warning("API 鉴权失败: 来自 %s 的请求缺少 API Key", client_ip)
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

    logger.warning("API 鉴权失败: 来自 %s 的 API Key 无效", client_ip)
    raise HTTPException(
        status_code=401,
        detail={
            "message": "提供的 API Key 无效",
            "type": "authentication_error",
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_client() -> AIStudioClient:
    if runtime_state.client is None:
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
