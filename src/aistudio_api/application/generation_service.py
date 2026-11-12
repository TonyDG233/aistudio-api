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

import asyncio
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
from aistudio_api.domain.models import Candidate, ModelOutput
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


def _is_thinking_only(candidate: Candidate) -> bool:
    """True when the candidate carries reasoning but no answer payload.

    Such a response is ``thinking``-only truncation: it must not be treated as
    an empty response (no retry / account switch, which would burn quota), only
    surfaced as a warning.
    """
    return bool(candidate.thinking) and not (
        candidate.text
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


def _terminal_result(exc: BaseException) -> str:
    """Map the final client-visible error to its client terminal bucket."""
    if isinstance(exc, (SurfaceSwitchExhaustedError, UsageLimitExceeded)):
        return "rate_limited"
    if isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
        return "aborted"
    return "errors"


def _record_client_terminal(
    pipeline: RoutingPipelineProtocol,
    model: str,
    result: str,
    *,
    usage: dict[str, object] | None = None,
    finish_reason: str | None = None,
    rescued: bool = False,
) -> None:
    """Record exactly one terminal outcome for the client request."""
    recorder = getattr(pipeline, "record_client", None)
    if not callable(recorder):
        return
    try:
        recorder(
            model,
            result,
            usage=usage,
            finish_reason=finish_reason,
            rescued=rescued,
        )
    except Exception as exc:
        logger.debug("[generation] client 缁堟€佽褰曞け璐? %s", exc)


def _record_empty_retry(
    pipeline: RoutingPipelineProtocol,
    context: RouteContext,
    attempt: RouteAttempt,
) -> None:
    recorder = getattr(pipeline, "record_empty_retry", None)
    if not callable(recorder):
        return
    try:
        recorder(context, attempt)
    except Exception as exc:
        logger.debug("[generation] 绌哄搷搴?attempt 璁板綍澶辫触: %s", exc)


class _ClientTerminal:
    """Idempotent client-request terminal recorder (exactly one per request)."""

    def __init__(self, pipeline: RoutingPipelineProtocol, model: str) -> None:
        self._pipeline = pipeline
        self.model = model
        self._done = False

    def record(
        self,
        result: str,
        *,
        usage: dict[str, object] | None = None,
        finish_reason: str | None = None,
        rescued: bool = False,
    ) -> None:
        if self._done:
            return
        self._done = True
        _record_client_terminal(
            self._pipeline,
            self.model,
            result,
            usage=usage,
            finish_reason=finish_reason,
            rescued=rescued,
        )


async def generate(
    *,
    model_path: str,
    req: object,
    pipeline: RoutingPipelineProtocol,
    api_format: str = "gemini",
    atg_factory: Callable[[], AtgProcessor] | None = None,
) -> GenerationOutcome:
    """Run one non-streaming generation through the routing pipeline."""
    attempt: RouteAttempt | None = None
    normalized: NormalizedGeminiRequest | None = None
    toolshape_emitted = False
    rescued = False
    client_terminal = _ClientTerminal(pipeline, model_path)

    try:
        context = await pipeline.begin(model_path=model_path)
        effective_model_path = context.route.model
        client_terminal.model = effective_model_path
        surface = context.route.surface

        atg = _create_atg(api_format, atg_factory)
        effective_req, tool_name = _wrap_atg_request(
            atg, req, effective_model_path, api_format
        )

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
                        raise UsageLimitExceeded("forced 429 (AISTUDIO_TEST_HOOKS)")
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
                        rescued = True
                        _record_empty_retry(pipeline, context, attempt)
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
                candidate = output.candidates[0] if output.candidates else None
                if candidate is not None and _is_thinking_only(candidate):
                    logger.warning(
                        "[generation] 思维链截断告警: 模型仅返回 thinking 未产出正文或"
                        "工具调用 (已跳过重试以保护配额): format=%s model=%s "
                        "instance=%s finish_reason=%s thinking_len=%d",
                        api_format,
                        normalized.model,
                        attempt.instance_id,
                        finish_reason,
                        len(candidate.thinking),
                    )
                pipeline.record_success(
                    context,
                    attempt,
                    usage=output.usage,
                    finish_reason=finish_reason,
                )
                client_terminal.record(
                    "success",
                    usage=output.usage,
                    finish_reason=finish_reason,
                    rescued=rescued,
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
                action = await pipeline.handle_error(
                    context, attempt, exc, has_yielded_data=False
                )
                if action == "retry_same":
                    rescued = True
                    continue
                if action == "switch":
                    rescued = True
                    await pipeline.release(attempt)
                    attempt = None
                    continue
                raise
    except BaseException as exc:
        client_terminal.record(_terminal_result(exc))
        raise
    finally:
        if attempt is not None:
            await pipeline.release(attempt)


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
    Exactly one client terminal is recorded: success once the upstream stream
    is fully consumed, ``aborted`` on cancellation/disconnect, or
    ``rate_limited``/``errors`` for the final failure.
    """
    attempt: RouteAttempt | None = None
    normalized: NormalizedGeminiRequest | None = None
    unwrapper = None
    toolshape_emitted = False
    empty_attempt = 0
    final_usage: dict[str, object] | None = None
    final_finish_reason = "STOP"
    latest_response_id: str | None = None
    buffered_thought_sig: str | None = None
    rescued = False
    client_terminal = _ClientTerminal(pipeline, model_path)

    try:
        context = await pipeline.begin(model_path=model_path)
        effective_model_path = context.route.model
        client_terminal.model = effective_model_path
        surface = context.route.surface

        atg = _create_atg(api_format, atg_factory)
        effective_req, tool_name = _wrap_atg_request(
            atg, req, effective_model_path, api_format
        )

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
                    produced_answer = False
                    thinking_chars = 0
                    if forced_429(
                        account_id=attempt.account_id,
                        model=normalized.model,
                        surface=surface,
                    ):
                        raise UsageLimitExceeded("forced 429 (AISTUDIO_TEST_HOOKS)")
                    async for (
                        event_type,
                        text,
                    ) in attempt.client.stream_generate_content(
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
                                if event_type == "thinking":
                                    thinking_chars += (
                                        len(text) if isinstance(text, str) else 0
                                    )
                                elif event_type in (
                                    "body",
                                    "tool_calls",
                                    "images",
                                    "reasoning_images",
                                ):
                                    produced_answer = True
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
                    action = await pipeline.handle_error(
                        context,
                        attempt,
                        exc,
                        has_yielded_data=has_yielded_data,
                    )
                    if action == "retry_same":
                        rescued = True
                        continue
                    if action == "switch":
                        rescued = True
                        await pipeline.release(attempt)
                        attempt = None
                        continue
                    raise

                max_empty = atg.cfg.empty_retries if tool_name else 1
                if not produced_content and empty_attempt < max_empty:
                    empty_attempt += 1
                    rescued = True
                    _record_empty_retry(pipeline, context, attempt)
                    logger.warning(
                        "[generation] empty stream, retrying same account (%d/%d): "
                        "model=%s",
                        empty_attempt,
                        max_empty,
                        effective_model_path,
                    )
                    continue
                if (
                    not produced_content
                    and atg.enabled
                    and tool_name
                    and (atg.cfg.mode == "required")
                ):
                    raise SyntheticRequiredError(
                        "synthetic tool call required but not present in upstream "
                        "response"
                    )

                clean_model = normalized.model
                if thinking_chars > 0 and not produced_answer:
                    logger.warning(
                        "[generation] 思维链截断告警: 流式响应仅返回 thinking 未产出"
                        "正文或工具调用 (已跳过重试以保护配额): format=%s model=%s "
                        "instance=%s finish_reason=%s thinking_len=%d",
                        api_format,
                        clean_model,
                        attempt.candidate.instance_id,
                        final_finish_reason,
                        thinking_chars,
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
                client_terminal.record(
                    "success",
                    usage=final_usage,
                    finish_reason=final_finish_reason,
                    rescued=rescued,
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
    except BaseException as exc:
        client_terminal.record(_terminal_result(exc))
        raise

    if normalized is None:
        raise SurfaceSwitchExhaustedError(surface=client_terminal.model)

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
