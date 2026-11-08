"""OpenAI Responses compatible routes (``POST /v1/responses``).

The two endpoints are registered on the app and share the existing auth,
keepalive and four-protocol error adapters; defaults ship enabled and can be
turned off explicitly via ``features.formats.responses``.
"""

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
from aistudio_api.application.contracts.gemini import GeminiGenerateContentRequest
from aistudio_api.application.contracts.responses import ResponsesRequest
from aistudio_api.application.format_converters.common import ConversionError
from aistudio_api.application.format_converters.media import MediaFetcher
from aistudio_api.application.format_converters.responses_request import (
    convert_responses_request,
)
from aistudio_api.application.format_converters.responses_response import (
    ResponsesStreamRenderer,
    responses_response,
)
from aistudio_api.application.generation_service import (
    StreamEvent,
    generate,
    stream_generation,
)
from aistudio_api.application.routing_pipeline import RoutingPipeline
from aistudio_api.application.token_counter import count_tokens
from aistudio_api.domain.errors import RequestError

router = APIRouter()


def require_responses() -> None:
    require_format_enabled("responses")


def strip_responses_model_prefix(model: str) -> str:
    """Strip only ``models/`` / ``chat/`` prefixes; never invent aliases."""
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


def _request_error(message: str) -> RequestError:
    return RequestError(502, message)


async def _build_gemini_request(
    payload: ResponsesRequest,
) -> tuple[str, GeminiGenerateContentRequest]:
    media = MediaFetcher(allow_private=format_media_allow_private())
    body = await convert_responses_request(payload, media)
    request = GeminiGenerateContentRequest.model_validate(body)
    return strip_responses_model_prefix(payload.model), request


@router.post(
    "/v1/responses",
    dependencies=[Depends(require_responses), Depends(require_api_key)],
)
async def create_response(
    request: Request,
    pipeline: RoutingPipeline = Depends(get_routing_pipeline),
) -> Response:
    try:
        raw: object = await request.json()
    except Exception:
        return format_error_response(
            ConversionError("request body is not valid JSON"), "responses"
        )

    try:
        payload = ResponsesRequest.model_validate(raw)
    except ValidationError as exc:
        return format_error_response(_validation_error(exc), "responses")

    try:
        model_path, gemini_request = await _build_gemini_request(payload)

        if not payload.stream:
            outcome = await generate(
                model_path=model_path,
                req=gemini_request,
                pipeline=pipeline,
                api_format="responses",
            )
            return JSONResponse(responses_response(model=model_path, outcome=outcome))

        events: KeepaliveIterator[StreamEvent] = KeepaliveIterator(
            stream_generation(
                model_path=model_path,
                req=gemini_request,
                pipeline=pipeline,
                api_format="responses",
            ),
            interval=keepalive_interval_sec(),
        )

        # Phase A: bounded wait for the first real event.  Errors and an
        # exhausted stream before the deadline keep a real HTTP status; a slow
        # upstream switches to HTTP 200 plus ``: keepalive`` comments.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + preread_window_sec()
        first_event: StreamEvent | None = None
        try:
            while first_event is None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                event = await events.next_event(remaining)
                if isinstance(event, KeepaliveTimeout):
                    break
                candidate = cast("StreamEvent", event)
                if candidate.kind == "routing_wait":
                    continue
                first_event = candidate
        except StopAsyncIteration:
            with contextlib.suppress(Exception):
                await events.aclose()
            return format_error_response(
                _request_error("generation produced no stream events"),
                "responses",
            )
        except Exception as exc:
            with contextlib.suppress(Exception):
                await events.aclose()
            return format_error_response(exc, "responses")

        renderer = ResponsesStreamRenderer(model_path)

        async def event_stream() -> AsyncIterator[str]:
            try:
                yield renderer.start()
                if first_event is not None:
                    chunk = renderer.render(first_event)
                    if chunk:
                        yield chunk
                else:
                    yield KEEPALIVE_FRAME
                async for item in events:
                    if item is KEEPALIVE_SENTINEL:
                        yield KEEPALIVE_FRAME
                        continue
                    rendered = renderer.render(cast("StreamEvent", item))
                    if rendered:
                        yield rendered
                if not renderer.done:
                    tail = renderer.close()
                    if tail:
                        yield tail
            except Exception as exc:
                yield renderer.failed(exc)
            finally:
                await events.aclose()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    except Exception as exc:
        return format_error_response(exc, "responses")


@router.post(
    "/v1/responses/input_tokens",
    dependencies=[Depends(require_responses), Depends(require_api_key)],
)
async def count_response_input_tokens(
    request: Request,
    pipeline: RoutingPipeline = Depends(get_routing_pipeline),
) -> Response:
    try:
        raw: object = await request.json()
    except Exception:
        return format_error_response(
            ConversionError("request body is not valid JSON"), "responses"
        )

    try:
        payload = ResponsesRequest.model_validate(raw)
    except ValidationError as exc:
        return format_error_response(_validation_error(exc), "responses")

    try:
        model_path, gemini_request = await _build_gemini_request(payload)
        outcome = await count_tokens(
            model_path=model_path,
            req=gemini_request,
            pipeline=pipeline,
            api_format="responses",
        )
    except Exception as exc:
        return format_error_response(exc, "responses")

    return JSONResponse(
        {
            "object": "response.input_tokens",
            "input_tokens": outcome.total_tokens,
        }
    )


__all__ = [
    "count_response_input_tokens",
    "create_response",
    "require_responses",
    "router",
    "strip_responses_model_prefix",
]
