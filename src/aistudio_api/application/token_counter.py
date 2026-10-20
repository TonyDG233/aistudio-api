"""Real upstream token counting (WP11).

This module is the single entry point for ``:countTokens`` style requests. It
reuses the generation routing pipeline and instance-pool leases, but never
mutates generation templates, captures snapshots, or applies ATG wrapping.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import cast

from fastapi import HTTPException

from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_orchestrator import (
    MAX_RETRIES,
    ensure_active_account,
    record_rotator_event,
)
from aistudio_api.application.generation_service import (
    _acquire_candidate_lease,
    _begin_route,
    _raise_switch_exhausted,
)
from aistudio_api.application.testing_hooks import forced_429
from aistudio_api.application.upstream_errors import handle_attempt_exception
from aistudio_api.domain.errors import UsageLimitExceeded
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.wire_types import (
    AistudioContent,
    AistudioPart,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("token_counter")


@dataclass(frozen=True)
class TokenCountOutcome:
    model: str
    total_tokens: int
    surface: str


def _clean_json(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _clean_json(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_clean_json(item) for item in value if item is not None]
    return value


def _parts_text(parts: object) -> list[str]:
    if not isinstance(parts, list):
        return []
    lines: list[str] = []
    for part in parts:
        if hasattr(part, "model_dump"):
            part = part.model_dump(mode="json", by_alias=True, exclude_none=True)
        if not isinstance(part, dict):
            continue
        if isinstance(part.get("text"), str):
            lines.append(cast("str", part["text"]))
        elif "functionCall" in part or "functionResponse" in part:
            lines.append(
                json.dumps(
                    _clean_json(part),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
    return lines


def _packed_body(req: GeminiGenerateContentRequest, model: str) -> list[object]:
    sections: list[str] = []
    if req.systemInstruction is not None:
        system_text = "\n".join(_parts_text(req.systemInstruction.parts))
        if system_text:
            sections.append(f"[system]\n{system_text}")

    if req.tools:
        tools_json = json.dumps(
            [
                _clean_json(
                    tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                )
                for tool in req.tools
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        sections.append(f"[tools]\n{tools_json}")

    inline_parts: list[AistudioPart] = []
    conversation: list[str] = []
    for content in req.contents:
        role = content.role or "user"
        for part in content.parts:
            if part.text is not None:
                prefix = f"[{role}/thought] " if part.thought else f"[{role}] "
                conversation.append(f"{prefix}{part.text}")
            elif part.inlineData is not None:
                inline_parts.append(
                    AistudioPart(
                        inline_data=(
                            part.inlineData.mimeType,
                            part.inlineData.data,
                        )
                    )
                )
            else:
                conversation.append(
                    json.dumps(
                        _clean_json(
                            part.model_dump(
                                mode="json", by_alias=True, exclude_none=True
                            )
                        ),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
    if conversation:
        sections.append("[conversation]\n" + "\n".join(conversation))

    parts = [AistudioPart(text="\n\n".join(sections)), *inline_parts]
    content = AistudioContent(role="user", parts=parts)
    return [f"models/{model}", [content.to_wire()]]


def build_count_body(req: GeminiGenerateContentRequest, model: str) -> list[object]:
    """Build the aggregate prompt used for upstream counting.

    System, tools and conversation are serialized into one synthetic contents
    payload; JSON is compacted and null fields removed to keep the count close
    to the native structured request.
    """
    return _packed_body(req, model)


async def _count_tokens_impl(
    *,
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient,
    api_format: str = "gemini",
) -> TokenCountOutcome:
    """Count tokens using the real upstream total, with account failover."""
    pipe, ctx = _begin_route(model_path, api_format)
    effective_model_path = ctx.clean_model if ctx is not None else model_path
    surface = ctx.surface if ctx is not None else "chat"
    candidate = None
    lease = None
    last_error: Exception | None = None
    body = build_count_body(req, effective_model_path)

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

        try:
            logger.info(
                "计数请求: format=%s, model=%s, contents=%d, 尝试第 %d 次",
                api_format,
                effective_model_path,
                len(req.contents),
                attempt + 1,
            )
            account_id = lease.account_id if lease is not None else None
            if forced_429(
                account_id=account_id,
                model=effective_model_path,
                surface=surface,
            ):
                raise UsageLimitExceeded("forced 429 by AISTUDIO_TEST_FORCE_429")
            total_tokens = await client.count_tokens(
                packed_body=body,
                lease=lease,
            )

            account_id = lease.account_id if lease is not None else None
            if pipe is not None and ctx is not None:
                pipe.record_success(ctx)
                await pipe.release(ctx, lease)
                lease = None
            record_rotator_event(
                "success",
                model=effective_model_path,
                surface=surface,
                account_id=account_id,
            )
            logger.info(
                "计数完成: format=%s, model=%s, totalTokens=%s",
                api_format,
                effective_model_path,
                total_tokens,
            )
            return TokenCountOutcome(
                model=effective_model_path,
                total_tokens=total_tokens,
                surface=surface,
            )
        except Exception as exc:
            last_error = exc
            try:
                retry = await handle_attempt_exception(
                    exc,
                    attempt=attempt,
                    model_path=model_path,
                    normalized_model=effective_model_path,
                    client=client,
                    api_format=api_format,
                    surface=surface,
                    routing=pipe,
                    route_ctx=ctx,
                    lease=lease,
                    record_stats=False,
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
            "message": (
                f"All accounts reached daily quota for model '{model_path}'. "
                "Quota resets at 00:00 PST."
            ),
            "type": "rate_limit_exceeded",
        },
    ) from last_error


async def count_tokens(
    *,
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient,
    api_format: str = "gemini",
) -> TokenCountOutcome:
    """Count tokens and record one client-level count outcome."""
    try:
        outcome = await _count_tokens_impl(
            model_path=model_path,
            req=req,
            client=client,
            api_format=api_format,
        )
    except asyncio.CancelledError:
        runtime_state.record_token_count(
            model_path, api_format=api_format, outcome="error"
        )
        raise
    except Exception:
        runtime_state.record_token_count(
            model_path, api_format=api_format, outcome="error"
        )
        raise
    runtime_state.record_token_count(
        outcome.model,
        api_format=api_format,
        surface=outcome.surface,
        outcome="success",
        total_tokens=outcome.total_tokens,
    )
    return outcome
