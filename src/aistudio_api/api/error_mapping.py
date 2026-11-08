"""Protocol error mapping for routing/tier domain errors (D-04/D-06).

Application-layer code raises domain errors only.  These adapters render the
same facts for Gemini, OpenAI Chat Completions, Claude Messages and OpenAI
Responses.  Tier insufficiency is always HTTP 403 with
``tier/model/required_tier/current_tier`` and is never mixed with 429/503.
"""

from __future__ import annotations

import re
from typing import Literal

from aistudio_api.application.atg.extractor import AtgError
from aistudio_api.domain.errors import (
    AistudioError,
    AuthError,
    BrowserTransportError,
    RequestError,
    RoutingError,
    SessionExpiredError,
    TierRequiredError,
    UsageLimitExceeded,
    parse_upstream_grpc_error,
)

ProtocolName = Literal["gemini", "openai", "claude", "responses"]


def clean_upstream_error_message(raw_msg: str) -> str:
    """Extract a clean, readable error message from Google/JSPB upstream error strings."""
    if not raw_msg:
        return ""
    text = raw_msg.strip()
    if "[,[" not in text and "[null,[" not in text and not text.startswith("[,"):
        return text
    inner = text
    while True:
        m = re.match(r"^HTTP\s+\d+:\s*(.*)$", inner)
        if m:
            inner = m.group(1).strip()
        else:
            break
    _, msg = parse_upstream_grpc_error(inner)
    return msg or inner


def classify_gemini_error_payload(exc: Exception) -> tuple[int, str, str]:
    """Extract standard Gemini HTTP status code, message, and status string."""
    if isinstance(exc, TierRequiredError):
        return 403, str(exc), "PERMISSION_DENIED"
    if isinstance(exc, RoutingError):
        status = int(getattr(exc, "status_code", 503))
        status_text = "UNAVAILABLE" if status >= 500 else "PERMISSION_DENIED"
        return status, str(exc), status_text
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
    if isinstance(exc, ValueError):
        return 400, str(exc), "INVALID_ARGUMENT"
    if isinstance(exc, RequestError):
        status = exc.status if exc.status > 0 else 500
        clean_msg = clean_upstream_error_message(str(exc))
        status_map = {
            400: "INVALID_ARGUMENT",
            401: "UNAUTHENTICATED",
            403: "PERMISSION_DENIED",
            404: "NOT_FOUND",
            429: "RESOURCE_EXHAUSTED",
            500: "INTERNAL",
            502: "UNAVAILABLE",
            503: "UNAVAILABLE",
        }
        return (
            status,
            clean_msg,
            status_map.get(status, "INTERNAL" if status >= 500 else "UNKNOWN"),
        )
    if isinstance(exc, BrowserTransportError):
        return 502, str(exc), "UNAVAILABLE"
    if isinstance(exc, AtgError):
        return 502, str(exc), "UNAVAILABLE"
    if isinstance(exc, AistudioError):
        return 500, str(exc), "INTERNAL"
    return 500, str(exc), "INTERNAL"


def _openai_error(
    *,
    message: str,
    error_type: str,
    code: object,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    error: dict[str, object] = {
        "message": message,
        "type": error_type,
        "code": code,
        "param": None,
    }
    if extra:
        error.update(extra)
    return {"error": error}


def _claude_error(
    *,
    message: str,
    error_type: str,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    error: dict[str, object] = {"type": error_type, "message": message}
    if extra:
        error.update(extra)
    return {"type": "error", "error": error}


def error_payload(
    exc: Exception, protocol: ProtocolName
) -> tuple[int, dict[str, object]]:
    """Return ``(http_status, json_body)`` for one protocol."""
    if isinstance(exc, TierRequiredError):
        fields: dict[str, object] = dict(exc.as_fields())
        message = str(exc)
        if protocol == "gemini":
            return 403, {
                "error": {
                    "code": 403,
                    "message": message,
                    "status": "PERMISSION_DENIED",
                    **fields,
                }
            }
        if protocol == "claude":
            return 403, _claude_error(
                message=message, error_type="permission_error", extra=fields
            )
        return 403, _openai_error(
            message=message,
            error_type="tier_required",
            code="tier_required",
            extra=fields,
        )

    if isinstance(exc, RoutingError):
        status = int(getattr(exc, "status_code", 503))
        message = str(exc)
        fields = dict(exc.as_fields())
        field_values: dict[str, object] = dict(fields)
        # RoutingError.as_fields() carries its own ``type``/``reason``; OpenAI
        # and Claude reserve ``error.type`` for the protocol-level error class.
        if protocol != "gemini":
            field_values.pop("type", None)
        if protocol == "gemini":
            status_text = "UNAVAILABLE" if status >= 500 else "PERMISSION_DENIED"
            return status, {
                "error": {
                    "code": status,
                    "message": message,
                    "status": status_text,
                    **field_values,
                }
            }
        if protocol == "claude":
            return status, _claude_error(
                message=message,
                error_type="api_error",
                extra={"code": exc.code, **field_values},
            )
        return status, _openai_error(
            message=message,
            error_type="service_unavailable" if status >= 500 else "invalid_request",
            code=exc.code,
            extra=field_values,
        )

    code, message, status_text = classify_gemini_error_payload(exc)
    if protocol == "gemini":
        return code, {
            "error": {"code": code, "message": message, "status": status_text}
        }
    error_type = {
        400: "invalid_request_error",
        401: "authentication_error",
        403: "permission_denied",
        404: "not_found_error",
        429: "rate_limit_exceeded",
    }.get(code, "api_error")
    if protocol == "claude":
        claude_type = {
            400: "invalid_request_error",
            401: "authentication_error",
            403: "permission_error",
            404: "not_found_error",
            429: "rate_limit_error",
        }.get(code, "api_error")
        return code, _claude_error(message=message, error_type=claude_type)
    return code, _openai_error(message=message, error_type=error_type, code=code)


__all__ = [
    "ProtocolName",
    "classify_gemini_error_payload",
    "clean_upstream_error_message",
    "error_payload",
]
