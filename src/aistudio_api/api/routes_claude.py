"""Claude Messages protocol face (``POST /v1/messages``)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from aistudio_api.api.dependencies import get_client
from aistudio_api.api.format_errors import (
    claude_sse_error,
    format_error_response,
    format_media_allow_private,
    require_format_enabled,
)
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.api.schemas_claude import (
    ClaudeCountTokensRequest,
    ClaudeMessagesRequest,
)
from aistudio_api.application.format_converters.claude_request import (
    convert_claude_count_request,
    convert_claude_request,
)
from aistudio_api.application.format_converters.claude_response import (
    ClaudeStreamRenderer,
    claude_response,
)
from aistudio_api.application.format_converters.common import (
    ConversionError,
    strip_models_prefix,
)
from aistudio_api.application.format_converters.media import MediaFetcher
from aistudio_api.application.generation_service import (
    StreamEvent,
    generate,
    stream_generation,
)
from aistudio_api.application.token_counter import count_tokens as count_prompt_tokens
from aistudio_api.infrastructure.gateway.client import AIStudioClient

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def require_claude() -> None:
    require_format_enabled("claude")


def _gate_error() -> JSONResponse | None:
    try:
        require_claude()
    except Exception as exc:
        return format_error_response(exc, "claude")
    return None


def _validation_error(exc: ValidationError) -> ConversionError:
    errors = exc.errors()
    if not errors:
        return ConversionError("request validation failed")
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg", "invalid value"))
    readable = f"{location}: {message}" if location else message
    return ConversionError(readable, field=location or None)


@router.post("/v1/messages", response_model=None)
async def create_message(request: Request) -> JSONResponse | StreamingResponse:
    gate = _gate_error()
    if gate is not None:
        return gate

    try:
        raw = await request.json()
        payload = ClaudeMessagesRequest.model_validate(raw)
        media = MediaFetcher(allow_private=format_media_allow_private())
        gemini_body = await convert_claude_request(payload, media)
        gemini_request = GeminiGenerateContentRequest.model_validate(gemini_body)
    except ValidationError as exc:
        return format_error_response(_validation_error(exc), "claude")
    except Exception as exc:
        return format_error_response(exc, "claude")

    model_path = strip_models_prefix(payload.model)
    try:
        client = get_client()
    except Exception as exc:
        return format_error_response(exc, "claude")

    if payload.stream:
        return await _streaming_response(
            gemini_request=gemini_request,
            client=client,
            model_path=model_path,
        )

    try:
        outcome = await generate(
            model_path=model_path,
            req=gemini_request,
            client=client,
            api_format="claude",
        )
    except Exception as exc:
        return format_error_response(exc, "claude")
    return JSONResponse(claude_response(model=model_path, outcome=outcome))


async def _streaming_response(
    *,
    gemini_request: GeminiGenerateContentRequest,
    client: AIStudioClient,
    model_path: str,
) -> JSONResponse | StreamingResponse:
    iterator = cast(
        "AsyncGenerator[StreamEvent, None]",
        stream_generation(
            model_path=model_path,
            req=gemini_request,
            client=client,
            api_format="claude",
        ),
    )
    try:
        first = await iterator.__anext__()
    except StopAsyncIteration:
        return format_error_response(
            HTTPException(
                500,
                detail={
                    "message": "upstream returned an empty stream",
                    "type": "server_error",
                },
            ),
            "claude",
        )
    except Exception as exc:
        return format_error_response(exc, "claude")

    renderer = ClaudeStreamRenderer(model_path)

    async def event_stream() -> AsyncGenerator[str, None]:
        yield renderer.start()
        try:
            chunk = renderer.render(first)
            if chunk:
                yield chunk
            async for event in iterator:
                chunk = renderer.render(event)
                if chunk:
                    yield chunk
            yield renderer.close()
        except Exception as exc:
            yield claude_sse_error(exc)
        finally:
            await iterator.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/v1/messages/count_tokens", response_model=None)
async def count_message_tokens(request: Request) -> JSONResponse:
    gate = _gate_error()
    if gate is not None:
        return gate

    try:
        raw = await request.json()
        payload = ClaudeCountTokensRequest.model_validate(raw)
        media = MediaFetcher(allow_private=format_media_allow_private())
        gemini_body = await convert_claude_count_request(payload, media)
        gemini_request = GeminiGenerateContentRequest.model_validate(gemini_body)
        model_path = strip_models_prefix(payload.model)
        client = get_client()
        outcome = await count_prompt_tokens(
            model_path=model_path,
            req=gemini_request,
            client=client,
            api_format="claude",
        )
    except ValidationError as exc:
        return format_error_response(_validation_error(exc), "claude")
    except Exception as exc:
        return format_error_response(exc, "claude")
    return JSONResponse({"input_tokens": outcome.total_tokens})
