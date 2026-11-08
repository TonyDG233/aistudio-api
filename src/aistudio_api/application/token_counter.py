"""Real upstream token counting for ``:countTokens`` requests.

Counting reuses the routing pipeline and its ``active_requests`` handle, but
never mutates generation templates, captures snapshots, or applies ATG wrapping.
The default strategy packs system/tools/contents into one synthetic prompt and
asks the upstream ``CountTokens`` RPC; no official API key is generated, echoed
or logged by this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from aistudio_api.application.contracts.gemini import GeminiGenerateContentRequest
from aistudio_api.application.routing_pipeline import (
    RouteAttempt,
    RoutingPipelineProtocol,
)
from aistudio_api.application.testing_hooks import forced_429
from aistudio_api.domain.errors import (
    SurfaceSwitchExhaustedError,
    UsageLimitExceeded,
)
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
    """Build the aggregate prompt used for upstream counting."""
    return _packed_body(req, model)


def _record_count(
    pipeline: RoutingPipelineProtocol,
    model: str,
    result: str,
    *,
    input_tokens: int = 0,
) -> None:
    """Record one isolated countTokens terminal (never a generation attempt)."""
    recorder = getattr(pipeline, "record_count", None)
    if not callable(recorder):
        return
    try:
        recorder(model, result, input_tokens=input_tokens)
    except Exception as exc:
        logger.debug("[token_counter] countTokens 统计记录失败: %s", exc)


async def count_tokens(
    *,
    model_path: str,
    req: GeminiGenerateContentRequest,
    pipeline: RoutingPipelineProtocol,
    api_format: str = "gemini",
) -> TokenCountOutcome:
    """Count tokens through the real upstream total, with account failover.

    The request reuses routing for transport only: it explicitly disables the
    generation attempt ledger and rotator quota events, then records a single
    ``count_tokens`` terminal in its own ledger.
    """
    count_model = model_path
    attempt: RouteAttempt | None = None

    try:
        context = await pipeline.begin(model_path=model_path)
        context.record_attempts = False
        effective_model_path = context.route.model
        count_model = effective_model_path
        surface = context.route.surface
        body = build_count_body(req, effective_model_path)

        try:
            while True:
                if attempt is None:
                    attempt = await pipeline.acquire(context)
                    if attempt is None:
                        raise SurfaceSwitchExhaustedError(surface=surface)
                try:
                    logger.info(
                        "count request: format=%s model=%s contents=%d instance=%s",
                        api_format,
                        effective_model_path,
                        len(req.contents),
                        attempt.instance_id,
                    )
                    if forced_429(
                        account_id=attempt.account_id,
                        model=effective_model_path,
                        surface=surface,
                    ):
                        raise UsageLimitExceeded(
                            "forced 429 (AISTUDIO_TEST_HOOKS)"
                        )
                    total_tokens = await attempt.client.count_tokens(
                        packed_body=body
                    )
                    _record_count(
                        pipeline,
                        effective_model_path,
                        "success",
                        input_tokens=total_tokens,
                    )
                    logger.info(
                        "count complete: format=%s model=%s totalTokens=%s",
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
    except Exception:
        _record_count(pipeline, count_model, "errors")
        raise


__all__ = ["TokenCountOutcome", "build_count_body", "count_tokens"]
