"""Protocol-aware error responses and format-face feature gates."""

from __future__ import annotations

import json
from typing import Literal

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from aistudio_api.application.format_converters import ConversionError
from aistudio_api.application.surface_orchestrator import SurfacePolicyError
from aistudio_api.application.upstream_errors import classify_gemini_error_payload

ApiFormat = Literal["gemini", "openai", "claude"]

_OPENAI_TYPE_BY_STATUS: dict[int, str] = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    413: "invalid_request_error",
    422: "invalid_request_error",
    429: "rate_limit_error",
    500: "api_error",
    503: "service_unavailable",
    504: "timeout_error",
}

_CLAUDE_TYPE_BY_STATUS: dict[int, str] = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    413: "request_too_large",
    422: "invalid_request_error",
    429: "rate_limit_error",
    500: "api_error",
    503: "overloaded_error",
    504: "timeout_error",
    529: "overloaded_error",
}

_GEMINI_STATUS_BY_HTTP: dict[int, str] = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    429: "RESOURCE_EXHAUSTED",
    500: "INTERNAL",
    503: "UNAVAILABLE",
    504: "DEADLINE_EXCEEDED",
}


def format_for_path(path: str) -> ApiFormat:
    if path.startswith("/v1/chat/completions") or path == "/v1/models":
        return "openai"
    if path.startswith("/v1/models/"):
        return "openai"
    if path.startswith("/v1/messages"):
        return "claude"
    return "gemini"


def format_enabled(api_format: ApiFormat) -> bool:
    try:
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_features_config,
        )

        features = get_features_config()
    except Exception:
        return False
    formats = features.get("formats")
    if not isinstance(formats, dict):
        return False
    return bool(formats.get(api_format, False))


def format_media_allow_private() -> bool:
    try:
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_features_config,
        )

        features = get_features_config()
    except Exception:
        return False
    formats = features.get("formats")
    if not isinstance(formats, dict):
        return False
    media = formats.get("media")
    if not isinstance(media, dict):
        return False
    return bool(media.get("allow_private_urls", False))


def require_format_enabled(api_format: ApiFormat) -> None:
    if format_enabled(api_format):
        return
    raise HTTPException(
        403,
        detail={
            "message": (
                f"{api_format} format face is disabled; set "
                f"features.formats.{api_format}=true in config.yaml"
            ),
            "type": "feature_disabled",
        },
    )


def _validation_message(exc: RequestValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ()))
        message = str(error.get("msg", "invalid value"))
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts) or "request validation failed"


def _type_for_status(status: int, api_format: ApiFormat) -> str:
    if api_format == "openai":
        return _OPENAI_TYPE_BY_STATUS.get(status, "api_error")
    if api_format == "claude":
        return _CLAUDE_TYPE_BY_STATUS.get(status, "api_error")
    return "INTERNAL"


def classify_format_error(
    exc: Exception,
    api_format: ApiFormat,
) -> tuple[int, str, str, str | None]:
    """Return (status, error_type, message, param)."""
    if isinstance(exc, SurfacePolicyError):
        status = exc.http_status
        return status, _type_for_status(status, api_format), str(exc), None
    if isinstance(exc, RequestValidationError):
        return (
            400,
            _type_for_status(400, api_format),
            _validation_message(exc),
            None,
        )

    status, message, _ = classify_gemini_error_payload(exc)
    if status <= 0:
        status = 500
    param = exc.field if isinstance(exc, ConversionError) else None
    return status, _type_for_status(status, api_format), message, param


def openai_error_body(
    status: int,
    message: str,
    *,
    error_type: str | None = None,
    param: str | None = None,
) -> dict[str, object]:
    return {
        "error": {
            "message": message,
            "type": error_type or _OPENAI_TYPE_BY_STATUS.get(status, "api_error"),
            "code": None,
            "param": param,
        }
    }


def claude_error_body(
    status: int,
    message: str,
    *,
    error_type: str | None = None,
) -> dict[str, object]:
    return {
        "type": "error",
        "error": {
            "type": error_type or _CLAUDE_TYPE_BY_STATUS.get(status, "api_error"),
            "message": message,
        },
    }


def format_error_response(
    exc: Exception,
    api_format: ApiFormat,
) -> JSONResponse:
    status, error_type, message, param = classify_format_error(exc, api_format)
    if api_format == "openai":
        body = openai_error_body(status, message, error_type=error_type, param=param)
    elif api_format == "claude":
        body = claude_error_body(status, message, error_type=error_type)
    else:
        body = {
            "error": {
                "code": status,
                "message": message,
                "status": _GEMINI_STATUS_BY_HTTP.get(
                    status, "INTERNAL" if status >= 500 else "UNKNOWN"
                ),
            }
        }
    return JSONResponse(status_code=status, content=body)


def openai_sse_error(exc: Exception) -> str:
    status, error_type, message, _ = classify_format_error(exc, "openai")
    payload = openai_error_body(status, message, error_type=error_type)
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def claude_sse_error(exc: Exception) -> str:
    status, error_type, message, _ = classify_format_error(exc, "claude")
    payload = claude_error_body(status, message, error_type=error_type)
    return "event: error\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
