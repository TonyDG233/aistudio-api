"""Protocol error mapping for routing/tier domain errors (D-04/D-06).

Application-layer code raises domain errors only.  These adapters render the
same facts for Gemini, OpenAI Chat Completions, Claude Messages and OpenAI
Responses.  Tier insufficiency is always HTTP 403 with
``tier/model/required_tier/current_tier`` and is never mixed with 429/503.
"""

from __future__ import annotations

from typing import Literal

from aistudio_api.domain.errors import RoutingError, TierRequiredError

ProtocolName = Literal["gemini", "openai", "claude", "responses"]


def _generic_gemini(exc: Exception) -> tuple[int, str, str]:
    from aistudio_api.application.api_service_gemini import (
        classify_gemini_error_payload,
    )

    return classify_gemini_error_payload(exc)


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

    code, message, status_text = _generic_gemini(exc)
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


__all__ = ["ProtocolName", "error_payload"]
