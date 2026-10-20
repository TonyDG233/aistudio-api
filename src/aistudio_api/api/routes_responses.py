"""OpenAI Responses compatible routes (WP11.1)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from aistudio_api.api.dependencies import get_client
from aistudio_api.api.format_errors import (
    format_error_response,
    format_media_allow_private,
    require_format_enabled,
)
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.api.schemas_openai_responses import ResponsesRequest
from aistudio_api.application.format_converters.common import (
    ConversionError,
    strip_models_prefix,
)
from aistudio_api.application.format_converters.media import MediaFetcher
from aistudio_api.application.format_converters.responses_request import (
    convert_responses_request,
)
from aistudio_api.application.format_converters.responses_response import (
    ResponsesStreamRenderer,
    response_failed_event,
    responses_response,
)
from aistudio_api.application.generation_service import (
    StreamEvent,
    generate,
    stream_generation,
)
from aistudio_api.application.token_counter import count_tokens as count_prompt_tokens
from aistudio_api.infrastructure.gateway.client import AIStudioClient

if TYPE_CHECKING:
    pass

router = APIRouter()


def require_responses() -> None:
    require_format_enabled("responses")


def _validation_error(exc: ValidationError) -> ConversionError:
    errors = exc.errors()
    if not errors:
        return ConversionError("request validation failed")
    first: Mapping[str, Any] = errors[0]
    location = ".".join(str(item) for item in first.get("loc", ()))
    message = str(first.get("msg", "invalid value"))
    text = f"{location}: {message}" if location else message
    return ConversionError(text, field=location or None)


@router.post("/v1/responses/input_tokens", dependencies=[Depends(require_responses)])
async def count_response_input_tokens(
    request: Request,
    client: AIStudioClient = Depends(get_client),
) -> Response:
    try:
        raw: object = await request.json()
    except Exception:
        return format_error_response(
            ConversionError("request body is not valid JSON"), "openai"
        )

    try:
        payload = ResponsesRequest.model_validate(raw)
    except ValidationError as exc:
        return format_error_response(_validation_error(exc), "openai")

    try:
        media = MediaFetcher(allow_private=format_media_allow_private())
        body = await convert_responses_request(payload, media)
        gemini_request = GeminiGenerateContentRequest.model_validate(body)
        model_path = strip_models_prefix(payload.model)
        outcome = await count_prompt_tokens(
            model_path=model_path,
            req=gemini_request,
            client=client,
            api_format="responses",
        )
    except Exception as exc:
        return format_error_response(exc, "openai")
    return JSONResponse(
        {
            "object": "response.input_tokens",
            "input_tokens": outcome.total_tokens,
        }
    )


@router.post("/v1/responses", dependencies=[Depends(require_responses)])
async def create_response(
    request: Request,
    client: AIStudioClient = Depends(get_client),
) -> Response:
    try:
        raw: object = await request.json()
    except Exception:
        return format_error_response(
            ConversionError("request body is not valid JSON"), "openai"
        )

    try:
        payload = ResponsesRequest.model_validate(raw)
    except ValidationError as exc:
        return format_error_response(_validation_error(exc), "openai")

    try:
        media = MediaFetcher(allow_private=format_media_allow_private())
        body = await convert_responses_request(payload, media)
        gemini_request = GeminiGenerateContentRequest.model_validate(body)
        model_path = strip_models_prefix(payload.model)

        if not payload.stream:
            outcome = await generate(
                model_path=model_path,
                req=gemini_request,
                client=client,
                api_format="responses",
            )
            return JSONResponse(responses_response(model=model_path, outcome=outcome))

        iterator: AsyncIterator[StreamEvent] = stream_generation(
            model_path=model_path,
            req=gemini_request,
            client=client,
            api_format="responses",
        )
        try:
            first_event = await iterator.__anext__()
        except StopAsyncIteration:
            return format_error_response(
                HTTPException(
                    500,
                    detail={
                        "message": "generation produced no stream events",
                        "type": "server_error",
                    },
                ),
                "openai",
            )
        except Exception as exc:
            return format_error_response(exc, "openai")

        renderer = ResponsesStreamRenderer(model_path)

        async def event_stream() -> AsyncIterator[str]:
            try:
                yield renderer.start()
                chunk = renderer.render(first_event)
                if chunk:
                    yield chunk
                async for event in iterator:
                    rendered = renderer.render(event)
                    if rendered:
                        yield rendered
                tail = renderer.close()
                if tail:
                    yield tail
            except Exception as exc:
                yield response_failed_event(exc)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    except Exception as exc:
        return format_error_response(exc, "openai")
