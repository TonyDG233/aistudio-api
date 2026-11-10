"""Claude Messages protocol face (``POST /v1/messages``)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Mapping
from typing import Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from aistudio_api.api.dependencies import get_routing_pipeline, require_api_key
from aistudio_api.api.format_errors import (
    claude_sse_error,
    format_error_response,
    format_media_allow_private,
    require_format_enabled,
)
from aistudio_api.api.sse_keepalive import (
    KEEPALIVE_FRAME,
    KEEPALIVE_SENTINEL,
    KeepaliveIterator,
    KeepaliveTimeout,
    keepalive_interval_sec,
    preread_window_sec,
)
from aistudio_api.application.contracts.claude import (
    ClaudeCountTokensRequest,
    ClaudeMessagesRequest,
)
from aistudio_api.application.contracts.gemini import GeminiGenerateContentRequest
from aistudio_api.application.format_converters.claude_request import (
    convert_claude_count_request,
    convert_claude_request,
)
from aistudio_api.application.format_converters.claude_response import (
    ClaudeStreamRenderer,
    claude_response,
)
from aistudio_api.application.format_converters.common import ConversionError
from aistudio_api.application.format_converters.media import MediaFetcher
from aistudio_api.application.generation_service import (
    StreamEvent,
    generate,
    stream_generation,
)
from aistudio_api.application.routing_pipeline import RoutingPipeline
from aistudio_api.application.token_counter import count_tokens as count_prompt_tokens
from aistudio_api.domain.errors import RequestError

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def require_claude() -> None:
    require_format_enabled("claude")


def _strip_model_prefix(model: str) -> str:
    """Strip repeated ``models/`` / ``chat/`` prefixes (no alias mapping)."""
    value = model.strip()
    while True:
        for prefix in ("models/", "chat/"):
            if value.startswith(prefix):
                value = value[len(prefix) :]
                break
        else:
            return value


def _validation_error(exc: ValidationError) -> ConversionError:
    errors = exc.errors()
    if not errors:
        return ConversionError("request validation failed")
    first: Mapping[str, Any] = errors[0]
    location = ".".join(str(item) for item in first.get("loc", ()))
    message = str(first.get("msg", "invalid value"))
    text = f"{location}: {message}" if location else message
    return ConversionError(text, field=location or None)


async def _streaming_response(
    *,
    gemini_request: GeminiGenerateContentRequest,
    model_path: str,
    pipeline: RoutingPipeline,
) -> Response:
    events: KeepaliveIterator[StreamEvent] = KeepaliveIterator(
        stream_generation(
            model_path=model_path,
            req=gemini_request,
            pipeline=pipeline,
            api_format="claude",
        ),
        interval=keepalive_interval_sec(),
    )

    # Phase A: bounded wait for the first renderable event.  Errors before this
    # point keep a real HTTP status; a slow upstream switches to 200 + keepalive.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + preread_window_sec()
    first_event: StreamEvent | None = None
    saw_routing_wait = False
    try:
        while first_event is None:
            remaining = deadline - loop.time()
            if remaining <= 0 and not saw_routing_wait:
                break
            saw_routing_wait = False
            event = await events.next_event(max(0.0, remaining))
            if isinstance(event, KeepaliveTimeout):
                break
            candidate = cast("StreamEvent", event)
            if candidate.kind in {"routing_wait", "usage"}:
                # ``routing_wait`` is progress and ``usage`` is the terminal
                # marker: neither starts a Claude content stream.
                if remaining <= 0:
                    break
                saw_routing_wait = True
                continue
            first_event = candidate
    except StopAsyncIteration:
        with contextlib.suppress(Exception):
            await events.aclose()
        return format_error_response(
            RequestError(502, "generation produced no stream events"),
            "claude",
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            await events.aclose()
        return format_error_response(exc, "claude")

    renderer = ClaudeStreamRenderer(model_path)

    async def event_stream() -> AsyncIterator[str]:
        rendered_any = False
        try:
            yield renderer.start()
            if first_event is not None:
                chunk = renderer.render(first_event)
                rendered_any = rendered_any or bool(chunk)
                if chunk:
                    yield chunk
            else:
                yield KEEPALIVE_FRAME
            async for item in events:
                if item is KEEPALIVE_SENTINEL:
                    yield KEEPALIVE_FRAME
                    continue
                event = cast("StreamEvent", item)
                rendered = renderer.render(event)
                if event.kind != "routing_wait":
                    rendered_any = rendered_any or bool(rendered)
                if rendered:
                    yield rendered
            if rendered_any:
                tail = renderer.close()
                if tail:
                    yield tail
            else:
                yield claude_sse_error(
                    RequestError(502, "generation produced no stream events")
                )
        except Exception as exc:
            yield claude_sse_error(exc)
        finally:
            await events.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post(
    "/v1/messages",
    dependencies=[Depends(require_claude), Depends(require_api_key)],
)
async def create_message(
    request: Request,
    pipeline: RoutingPipeline = Depends(get_routing_pipeline),
) -> Response:
    try:
        raw: object = await request.json()
    except Exception:
        return format_error_response(
            ConversionError("request body is not valid JSON"), "claude"
        )

    try:
        payload = ClaudeMessagesRequest.model_validate(raw)
    except ValidationError as exc:
        return format_error_response(_validation_error(exc), "claude")

    try:
        media = MediaFetcher(allow_private=format_media_allow_private())
        body = await convert_claude_request(payload, media)
        gemini_request = GeminiGenerateContentRequest.model_validate(body)
        model_path = _strip_model_prefix(payload.model)

        if not payload.stream:
            outcome = await generate(
                model_path=model_path,
                req=gemini_request,
                pipeline=pipeline,
                api_format="claude",
            )
            return JSONResponse(claude_response(model=model_path, outcome=outcome))

        return await _streaming_response(
            gemini_request=gemini_request,
            model_path=model_path,
            pipeline=pipeline,
        )
    except Exception as exc:
        return format_error_response(exc, "claude")


@router.post(
    "/v1/messages/count_tokens",
    dependencies=[Depends(require_claude), Depends(require_api_key)],
)
async def count_message_tokens(
    request: Request,
    pipeline: RoutingPipeline = Depends(get_routing_pipeline),
) -> Response:
    try:
        raw: object = await request.json()
    except Exception:
        return format_error_response(
            ConversionError("request body is not valid JSON"), "claude"
        )

    try:
        payload = ClaudeCountTokensRequest.model_validate(raw)
    except ValidationError as exc:
        return format_error_response(_validation_error(exc), "claude")

    try:
        media = MediaFetcher(allow_private=format_media_allow_private())
        body = await convert_claude_count_request(payload, media)
        gemini_request = GeminiGenerateContentRequest.model_validate(body)
        model_path = _strip_model_prefix(payload.model)
        outcome = await count_prompt_tokens(
            model_path=model_path,
            req=gemini_request,
            pipeline=pipeline,
            api_format="claude",
        )
    except Exception as exc:
        return format_error_response(exc, "claude")
    return JSONResponse({"input_tokens": outcome.total_tokens})
