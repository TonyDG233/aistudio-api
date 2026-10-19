"""Protocol-neutral generation pipeline.

Both the Gemini-compatible handler and the OpenAI / Claude format faces use
this module. It owns ATG wrapping/unwrapping, surface resolution, retries,
account switching, empty-response retries and usage accounting. Rendering to a
specific wire protocol happens outside, in the API layer.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, NoReturn, cast

from fastapi import HTTPException

from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_orchestrator import (
    MAX_RETRIES,
    ensure_active_account,
    record_rotator_event,
)
from aistudio_api.application.atg import AtgProcessor, create_atg_processor
from aistudio_api.application.atg.extractor import SyntheticRequiredError
from aistudio_api.application.chat_service import (
    NormalizedGeminiRequest,
    normalize_gemini_request,
)
from aistudio_api.application.routing_pipeline import (
    RouteContext,
    RoutingPipeline,
    get_routing_pipeline,
)
from aistudio_api.application.surface_orchestrator import (
    SurfacePolicyError,
    get_configured_groups,
    resolve_surface,
)
from aistudio_api.application.testing_hooks import forced_429
from aistudio_api.application.upstream_errors import (
    handle_attempt_exception,
    to_gemini_finish_reason,
)
from aistudio_api.domain.errors import UsageLimitExceeded
from aistudio_api.domain.models import ModelOutput
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.instance_pool import InstanceLease
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("generation")

# ATG event types forwarded through the streaming unwrapper. usage /
# finish_reason / response_id / thought_signature keep their dedicated branches.
_ATG_STREAM_EVENT_TYPES = frozenset(
    {"body", "thinking", "tool_calls", "images", "reasoning_images"}
)


@dataclass(frozen=True)
class StreamEvent:
    """One protocol-neutral streaming event.

    ``kind`` uses the existing gateway event vocabulary:
    body / thinking / tool_calls / images / reasoning_images / usage.
    The terminal ``usage`` event carries the finish reason and response id.
    """

    kind: str
    payload: object
    model: str | None = None
    response_id: str | None = None
    thought_signature: str | None = None
    finish_reason: str | None = None


class _LeaseHolder:
    """Tracks the current streaming lease so cancellation still releases it."""

    def __init__(self) -> None:
        self.pipe: RoutingPipeline | None = None
        self.ctx: RouteContext | None = None
        self.lease: InstanceLease | None = None

    async def release(self) -> None:
        lease = self.lease
        self.lease = None
        if lease is not None and self.pipe is not None and self.ctx is not None:
            await self.pipe.release(self.ctx, lease)


@dataclass(frozen=True)
class GenerationOutcome:
    output: ModelOutput
    model: str
    finish_reason: str
    response_id: str | None
    surface: str


def _output_is_empty(output: ModelOutput) -> bool:
    """ATG emptiness test: no text, no thinking and no tool calls at all."""
    if not output.candidates:
        return True
    candidate = output.candidates[0]
    return not (candidate.text or candidate.thinking or candidate.function_calls)


def _create_atg(
    api_format: str,
    atg_factory: Callable[[], AtgProcessor] | None,
) -> AtgProcessor:
    if atg_factory is not None:
        return atg_factory()
    if api_format == "gemini":
        return create_atg_processor()
    from aistudio_api.application.atg.config import resolve_atg_mode

    return create_atg_processor({"mode": resolve_atg_mode(api_format)})


def _resolve_surface(model_path: str) -> str:
    """Resolve the surface for streaming requests (WP5 surface orchestration).

    Thin adapter over
    :func:`aistudio_api.application.surface_orchestrator.resolve_surface`, which
    owns the priority chain (``AISTUDIO_SURFACE_OVERRIDE`` > legacy tier routing
    when ``features.route_by_tier`` is off > group/default-tier policy when on).
    Reads the active account's tier/email and the hot-reloadable feature flags +
    ``features.groups``. Raises ``SurfacePolicyError`` for policy denials; the
    caller maps those to an explicit 4xx body (no silent surface switch).
    """
    try:
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_features_config,
        )

        features = dict(get_features_config())
    except Exception:
        features = {}
    if "groups" not in features:
        features["groups"] = get_configured_groups()

    tier: str | None = None
    email: str | None = None
    try:
        from aistudio_api.infrastructure.account.account_store import AccountStore

        account = AccountStore().get_active_account()
        if account is not None:
            tier = account.tier
            email = getattr(account, "email", None)
    except Exception:
        tier = None
        email = None

    return resolve_surface(model_path, tier, email, features).surface


def _begin_route(
    model_path: str, api_format: str
) -> tuple[RoutingPipeline | None, RouteContext | None]:
    """Start the WP5 phase-2 pipeline; degrade to the legacy path only on bugs."""
    try:
        pipe = get_routing_pipeline()
    except Exception as exc:
        logger.warning("路由管线不可用，回退旧路径: %s", exc, exc_info=True)
        return None, None
    if pipe is None:
        return None, None
    try:
        return pipe, pipe.begin(model_path=model_path, api_format=api_format)
    except SurfacePolicyError:
        raise
    except Exception as exc:
        logger.warning("路由管线初始化失败，回退旧路径: %s", exc, exc_info=True)
        return None, None


def _raise_switch_exhausted(ctx: RouteContext) -> NoReturn:
    snapshot = ctx.budget.snapshot()
    raise SurfacePolicyError(
        "surface_switch_exhausted",
        message=(
            "没有更多可尝试的账号/实例："
            f"switches={snapshot['switches']}/{snapshot['max_switches']}, "
            f"defers={snapshot['defers']}/{snapshot['max_defers']}。"
        ),
        advice="请稍后重试、检查账号健康度，或调整 features.routing 预算。",
    )


async def _acquire_candidate_lease(
    pipe: RoutingPipeline,
    ctx: RouteContext,
    candidate: Any,
) -> tuple[Any, InstanceLease]:
    """Acquire a candidate, advancing past per-candidate capacity denials."""
    last_capacity: SurfacePolicyError | None = None
    skipped = 0
    skip_limit = max(1, int(cast("int", ctx.budget.snapshot()["max_switches"])) + 1)
    while True:
        try:
            return candidate, await pipe.acquire(ctx, candidate)
        except SurfacePolicyError as exc:
            if exc.code != "surface_quota_exceeded":
                raise
            last_capacity = exc
            skipped += 1
            if skipped > skip_limit:
                raise last_capacity from None
            candidate = pipe.next_candidate(ctx, candidate.account_id)
            if candidate is None:
                if last_capacity is not None:
                    raise last_capacity from None
                _raise_switch_exhausted(ctx)


async def generate(
    *,
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient,
    api_format: str = "gemini",
    atg_factory: Callable[[], AtgProcessor] | None = None,
) -> GenerationOutcome:
    """Run a non-streaming generation, returning a protocol-neutral outcome."""
    pipe, ctx = _begin_route(model_path, api_format)
    effective_model_path = ctx.clean_model if ctx is not None else model_path
    surface = ctx.surface if ctx is not None else "chat"
    candidate = None
    lease = None

    # ATG: inject the synthetic transport once, before normalization. The wrapped
    # request is reused for every retry so the synthetic declaration and control
    # turn never stack. The synthetic tool name lives only in this request scope.
    atg = _create_atg(api_format, atg_factory)
    tool_name = ""
    if atg.enabled:
        atg_body = req.model_dump(mode="json", exclude_none=True)
        wrap = atg.wrap(atg_body, effective_model_path)
        if wrap.client_synthetic is not None:
            logger.info(
                "[ATG] 客户端已自带合成通道，跳过本地包装: tool=%s, reason=%s",
                wrap.client_synthetic.name or "(instruction-only)",
                wrap.client_synthetic.reason,
            )
        elif wrap.synthetic_hit:
            logger.info(
                "[ATG] 已注入合成通道: tool=%s, model=%s, contents=%d, tools=%d",
                wrap.tool_name,
                model_path,
                len(atg_body.get("contents") or []),
                len(atg_body.get("tools") or []),
            )
            req = GeminiGenerateContentRequest.model_validate(wrap.body)
            tool_name = wrap.tool_name
        else:
            logger.info("[ATG] 跳过包装: model=%s (非文本模型)", model_path)

    build_rest_body: str | None = None
    if surface == "build":
        build_rest_body = json.dumps(
            req.model_dump(mode="json", exclude_none=True), ensure_ascii=False
        )

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        if pipe is not None and ctx is not None:
            if not (ctx.retry_same and candidate is not None):
                if lease is not None:
                    await pipe.release(ctx, lease)
                    lease = None
                previous = candidate.account_id if candidate is not None else None
                candidate = pipe.next_candidate(ctx, previous)
                if candidate is None:
                    _raise_switch_exhausted(ctx)
                candidate, lease = await _acquire_candidate_lease(pipe, ctx, candidate)
            else:
                ctx.retry_same = False
        else:
            await ensure_active_account(attempt, model=model_path, surface=surface)
        normalized: NormalizedGeminiRequest | None = None
        try:
            normalized = normalize_gemini_request(req, effective_model_path)
            logger.info(
                "生成请求: format=%s, model=%s, 对话轮数=%s, 流式=否, 尝试第 %d 次",
                api_format,
                normalized.model,
                len(req.contents),
                attempt + 1,
            )

            # ATG empty-response retry: a fully empty (collapsed) upstream result
            # is re-sent up to cfg.empty_retries times. This layer never switches
            # accounts; it only re-sends within the current attempt.
            max_empty = atg.cfg.empty_retries if (atg.enabled and tool_name) else 0
            output: ModelOutput | None = None
            for empty_attempt in range(max_empty + 1):
                if forced_429(
                    account_id=lease.account_id if lease is not None else None,
                    model=normalized.model,
                    surface=surface,
                ):
                    raise UsageLimitExceeded("forced 429 by AISTUDIO_TEST_FORCE_429")
                output = await client.generate_content(
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
                    surface=surface,
                    rest_body=build_rest_body,
                    lease=lease,
                )
                if not (atg.enabled and tool_name):
                    break
                if not _output_is_empty(output):
                    break
                if empty_attempt < max_empty:
                    logger.warning(
                        "[ATG] 空响应，重发 (%d/%d): model=%s",
                        empty_attempt + 1,
                        max_empty,
                        model_path,
                    )
                    continue
                logger.warning(
                    "[ATG] empty response retries exhausted: model=%s", model_path
                )
                if atg.cfg.mode == "required":
                    raise SyntheticRequiredError(
                        "synthetic tool call required but not present in upstream response"
                    )
            assert output is not None

            if atg.enabled and tool_name:
                unwrap = atg.unwrap_response(output, tool_name)
                output = unwrap.output
                logger.info(
                    "[ATG] 非流式解包: tool=%s, stats=%s",
                    tool_name,
                    json.dumps(unwrap.stats.to_dict(), ensure_ascii=False),
                )

            success_account_id = lease.account_id if lease is not None else None
            if pipe is not None and ctx is not None:
                pipe.record_success(ctx)
                await pipe.release(ctx, lease)
                lease = None
            record_rotator_event(
                "success",
                model=normalized.model,
                surface=surface,
                account_id=success_account_id,
            )
            runtime_state.record(
                normalized.model, "success", output.usage, api_format=api_format
            )
            logger.info(
                "生成完成: format=%s, model=%s, 输入Tokens=%s, 输出Tokens=%s",
                api_format,
                normalized.model,
                output.usage.get("prompt_tokens") if output.usage else 0,
                output.usage.get("completion_tokens") if output.usage else 0,
            )
            return GenerationOutcome(
                output=output,
                model=normalized.model,
                finish_reason=to_gemini_finish_reason(
                    output.candidates[0].finish_reason if output.candidates else None
                ),
                response_id=output.response_id or None,
                surface=surface,
            )
        except Exception as exc:
            last_error = exc
            try:
                retry = await handle_attempt_exception(
                    exc,
                    attempt=attempt,
                    model_path=model_path,
                    normalized_model=normalized.model if normalized else None,
                    client=client,
                    api_format=api_format,
                    surface=surface,
                    routing=pipe,
                    route_ctx=ctx,
                    lease=lease,
                )
            except BaseException:
                if pipe is not None and ctx is not None:
                    await pipe.release(ctx, lease)
                    lease = None
                raise
            if retry:
                if pipe is not None and ctx is not None and not ctx.retry_same:
                    await pipe.release(ctx, lease)
                    lease = None
                continue
            if pipe is not None and ctx is not None:
                await pipe.release(ctx, lease)
                lease = None
            raise
        except BaseException:
            if pipe is not None and ctx is not None:
                await pipe.release(ctx, lease)
                lease = None
            raise

    if pipe is not None and ctx is not None:
        await pipe.release(ctx, lease)
    raise HTTPException(
        429,
        detail={
            "message": f"All accounts reached daily quota for model '{model_path}'. Quota resets at 00:00 PST.",
            "type": "rate_limit_exceeded",
        },
    ) from last_error


async def _stream_generation_impl(
    *,
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient,
    api_format: str = "gemini",
    atg_factory: Callable[[], AtgProcessor] | None = None,
    lease_holder: _LeaseHolder,
) -> AsyncIterator[StreamEvent]:
    """Run a streaming generation, yielding protocol-neutral events."""
    pipe, ctx = _begin_route(model_path, api_format)
    lease_holder.pipe = pipe
    lease_holder.ctx = ctx
    effective_model_path = ctx.clean_model if ctx is not None else model_path
    surface = ctx.surface if ctx is not None else _resolve_surface(effective_model_path)
    candidate = None
    lease = None

    # ATG: inject the synthetic transport once, before normalization. The
    # synthetic tool name lives only in this request scope.
    atg = _create_atg(api_format, atg_factory)
    tool_name = ""
    unwrapper = None
    effective_req = req
    if atg.enabled:
        atg_body = req.model_dump(mode="json", exclude_none=True)
        wrap = atg.wrap(atg_body, effective_model_path)
        if wrap.client_synthetic is not None:
            logger.info(
                "[ATG] 客户端已自带合成通道，跳过本地包装: tool=%s, reason=%s",
                wrap.client_synthetic.name or "(instruction-only)",
                wrap.client_synthetic.reason,
            )
        elif wrap.synthetic_hit:
            logger.info(
                "[ATG] 已注入合成通道: tool=%s, model=%s, contents=%d, tools=%d",
                wrap.tool_name,
                model_path,
                len(atg_body.get("contents") or []),
                len(atg_body.get("tools") or []),
            )
            effective_req = GeminiGenerateContentRequest.model_validate(wrap.body)
            tool_name = wrap.tool_name
            unwrapper = atg.create_stream_unwrapper(tool_name)
        else:
            logger.info("[ATG] 跳过包装: model=%s (非文本模型)", model_path)

    build_rest_body: str | None = None
    if surface == "build":
        build_rest_body = json.dumps(
            effective_req.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
        )
        logger.info(
            "[build] 流式请求经 Build 面直连: model=%s, contents=%d",
            model_path,
            len(effective_req.contents),
        )

    normalized: NormalizedGeminiRequest | None = None
    logger.info(
        "流式请求: format=%s, model=%s, 对话轮数=%s",
        api_format,
        model_path,
        len(effective_req.contents),
    )
    final_usage: dict[str, object] | None = None
    final_finish_reason: str = "STOP"
    latest_response_id: str | None = None
    buffered_thought_sig: str | None = None
    max_empty = atg.cfg.empty_retries if (atg.enabled and tool_name) else 0
    for stream_attempt in range(MAX_RETRIES):
        has_yielded_data = False
        if pipe is not None and ctx is not None:
            if not (ctx.retry_same and candidate is not None):
                if lease is not None:
                    await pipe.release(ctx, lease)
                    lease = None
                    lease_holder.lease = None
                previous = candidate.account_id if candidate is not None else None
                candidate = pipe.next_candidate(ctx, previous)
                if candidate is None:
                    _raise_switch_exhausted(ctx)
                yield StreamEvent(
                    kind="routing_wait",
                    payload="resolving-instance",
                    model=effective_model_path,
                )
                candidate, lease = await _acquire_candidate_lease(pipe, ctx, candidate)
                lease_holder.lease = lease
            else:
                ctx.retry_same = False
        else:
            await ensure_active_account(
                stream_attempt, model=model_path, surface=surface
            )
        normalized = normalize_gemini_request(effective_req, effective_model_path)
        # Fresh per-attempt unwrapper: a retry must not carry stale
        # buffered/consumed state from the previous attempt.
        if atg.enabled and tool_name:
            unwrapper = atg.create_stream_unwrapper(tool_name)
        empty_attempt = 0
        retry_outer = False
        while True:
            produced_content = False
            try:
                if forced_429(
                    account_id=lease.account_id if lease is not None else None,
                    model=normalized.model,
                    surface=surface,
                ):
                    raise UsageLimitExceeded("forced 429 by AISTUDIO_TEST_FORCE_429")
                if surface == "build":
                    stream_iter = client.stream_generate_content(
                        model=normalized.model,
                        capture_prompt="",
                        surface="build",
                        rest_body=build_rest_body,
                        lease=lease,
                    )
                else:
                    stream_iter = client.stream_generate_content(
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
                        force_refresh_capture=(stream_attempt > 0 or empty_attempt > 0),
                        lease=lease,
                    )
                async for event_type, text in stream_iter:
                    if event_type == "usage":
                        final_usage = text if isinstance(text, dict) else None
                    elif event_type == "finish_reason":
                        final_finish_reason = (
                            to_gemini_finish_reason(text)
                            if isinstance(text, int)
                            else str(text)
                        )
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
                    elif (
                        unwrapper is not None and event_type in _ATG_STREAM_EVENT_TYPES
                    ):
                        if text:
                            produced_content = True
                        out_events = unwrapper.process_event(event_type, text)
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
                    else:
                        if text:
                            has_yielded_data = True
                        yield StreamEvent(
                            kind=event_type,
                            payload=text,
                            model=normalized.model,
                            response_id=latest_response_id,
                            thought_signature=buffered_thought_sig,
                        )
                        buffered_thought_sig = None
            except Exception as exc:
                try:
                    retry = await handle_attempt_exception(
                        exc,
                        attempt=stream_attempt,
                        model_path=model_path,
                        normalized_model=normalized.model if normalized else None,
                        client=client,
                        has_yielded_data=has_yielded_data,
                        surface=surface,
                        api_format=api_format,
                        routing=pipe,
                        route_ctx=ctx,
                        lease=lease,
                    )
                except Exception:
                    if pipe is not None and ctx is not None:
                        await pipe.release(ctx, lease)
                        lease = None
                        lease_holder.lease = None
                    raise
                if retry:
                    if pipe is not None and ctx is not None and not ctx.retry_same:
                        await pipe.release(ctx, lease)
                        lease = None
                        lease_holder.lease = None
                    retry_outer = True
                    break
                if pipe is not None and ctx is not None:
                    await pipe.release(ctx, lease)
                    lease = None
                    lease_holder.lease = None
                raise

            # ATG empty-response retry: an attempt that produced no content
            # event is re-sent up to cfg.empty_retries times (no account switch).
            if max_empty > 0 and not produced_content:
                if empty_attempt < max_empty:
                    empty_attempt += 1
                    logger.warning(
                        "[ATG] 流式空响应，重发 (%d/%d): model=%s",
                        empty_attempt,
                        max_empty,
                        model_path,
                    )
                    continue
                logger.warning(
                    "[ATG] empty response retries exhausted: model=%s",
                    model_path,
                )
                if atg.cfg.mode == "required":
                    raise SyntheticRequiredError(
                        "synthetic tool call required but not present in "
                        "upstream response"
                    )
            break
        if retry_outer:
            continue

        # Flush buffered output and enforce required-mode validation.
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
                "[ATG] 流式解包: tool=%s, stats=%s",
                tool_name,
                json.dumps(unwrapper.stats.to_dict(), ensure_ascii=False),
            )
        break

    target_model = normalized.model if normalized else model_path
    success_account_id = lease.account_id if lease is not None else None
    if pipe is not None and ctx is not None:
        pipe.record_success(ctx)
        await pipe.release(ctx, lease)
        lease = None
        lease_holder.lease = None
    record_rotator_event(
        "success",
        model=target_model,
        surface=surface,
        account_id=success_account_id,
    )
    if normalized is not None:
        runtime_state.record(
            normalized.model, "success", final_usage, api_format=api_format
        )
    logger.info(
        "流式完成: format=%s, model=%s, 输入Tokens=%s, 输出Tokens=%s",
        api_format,
        target_model,
        final_usage.get("prompt_tokens") if final_usage else 0,
        final_usage.get("completion_tokens") if final_usage else 0,
    )
    yield StreamEvent(
        kind="usage",
        payload=final_usage,
        model=target_model,
        response_id=latest_response_id,
        finish_reason=final_finish_reason,
    )


async def stream_generation(
    *,
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient,
    api_format: str = "gemini",
    atg_factory: Callable[[], AtgProcessor] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Run streaming generation; release the lease even if the client cancels."""
    holder = _LeaseHolder()
    try:
        async for event in _stream_generation_impl(
            model_path=model_path,
            req=req,
            client=client,
            api_format=api_format,
            atg_factory=atg_factory,
            lease_holder=holder,
        ):
            yield event
    finally:
        await holder.release()
