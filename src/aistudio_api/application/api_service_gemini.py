"""Gemini-compatible application service handlers.

The protocol-neutral generation chain (routing, instance acquisition, retry,
ATG wrapping/unwrapping and signature production) lives in
:mod:`aistudio_api.application.generation_service`; this module only renders
Gemini wire shapes and translates domain errors into Gemini payloads.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator

from fastapi.responses import StreamingResponse

from aistudio_api.api.response_models import (
    GeminiCandidateResponse,
    GeminiContentResponse,
    GeminiGenerateContentResponse,
)
from aistudio_api.api.responses import to_gemini_parts, to_gemini_usage_metadata
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application import generation_service
from aistudio_api.application.atg.extractor import AtgError
from aistudio_api.application.generation_service import StreamEvent
from aistudio_api.application.routing_pipeline import RoutingPipelineProtocol
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

WIRE_TO_GEMINI_FINISH_REASON: dict[int, str] = {
    0: "FINISH_REASON_UNSPECIFIED",
    1: "STOP",
    2: "MAX_TOKENS",
    3: "SAFETY",
    4: "RECITATION",
    5: "OTHER",
    6: "LANGUAGE",
    7: "BLOCKLIST",
    8: "PROHIBITED_CONTENT",
    9: "SPII",
    10: "MALFORMED_FUNCTION_CALL",
    11: "IMAGE_SAFETY",
    12: "UNEXPECTED_TOOL_CALL",
    13: "TOO_MANY_TOOL_CALLS",
    14: "IMAGE_PROHIBITED_CONTENT",
    15: "IMAGE_OTHER",
    16: "NO_IMAGE",
    17: "IMAGE_RECITATION",
    18: "MISSING_THOUGHT_SIGNATURE",
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


async def handle_gemini_generate_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    *,
    stream: bool,
    pipeline: RoutingPipelineProtocol,
):
    if stream:
        return await _build_gemini_streaming_response(
            pipeline=pipeline, req=req, model_path=model_path
        )

    try:
        outcome = await generation_service.generate(
            model_path=model_path,
            req=req,
            pipeline=pipeline,
            api_format="gemini",
        )
    except AtgError as exc:
        raise RequestError(502, str(exc)) from exc
    output = outcome.output
    return GeminiGenerateContentResponse(
        candidates=[
            GeminiCandidateResponse(
                content=GeminiContentResponse(
                    parts=to_gemini_parts(
                        output.text,
                        function_calls=output.function_calls,
                        function_responses=output.function_responses,
                        thinking=output.thinking,
                        images=output.images,
                        reasoning_images=output.reasoning_images,
                    ),
                ),
                finishReason=outcome.finish_reason,
                index=0,
            )
        ],
        usageMetadata=to_gemini_usage_metadata(output.usage),
        modelVersion=outcome.model,
        responseId=outcome.response_id,
    )


def format_sse_event(
    event_type: str,
    text: object,
    *,
    model_version: str | None = None,
    response_id: str | None = None,
    thought_signature: str | None = None,
) -> str | None:
    """Format a single Gemini streaming event into SSE chunk string."""
    if not text:
        return None

    parts: list[dict[str, object]] = []
    if event_type == "body":
        p: dict[str, object] = {"text": str(text)}
        if thought_signature:
            p["thoughtSignature"] = thought_signature
        parts.append(p)
    elif event_type == "thinking":
        p = {"text": str(text), "thought": True}
        if thought_signature:
            p["thoughtSignature"] = thought_signature
        parts.append(p)
    elif event_type == "tool_calls":
        fc_list = text if isinstance(text, list) else []
        parts = [
            part.model_dump(mode="json", exclude_none=True)
            for part in to_gemini_parts("", function_calls=fc_list)
        ]
        if thought_signature and parts:
            parts[0]["thoughtSignature"] = thought_signature
    elif event_type == "images":
        img_list = text if isinstance(text, list) else []
        parts = [
            part.model_dump(mode="json", exclude_none=True)
            for part in to_gemini_parts("", images=img_list)
        ]
        if thought_signature and parts:
            parts[0]["thoughtSignature"] = thought_signature
    elif event_type == "reasoning_images":
        r_img_list = text if isinstance(text, list) else []
        parts = [
            part.model_dump(mode="json", exclude_none=True)
            for part in to_gemini_parts("", reasoning_images=r_img_list)
        ]
        if thought_signature and parts:
            parts[0]["thoughtSignature"] = thought_signature
    else:
        return None

    payload: dict[str, object] = {
        "candidates": [{"content": {"role": "model", "parts": parts}, "index": 0}]
    }
    if model_version:
        payload["modelVersion"] = model_version
    if response_id:
        payload["responseId"] = response_id
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def format_sse_usage(
    final_usage: dict[str, object] | None,
    *,
    finish_reason: str = "STOP",
    model_version: str | None = None,
    response_id: str | None = None,
) -> str:
    """Format final completion usage metadata into SSE chunk string."""
    effective_usage = final_usage or {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    usage_dict = to_gemini_usage_metadata(effective_usage).model_dump(
        mode="json", exclude_none=True
    )
    payload: dict[str, object] = {
        "candidates": [
            {
                "content": {"role": "model", "parts": []},
                "finishReason": finish_reason,
                "index": 0,
            }
        ],
        "usageMetadata": usage_dict,
    }
    if model_version:
        payload["modelVersion"] = model_version
    if response_id:
        payload["responseId"] = response_id
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def format_sse_error(exc: Exception) -> str:
    """Format exception into standard Gemini SSE error chunk."""
    code, msg, status_str = classify_gemini_error_payload(exc)
    error: dict[str, object] = {
        "code": code,
        "message": msg,
        "status": status_str,
    }
    if isinstance(exc, (TierRequiredError, RoutingError)):
        error.update(exc.as_fields())
    return (
        "data: "
        + json.dumps({"error": error}, ensure_ascii=False)
        + "\n\n"
    )


def _render_stream_event(event: StreamEvent) -> str | None:
    if event.kind == "routing_wait":
        return None
    if event.kind == "usage":
        usage = event.payload if isinstance(event.payload, dict) else None
        return format_sse_usage(
            usage,
            finish_reason=event.finish_reason or "STOP",
            model_version=event.model,
            response_id=event.response_id,
        )
    return format_sse_event(
        event.kind,
        event.payload,
        model_version=event.model,
        response_id=event.response_id,
        thought_signature=event.thought_signature,
    )


async def _build_gemini_streaming_response(
    *,
    pipeline: RoutingPipelineProtocol,
    req: GeminiGenerateContentRequest,
    model_path: str,
) -> StreamingResponse:
    events = generation_service.stream_generation(
        model_path=model_path,
        req=req,
        pipeline=pipeline,
        api_format="gemini",
    )
    first_chunk: str | None = None
    try:
        while first_chunk is None:
            event = await events.__anext__()
            first_chunk = _render_stream_event(event)
    except StopAsyncIteration:
        await events.aclose()
        # A streaming request must never answer HTTP 200 with an empty SSE
        # body: clients that wait for the first ``data:`` frame observe "200 +
        # 0 events".  Surface this as a real upstream/protocol error instead.
        raise RequestError(502, "generation produced no stream events") from None
    except BrowserTransportError as exc:
        await events.aclose()
        raise RequestError(502, str(exc)) from exc
    except AtgError as exc:
        await events.aclose()
        raise RequestError(502, str(exc)) from exc
    except Exception:
        await events.aclose()
        raise

    assert first_chunk is not None

    async def body() -> AsyncGenerator[str, None]:
        try:
            yield first_chunk
            async for event in events:
                chunk = _render_stream_event(event)
                if chunk:
                    yield chunk
        except Exception as exc:
            yield format_sse_error(exc)
        finally:
            await events.aclose()

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = [
    "classify_gemini_error_payload",
    "clean_upstream_error_message",
    "format_sse_error",
    "format_sse_event",
    "format_sse_usage",
    "handle_gemini_generate_content",
    "to_gemini_finish_reason",
]
