"""Gemini-compatible application service handlers.

The request path is protocol-neutral: routing, instance acquisition, retry and
tier admission live in :mod:`routing_pipeline`.  This module only builds Gemini
requests/responses and translates domain errors into Gemini payloads.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from fastapi.responses import StreamingResponse

from aistudio_api.api.response_models import (
    GeminiCandidateResponse,
    GeminiContentResponse,
    GeminiGenerateContentResponse,
)
from aistudio_api.api.responses import to_gemini_parts, to_gemini_usage_metadata
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application.chat_service import normalize_gemini_request
from aistudio_api.application.routing_pipeline import (
    RouteAttempt,
    RouteContext,
    RoutingPipelineProtocol,
)
from aistudio_api.application.testing_hooks import forced_429
from aistudio_api.domain.errors import (
    AistudioError,
    AuthError,
    BrowserTransportError,
    RequestError,
    RoutingError,
    SessionExpiredError,
    SurfaceSwitchExhaustedError,
    TierRequiredError,
    UsageLimitExceeded,
    parse_upstream_grpc_error,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("api.gemini")


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
    if isinstance(exc, AistudioError):
        return 500, str(exc), "INTERNAL"
    return 500, str(exc), "INTERNAL"


@dataclass
class _StreamState:
    final_usage: dict[str, object] | None = None
    final_finish_reason: str = "STOP"
    latest_response_id: str | None = None
    buffered_thought_sig: str | None = None
    has_yielded_data: bool = False

    def reset(self) -> None:
        self.final_usage = None
        self.final_finish_reason = "STOP"
        self.latest_response_id = None
        self.buffered_thought_sig = None
        self.has_yielded_data = False


async def _consume_action(
    pipeline: RoutingPipelineProtocol,
    context: RouteContext,
    attempt: RouteAttempt,
    exc: Exception,
    *,
    has_yielded_data: bool,
) -> tuple[str, RouteAttempt | None]:
    action = await pipeline.handle_error(
        context, attempt, exc, has_yielded_data=has_yielded_data
    )
    if action == "switch":
        await pipeline.release(attempt)
        return action, None
    return action, attempt


def _normalized_or_400(req: GeminiGenerateContentRequest, model_path: str):
    try:
        return normalize_gemini_request(req, model_path)
    except ValueError as exc:
        raise RequestError(400, str(exc)) from exc


async def handle_gemini_generate_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    *,
    stream: bool,
    pipeline: RoutingPipelineProtocol,
):
    context = await pipeline.begin(model_path=model_path)
    if stream:
        return _build_gemini_streaming_response(
            pipeline=pipeline, context=context, req=req
        )
    return await _generate_non_streaming(
        pipeline=pipeline, context=context, req=req
    )


async def _generate_non_streaming(
    *,
    pipeline: RoutingPipelineProtocol,
    context: RouteContext,
    req: GeminiGenerateContentRequest,
) -> GeminiGenerateContentResponse:
    attempt: RouteAttempt | None = None
    last_error: Exception | None = None
    try:
        while True:
            if attempt is None:
                attempt = await pipeline.acquire(context)
                if attempt is None:
                    break
            normalized = _normalized_or_400(req, context.route.model)
            if forced_429(
                account_id=attempt.account_id,
                model=normalized.model,
                surface=context.route.surface,
            ):
                exc = UsageLimitExceeded("forced 429 (AISTUDIO_TEST_HOOKS)")
                last_error = exc
                action, attempt = await _consume_action(
                    pipeline, context, attempt, exc, has_yielded_data=False
                )
                if action == "retry_same":
                    continue
                if action == "switch":
                    continue
                raise exc
            try:
                output = await attempt.client.generate_content(
                    model=normalized.model,
                    capture_prompt=normalized.capture_prompt,
                    capture_images=normalized.capture_images,
                    contents=normalized.contents,
                    system_instruction_content=normalized.system_instruction,
                    tools=normalized.tools,
                    tool_config=normalized.tool_config,
                    safety_settings=normalized.safety_settings,
                    temperature=normalized.temperature,
                    top_p=normalized.top_p,
                    top_k=normalized.top_k,
                    max_tokens=normalized.max_tokens,
                    generation_config_overrides=normalized.generation_config_overrides,
                    sanitize_plain_text=False,
                    force_refresh_capture=attempt.same_retries > 0,
                )
            except Exception as exc:
                last_error = exc
                action, attempt = await _consume_action(
                    pipeline, context, attempt, exc, has_yielded_data=False
                )
                if action == "retry_same":
                    continue
                if action == "switch":
                    continue
                raise
            final_fr = to_gemini_finish_reason(
                output.candidates[0].finish_reason if output.candidates else None
            )
            pipeline.record_success(
                context, attempt, usage=output.usage, finish_reason=final_fr
            )
            logger.info(
                "Gemini 生成完成: model=%s, instance=%s",
                normalized.model,
                attempt.instance_id,
            )
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
                        finishReason=to_gemini_finish_reason(
                            output.candidates[0].finish_reason
                            if output.candidates
                            else None
                        ),
                        index=0,
                    )
                ],
                usageMetadata=to_gemini_usage_metadata(output.usage),
                modelVersion=normalized.model,
                responseId=output.response_id or None,
            )
    finally:
        if attempt is not None:
            await pipeline.release(attempt)

    if isinstance(last_error, UsageLimitExceeded):
        raise SurfaceSwitchExhaustedError(
            surface=context.route.surface
        ) from last_error
    if last_error is not None:
        raise last_error
    raise SurfaceSwitchExhaustedError(surface=context.route.surface)


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


def _build_gemini_streaming_response(
    *,
    pipeline: RoutingPipelineProtocol,
    context: RouteContext,
    req: GeminiGenerateContentRequest,
) -> StreamingResponse:
    async def stream_response() -> AsyncGenerator[str, None]:
        attempt: RouteAttempt | None = None
        last_error: Exception | None = None
        normalized = None
        state = _StreamState()
        stream_completed = False
        try:
            while True:
                if attempt is None:
                    attempt = await pipeline.acquire(context)
                    if attempt is None:
                        break
                normalized = _normalized_or_400(req, context.route.model)
                state.reset()
                if forced_429(
                    account_id=attempt.account_id,
                    model=normalized.model,
                    surface=context.route.surface,
                ):
                    exc = UsageLimitExceeded("forced 429 (AISTUDIO_TEST_HOOKS)")
                    last_error = exc
                    action, attempt = await _consume_action(
                        pipeline, context, attempt, exc, has_yielded_data=False
                    )
                    if action in ("retry_same", "switch"):
                        continue
                    raise exc
                try:
                    async for event_type, text in attempt.client.stream_generate_content(
                        model=normalized.model,
                        capture_prompt=normalized.capture_prompt,
                        capture_images=normalized.capture_images,
                        contents=normalized.contents,
                        system_instruction_content=normalized.system_instruction,
                        tools=normalized.tools,
                        tool_config=normalized.tool_config,
                        safety_settings=normalized.safety_settings,
                        temperature=normalized.temperature,
                        top_p=normalized.top_p,
                        top_k=normalized.top_k,
                        max_tokens=normalized.max_tokens,
                        generation_config_overrides=normalized.generation_config_overrides,
                        sanitize_plain_text=False,
                        force_refresh_capture=attempt.same_retries > 0,
                    ):
                        if event_type == "usage":
                            state.final_usage = text if isinstance(text, dict) else None
                        elif event_type == "finish_reason":
                            state.final_finish_reason = (
                                to_gemini_finish_reason(text)
                                if isinstance(text, int)
                                else str(text)
                            )
                        elif event_type == "response_id":
                            state.latest_response_id = str(text)
                        elif event_type == "thought_signature":
                            state.buffered_thought_sig = str(text)
                        else:
                            chunk = format_sse_event(
                                event_type,
                                text,
                                model_version=normalized.model,
                                response_id=state.latest_response_id,
                                thought_signature=state.buffered_thought_sig,
                            )
                            state.buffered_thought_sig = None
                            if chunk:
                                state.has_yielded_data = True
                                yield chunk
                except Exception as exc:
                    last_error = exc
                    action, attempt = await _consume_action(
                        pipeline,
                        context,
                        attempt,
                        exc,
                        has_yielded_data=state.has_yielded_data,
                    )
                    if action == "retry_same":
                        continue
                    if action == "switch":
                        continue
                    raise
                stream_completed = True
                break

            if not stream_completed:
                if isinstance(last_error, UsageLimitExceeded):
                    raise SurfaceSwitchExhaustedError(
                        surface=context.route.surface
                    ) from last_error
                if last_error is not None:
                    raise last_error
                raise SurfaceSwitchExhaustedError(surface=context.route.surface)
            target_model = normalized.model if normalized else context.route.model
            if attempt is not None:
                pipeline.record_success(
                    context,
                    attempt,
                    usage=state.final_usage,
                    finish_reason=state.final_finish_reason,
                )
            yield format_sse_usage(
                state.final_usage,
                finish_reason=state.final_finish_reason,
                model_version=target_model,
                response_id=state.latest_response_id,
            )
        except Exception as exc:
            yield format_sse_error(exc)
        finally:
            if attempt is not None:
                await pipeline.release(attempt)

    return StreamingResponse(
        stream_response(),
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
