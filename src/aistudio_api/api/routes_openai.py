"""OpenAI Chat Completions compatible routes (P5 B5b)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from aistudio_api.api.dependencies import (
    get_routing_pipeline,
    get_runtime_state,
    require_api_key,
    require_api_key_or_web_session,
)
from aistudio_api.api.format_errors import (
    format_error_response,
    format_media_allow_private,
    openai_sse_error,
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
from aistudio_api.api.state import discovery_sessions_for
from aistudio_api.application.contracts.gemini import GeminiGenerateContentRequest
from aistudio_api.application.contracts.openai import OpenAIChatRequest
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
from aistudio_api.application.routing_pipeline import RoutingPipeline
from aistudio_api.domain.errors import RequestError

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


def _enabled_surfaces(state: RuntimeState) -> tuple[str, ...]:
    manager = getattr(state, "instance_manager", None)
    configured = getattr(manager, "configured_surfaces", None) if manager else None
    if configured:
        return tuple(sorted(str(surface) for surface in configured))
    return ("chat",)


@router.post(
    "/v1/chat/completions",
    dependencies=[Depends(require_openai), Depends(require_api_key)],
)
async def chat_completions(
    request: Request,
    pipeline: RoutingPipeline = Depends(get_routing_pipeline),
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
                pipeline=pipeline,
                api_format="openai",
            )
            return JSONResponse(openai_response(model=model_path, outcome=outcome))

        events: KeepaliveIterator[StreamEvent] = KeepaliveIterator(
            stream_generation(
                model_path=model_path,
                req=gemini_request,
                pipeline=pipeline,
                api_format="openai",
            ),
            interval=keepalive_interval_sec(),
        )

        # Phase A: bounded wait for the first renderable event.  Errors and an
        # exhausted stream before the deadline keep the existing PG-02
        # non-200 JSON mapping; a slow upstream switches to 200 + keepalives.
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
                    # ``routing_wait`` is only a resolving progress comment,
                    # never a model content frame for the first window.
                    continue
                first_event = candidate
        except StopAsyncIteration:
            with contextlib.suppress(Exception):
                await events.aclose()
            return format_error_response(
                RequestError(502, "generation produced no stream events"),
                "openai",
            )
        except Exception as exc:
            with contextlib.suppress(Exception):
                await events.aclose()
            return format_error_response(exc, "openai")

        renderer = OpenAIStreamRenderer(model_path)

        async def event_stream() -> AsyncIterator[str]:
            rendered_any = False
            try:
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
                    # A resolving comment is progress, not a model frame: a
                    # routing-wait-only stream must still end in an SSE error.
                    if event.kind != "routing_wait":
                        rendered_any = rendered_any or bool(rendered)
                    if rendered:
                        yield rendered
                if rendered_any:
                    tail = renderer.close()
                    if tail:
                        yield tail
                else:
                    yield openai_sse_error(
                        RequestError(502, "generation produced no stream events")
                    )
            except Exception as exc:
                yield openai_sse_error(exc)
            finally:
                await events.aclose()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    except Exception as exc:
        return format_error_response(exc, "openai")


@router.get(
    "/v1/models",
    dependencies=[Depends(require_openai), Depends(require_api_key_or_web_session)],
)
async def list_models(
    state: RuntimeState = Depends(get_runtime_state),
) -> JSONResponse:
    from aistudio_api.application.model_catalog import project_model_ids

    if getattr(state, "ui_debug", False):
        from aistudio_api.api.ui_sandbox import sandbox_model_catalog

        discovered = sandbox_model_catalog()
    else:
        sessions = discovery_sessions_for(state)
        from aistudio_api.infrastructure.gateway.model_discovery import (
            model_discovery,
        )

        discovered = await model_discovery.get_models(sessions=sessions)
    projected = project_model_ids(discovered, enabled_surfaces=_enabled_surfaces(state))
    return JSONResponse(
        {
            "object": "list",
            "data": [_model_object(model_id) for model_id in projected],
        }
    )


@router.get(
    "/v1/models/{model_id:path}",
    dependencies=[Depends(require_openai), Depends(require_api_key_or_web_session)],
)
async def get_model(
    model_id: str,
    state: RuntimeState = Depends(get_runtime_state),
) -> JSONResponse:
    from aistudio_api.application.model_catalog import resolve_model_request

    clean_id, _surface = resolve_model_request(model_id)
    if getattr(state, "ui_debug", False):
        from aistudio_api.api.ui_sandbox import sandbox_model_by_id

        model = sandbox_model_by_id(clean_id)
    else:
        sessions = discovery_sessions_for(state)
        from aistudio_api.infrastructure.gateway.model_discovery import (
            model_discovery,
        )

        model = await model_discovery.get_model_by_id(clean_id, sessions=sessions)
    if model is None:
        return format_error_response(HTTPException(404, "Model not found"), "openai")
    raw_id = model_id.removeprefix("models/") or str(
        model.get("id") or model.get("name") or model_id
    )
    return JSONResponse(_model_object(raw_id))
