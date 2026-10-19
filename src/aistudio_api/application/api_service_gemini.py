"""Gemini-compatible application service handlers.

The protocol-neutral work (ATG, retries, account switching, surface policy)
lives in :mod:`aistudio_api.application.generation_service`; this module only
renders Gemini wire shapes.
"""

from __future__ import annotations

import json

from fastapi.responses import StreamingResponse

from aistudio_api.api.response_models import (
    GeminiCandidateResponse,
    GeminiContentResponse,
    GeminiGenerateContentResponse,
)
from aistudio_api.api.responses import to_gemini_parts, to_gemini_usage_metadata
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application.atg import create_atg_processor
from aistudio_api.application.generation_service import (
    StreamEvent,
    _resolve_surface,
    generate,
    stream_generation,
)
from aistudio_api.application.surface_orchestrator import SurfacePolicyError
from aistudio_api.application.upstream_errors import (
    WIRE_TO_GEMINI_FINISH_REASON,
    classify_gemini_error_payload,
    clean_upstream_error_message,
    handle_attempt_exception,
    to_gemini_finish_reason,
)
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.utils.logger import get_logger

__all__ = [
    "WIRE_TO_GEMINI_FINISH_REASON",
    "_resolve_surface",
    "classify_gemini_error_payload",
    "clean_upstream_error_message",
    "format_sse_error",
    "format_sse_event",
    "format_sse_usage",
    "handle_attempt_exception",
    "handle_gemini_generate_content",
    "to_gemini_finish_reason",
]

logger = get_logger("api.gemini")


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
    return (
        "data: "
        + json.dumps(
            {
                "error": {
                    "code": code,
                    "message": msg,
                    "status": status_str,
                }
            },
            ensure_ascii=False,
        )
        + "\n\n"
    )


def _render_stream_event(event: StreamEvent) -> str | None:
    if event.kind == "routing_wait":
        return ": resolving-instance\n\n"
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


async def handle_gemini_generate_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient,
    *,
    stream: bool,
):
    if stream:
        return _build_gemini_streaming_response(
            client=client, req=req, model_path=model_path
        )

    outcome = await generate(
        model_path=model_path,
        req=req,
        client=client,
        api_format="gemini",
        atg_factory=create_atg_processor,
    )
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


def _build_gemini_streaming_response(
    *,
    client: AIStudioClient,
    req: GeminiGenerateContentRequest,
    model_path: str,
) -> StreamingResponse:
    async def stream_response():
        try:
            async for event in stream_generation(
                model_path=model_path,
                req=req,
                client=client,
                api_format="gemini",
                atg_factory=create_atg_processor,
            ):
                chunk = _render_stream_event(event)
                if chunk:
                    yield chunk
        except SurfacePolicyError as exc:
            # Explicit 4xx denial: never swallow, never silently switch surface.
            logger.warning(
                "[surface] 流式请求被策略拒绝: code=%s model=%s advice=%s",
                exc.code,
                model_path,
                exc.advice,
            )
            yield (
                "data: " + json.dumps(exc.to_error_body(), ensure_ascii=False) + "\n\n"
            )
        except Exception as exc:
            yield format_sse_error(exc)

    return StreamingResponse(
        stream_response(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
