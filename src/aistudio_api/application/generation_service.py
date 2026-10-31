"""Protocol-neutral generation pipeline.

The Gemini-compatible handler and every format face (OpenAI Chat, and later
Claude/Responses) share this module.  It owns ATG wrapping/unwrapping, retries,
same-account empty-response retries, account/instance switching through
``RoutingPipeline`` and usage/finish/response-id extraction.  Rendering to a
specific wire protocol happens outside, in the API layer.

No lease/reservation/pinned/defer machinery is used: routing exposes only
``RouteAttempt`` handles, and every handle is released in ``finally``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import cast

from aistudio_api.application.atg import AtgProcessor, create_atg_processor
from aistudio_api.application.atg.extractor import SyntheticRequiredError
from aistudio_api.application.chat_service import (
    NormalizedGeminiRequest,
    normalize_gemini_request,
)
from aistudio_api.application.routing_pipeline import (
    RouteAttempt,
    RouteContext,
    RoutingPipelineProtocol,
)
from aistudio_api.application.signature_cache import record_function_call_signatures
from aistudio_api.application.testing_hooks import forced_429
from aistudio_api.domain.errors import (
    SurfaceSwitchExhaustedError,
    UsageLimitExceeded,
)
from aistudio_api.domain.models import ModelOutput
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("generation")

_ATG_STREAM_EVENT_TYPES = frozenset(
    {"body", "thinking", "tool_calls", "images", "reasoning_images"}
)


@dataclass(frozen=True)
class StreamEvent:
    """One protocol-neutral streaming event."""

    kind: str
    payload: object
    model: str | None = None
    response_id: str | None = None
    thought_signature: str | None = None
    finish_reason: str | None = None


@dataclass(frozen=True)
class GenerationOutcome:
    output: ModelOutput
    model: str
    finish_reason: str
    response_id: str | None
    surface: str


def _output_is_empty(output: ModelOutput) -> bool:
    """ATG emptiness test: no text/thinking/tool calls/images at all."""
    if not output.candidates:
        return True
    candidate = output.candidates[0]
    return not (
        candidate.text
        or candidate.thinking
        or candidate.function_calls
        or candidate.images
        or candidate.reasoning_images
    )


def _create_atg(
    api_format: str,
    atg_factory: Callable[[], AtgProcessor] | None = None,
) -> AtgProcessor:
    if atg_factory is not None:
        return atg_factory()
    from aistudio_api.application.atg.config import resolve_atg_mode

    return create_atg_processor({"mode": resolve_atg_mode(api_format)})


def _wrap_atg_request(
    atg: AtgProcessor,
    req: object,
    model_path: str,
    api_format: str,
) -> tuple[object, str]:
    """Inject the synthetic transport once per request, before normalization."""
    if not atg.enabled:
        return req, ""
    model_validate = getattr(type(req), "model_validate", None)
    model_dump = getattr(req, "model_dump", None)
    if not callable(model_dump) or not callable(model_validate):
        return req, ""
    body = cast("dict[str, object]", model_dump(mode="json", exclude_none=True))
    wrap = atg.wrap(body, model_path)
    if wrap.client_synthetic is not None:
        logger.info(
            "[ATG] client synthetic transport present, skipping local wrap: "
            "tool=%s reason=%s",
            wrap.client_synthetic.name or "(instruction-only)",
            wrap.client_synthetic.reason,
        )
        return req, ""
    if wrap.synthetic_hit:
        raw_contents = body.get("contents")
        raw_tools = body.get("tools")
        logger.info(
            "[ATG] injected synthetic transport: tool=%s model=%s contents=%d "
            "tools=%d format=%s",
            wrap.tool_name,
            model_path,
            len(raw_contents) if isinstance(raw_contents, list) else 0,
            len(raw_tools) if isinstance(raw_tools, list) else 0,
            api_format,
        )
        return model_validate(wrap.body), wrap.tool_name
    logger.info("[ATG] skipped wrap: model=%s (non-text model)", model_path)
    return req, ""


def _finish_reason(output: ModelOutput, normalized: NormalizedGeminiRequest) -> str:
    del normalized
    from aistudio_api.application.finish_reasons import to_gemini_finish_reason

    return to_gemini_finish_reason(
        output.candidates[0].finish_reason if output.candidates else None
    )


async def _acquire(
    pipeline: RoutingPipelineProtocol, context: RouteContext
) -> RouteAttempt:
    attempt = await pipeline.acquire(context)
    if attempt is None:
        raise SurfaceSwitchExhaustedError(surface=context.route.surface)
    return attempt


async def generate(
    *,
    model_path: str,
    req: object,
    pipeline: RoutingPipelineProtocol,
    api_format: str = "gemini",
    atg_factory: Callable[[], AtgProcessor] | None = None,
) -> GenerationOutcome:
    """Run one non-streaming generation through the routing pipeline."""
    context = await pipeline.begin(model_path=model_path)
    effective_model_path = context.route.model
    surface = context.route.surface

    atg = _create_atg(api_format, atg_factory)
    effective_req, tool_name = _wrap_atg_request(
        atg, req, effective_model_path, api_format
    )

    attempt: RouteAttempt | None = None
    normalized: NormalizedGeminiRequest | None = None
    toolshape_emitted = False
    last_error: Exception | None = None

    try:
        while True:
            if attempt is None:
                attempt = await _acquire(pipeline, context)
            try:
                normalized = normalize_gemini_request(
                    effective_req,  # type: ignore[arg-type]
                    effective_model_path,
                    api_format=api_format,
                    surface=surface,
                    emit_toolshape=not toolshape_emitted,
                )
                toolshape_emitted = True
                max_empty = atg.cfg.empty_retries if tool_name else 1
                output: ModelOutput | None = None
                for empty_index in range(max_empty + 1):
                    if forced_429(
                        account_id=attempt.account_id,
                        model=normalized.model,
                        surface=surface,
                    ):
                        raise UsageLimitExceeded(
                            "forced 429 (AISTUDIO_TEST_HOOKS)"
                        )
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
                        generation_config_overrides=(
                            normalized.generation_config_overrides
                        ),
                        sanitize_plain_text=False,
                        force_refresh_capture=empty_index > 0,
                    )
                    if not _output_is_empty(output):
                        break
                    if empty_index < max_empty:
                        logger.warning(
                            "[generation] empty response, retrying same account "
                            "(%d/%d): model=%s",
                            empty_index + 1,
                            max_empty,
                            effective_model_path,
                        )
                        continue
                    if atg.enabled and tool_name and atg.cfg.mode == "required":
                        raise SyntheticRequiredError(
                            "synthetic tool call required but not present in "
                            "upstream response"
                        )
                assert output is not None

                if atg.enabled and tool_name:
                    unwrap = atg.unwrap_response(output, tool_name)
                    output = unwrap.output
                    logger.info(
                        "[ATG] non-stream unwrap: tool=%s stats=%s",
                        tool_name,
                        json.dumps(unwrap.stats.to_dict(), ensure_ascii=False),
                    )

                record_function_call_signatures(output.function_calls)
                finish_reason = _finish_reason(output, normalized)
                pipeline.record_success(
                    context,
                    attempt,
                    usage=output.usage,
                    finish_reason=finish_reason,
                )
                logger.info(
                    "generation complete: format=%s model=%s instance=%s",
                    api_format,
                    normalized.model,
                    attempt.instance_id,
                )
                return GenerationOutcome(
                    output=output,
                    model=normalized.model,
                    finish_reason=finish_reason,
                    response_id=output.response_id or None,
                    surface=surface,
                )
            except Exception as exc:
                last_error = exc
                action = await pipeline.handle_error(
                    context, attempt, exc, has_yielded_data=False
                )
                if action == "retry_same":
                    continue
                if action == "switch":
                    await pipeline.release(attempt)
                    attempt = None
                    continue
                raise
    finally:
        if attempt is not None:
            await pipeline.release(attempt)

    if isinstance(last_error, UsageLimitExceeded):
        raise SurfaceSwitchExhaustedError(surface=surface) from last_error
    if last_error is not None:
        raise last_error
    raise SurfaceSwitchExhaustedError(surface=surface)


def _stream_finish(value: object) -> str:
    if isinstance(value, int):
        from aistudio_api.application.finish_reasons import to_gemini_finish_reason

        return to_gemini_finish_reason(value)
    return str(value)


async def stream_generation(
    *,
    model_path: str,
    req: object,
    pipeline: RoutingPipelineProtocol,
    api_format: str = "gemini",
    atg_factory: Callable[[], AtgProcessor] | None = None,
) -> AsyncGenerator[StreamEvent, None]:
    """Run one streaming generation, yielding protocol-neutral events.

    The terminal ``usage`` event carries ``finish_reason`` and ``response_id``.
    """
    context = await pipeline.begin(model_path=model_path)
    effective_model_path = context.route.model
    surface = context.route.surface

    atg = _create_atg(api_format, atg_factory)
    effective_req, tool_name = _wrap_atg_request(
        atg, req, effective_model_path, api_format
    )

    attempt: RouteAttempt | None = None
    normalized: NormalizedGeminiRequest | None = None
    unwrapper = None
    toolshape_emitted = False
    empty_attempt = 0
    last_error: Exception | None = None
    final_usage: dict[str, object] | None = None
    final_finish_reason = "STOP"
    latest_response_id: str | None = None
    buffered_thought_sig: str | None = None

    try:
        while True:
            if attempt is None:
                attempt = await _acquire(pipeline, context)
                empty_attempt = 0
                yield StreamEvent(
                    kind="routing_wait",
                    payload="resolving-instance",
                    model=effective_model_path,
                )
            final_usage = None
            final_finish_reason = "STOP"
            latest_response_id = None
            buffered_thought_sig = None
            has_yielded_data = False
            try:
                normalized = normalize_gemini_request(
                    effective_req,  # type: ignore[arg-type]
                    effective_model_path,
                    api_format=api_format,
                    surface=surface,
                    emit_toolshape=not toolshape_emitted,
                )
                toolshape_emitted = True
                unwrapper = (
                    atg.create_stream_unwrapper(tool_name)
                    if atg.enabled and tool_name
                    else None
                )
                produced_content = False
                if forced_429(
                    account_id=attempt.account_id,
                    model=normalized.model,
                    surface=surface,
                ):
                    raise UsageLimitExceeded("forced 429 (AISTUDIO_TEST_HOOKS)")
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
                    force_refresh_capture=(
                        attempt.same_retries > 0 or empty_attempt > 0
                    ),
                ):
                    if event_type == "usage":
                        final_usage = text if isinstance(text, dict) else None
                    elif event_type == "finish_reason":
                        final_finish_reason = _stream_finish(text)
                        if (
                            unwrapper is not None
                            and final_finish_reason == "TOOL_CALLS"
                            and unwrapper.should_remap_finish_reason()
                        ):
                            final_finish_reason = "STOP"
                    elif event_type == "response_id":
                        latest_response_id = str(text)
                    elif event_type == "thought_signature":
                        buffered_thought_sig = str(text)
                    else:
                        if event_type == "tool_calls":
                            record_function_call_signatures(text)
                        if text:
                            produced_content = True
                        if (
                            unwrapper is not None
                            and event_type in _ATG_STREAM_EVENT_TYPES
                        ):
                            out_events = unwrapper.process_event(event_type, text)
                        else:
                            out_events = [(event_type, text)]
                        for index, (out_type, out_text) in enumerate(out_events):
                            if out_text:
                                has_yielded_data = True
                            yield StreamEvent(
                                kind=out_type,
                                payload=out_text,
                                model=normalized.model,
                                response_id=latest_response_id,
                                thought_signature=(
                                    buffered_thought_sig if index == 0 else None
                                ),
                            )
                        buffered_thought_sig = None
            except Exception as exc:
                last_error = exc
                action = await pipeline.handle_error(
                    context,
                    attempt,
                    exc,
                    has_yielded_data=has_yielded_data,
                )
                if action == "retry_same":
                    continue
                if action == "switch":
                    await pipeline.release(attempt)
                    attempt = None
                    continue
                raise

            max_empty = atg.cfg.empty_retries if tool_name else 1
            if not produced_content and empty_attempt < max_empty:
                empty_attempt += 1
                logger.warning(
                    "[generation] empty stream, retrying same account (%d/%d): "
                    "model=%s",
                    empty_attempt,
                    max_empty,
                    effective_model_path,
                )
                continue
            if not produced_content and atg.enabled and tool_name and (
                atg.cfg.mode == "required"
            ):
                raise SyntheticRequiredError(
                    "synthetic tool call required but not present in upstream "
                    "response"
                )

            if unwrapper is not None:
                for out_type, out_text in unwrapper.finalize():
                    if out_text:
                        has_yielded_data = True
                    yield StreamEvent(
                        kind=out_type,
                        payload=out_text,
                        model=normalized.model,
                        response_id=latest_response_id,
                        thought_signature=buffered_thought_sig,
                    )
                    buffered_thought_sig = None
                logger.info(
                    "[ATG] stream unwrap: tool=%s stats=%s",
                    tool_name,
                    json.dumps(unwrapper.stats.to_dict(), ensure_ascii=False),
                )
            pipeline.record_success(
                context,
                attempt,
                usage=final_usage,
                finish_reason=final_finish_reason,
            )
            logger.info(
                "stream complete: format=%s model=%s instance=%s",
                api_format,
                normalized.model,
                attempt.instance_id,
            )
            break
    finally:
        if attempt is not None:
            await pipeline.release(attempt)

    if normalized is None:
        if isinstance(last_error, UsageLimitExceeded):
            raise SurfaceSwitchExhaustedError(surface=surface) from last_error
        if last_error is not None:
            raise last_error
        raise SurfaceSwitchExhaustedError(surface=surface)

    yield StreamEvent(
        kind="usage",
        payload=final_usage,
        model=normalized.model,
        response_id=latest_response_id,
        finish_reason=final_finish_reason,
    )


__all__ = [
    "GenerationOutcome",
    "StreamEvent",
    "generate",
    "stream_generation",
]
