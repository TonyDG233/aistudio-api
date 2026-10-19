"""OpenAI Chat Completions compatible routes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from aistudio_api.api.dependencies import get_client, get_runtime_state
from aistudio_api.api.format_errors import (
    format_error_response,
    format_media_allow_private,
    openai_sse_error,
    require_format_enabled,
)
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.api.schemas_openai import OpenAIChatRequest
from aistudio_api.application.format_converters.common import (
    ConversionError,
    strip_models_prefix,
)
from aistudio_api.application.format_converters.media import MediaFetcher
from aistudio_api.application.format_converters.openai_request import (
    convert_openai_request,
)
from aistudio_api.application.format_converters.openai_response import (
    OpenAIStreamRenderer,
    openai_response,
)
from aistudio_api.application.generation_service import (
    StreamEvent,
    generate,
    stream_generation,
)
from aistudio_api.infrastructure.gateway.client import AIStudioClient

if TYPE_CHECKING:
    from aistudio_api.api.state import RuntimeState

router = APIRouter()


def require_openai() -> None:
    require_format_enabled("openai")


def _validation_error(exc: ValidationError) -> ConversionError:
    errors = exc.errors()
    if not errors:
        return ConversionError("request validation failed")
    first: Mapping[str, Any] = errors[0]
    location = ".".join(str(item) for item in first.get("loc", ()))
    message = str(first.get("msg", "invalid value"))
    text = f"{location}: {message}" if location else message
    return ConversionError(text, field=location or None)


def _model_object(model_id: str) -> dict[str, object]:
    return {
        "id": model_id.removeprefix("models/"),
        "object": "model",
        "created": 0,
        "owned_by": "aistudio",
    }


@router.post("/v1/chat/completions", dependencies=[Depends(require_openai)])
async def chat_completions(
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
        payload = OpenAIChatRequest.model_validate(raw)
    except ValidationError as exc:
        return format_error_response(_validation_error(exc), "openai")

    try:
        media = MediaFetcher(allow_private=format_media_allow_private())
        body = await convert_openai_request(payload, media)
        gemini_request = GeminiGenerateContentRequest.model_validate(body)
        model_path = strip_models_prefix(payload.model)

        if not payload.stream:
            outcome = await generate(
                model_path=model_path,
                req=gemini_request,
                client=client,
                api_format="openai",
            )
            return JSONResponse(openai_response(model=model_path, outcome=outcome))

        iterator: AsyncIterator[StreamEvent] = stream_generation(
            model_path=model_path,
            req=gemini_request,
            client=client,
            api_format="openai",
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

        renderer = OpenAIStreamRenderer(model_path)

        async def event_stream() -> AsyncIterator[str]:
            try:
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
                yield openai_sse_error(exc)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    except Exception as exc:
        return format_error_response(exc, "openai")


@router.get("/v1/models", dependencies=[Depends(require_openai)])
async def list_models(
    state: RuntimeState = Depends(get_runtime_state),
) -> JSONResponse:
    session = state.client._session if state.client is not None else None
    from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

    discovered = await model_discovery.get_models(session=session)
    return JSONResponse(
        {
            "object": "list",
            "data": [
                _model_object(str(item.get("id") or item.get("name") or ""))
                for item in discovered
            ],
        }
    )


@router.get("/v1/models/{model_id:path}", dependencies=[Depends(require_openai)])
async def get_model(
    model_id: str,
    state: RuntimeState = Depends(get_runtime_state),
) -> JSONResponse:
    session = state.client._session if state.client is not None else None
    from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

    model = await model_discovery.get_model_by_id(model_id, session=session)
    if model is None:
        return format_error_response(HTTPException(404, "Model not found"), "openai")
    raw_id = str(model.get("id") or model.get("name") or model_id)
    return JSONResponse(_model_object(raw_id))
