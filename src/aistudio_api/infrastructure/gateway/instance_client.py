"""Request client bound to one pooled instance session.

This is the request-time client used by :mod:`routing_pipeline`.  It never owns
the browser process or the control session; it only replays a request on the
Page belonging to a single ``InstanceSlot``.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncGenerator
from pathlib import Path

from aistudio_api.config import DEFAULT_TEXT_MODEL, resolve_data_dir, settings
from aistudio_api.domain.errors import (
    BrowserTransportError,
    RequestError,
    classify_error,
)
from aistudio_api.domain.models import ModelOutput
from aistudio_api.infrastructure.gateway.capture import RequestCaptureService
from aistudio_api.infrastructure.gateway.public_web_constants import COUNT_TOKENS_URL
from aistudio_api.infrastructure.gateway.replay import RequestReplayService
from aistudio_api.infrastructure.gateway.session_protocol import RequestSession
from aistudio_api.infrastructure.gateway.streaming import StreamingGateway
from aistudio_api.infrastructure.gateway.wire_parser import parse_text_output
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import (
    get_logger,
    safe_json_payload,
    sanitize_log_message,
)

logger = get_logger("instance-client")

_SAFE_MODEL_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _sanitize_model_component(model: str) -> str:
    """Return a filename-safe model token with no separators or traversal."""
    sanitized = "".join(
        char if char in _SAFE_MODEL_CHARS else "_" for char in model
    )
    while ".." in sanitized:
        sanitized = sanitized.replace("..", "._")
    sanitized = sanitized.strip(".")
    return sanitized or "model"


class InstanceRequestClient:
    """Minimal generation facade over one pooled BrowserContext session."""

    def __init__(
        self,
        session: RequestSession,
        *,
        account_id: str,
        tier: str,
        surface: str,
    ) -> None:
        self._session = session
        self.account_id = account_id
        self.tier = tier
        self.surface = surface
        shared_capture = getattr(session, "capture_service", None)
        self._capture = (
            shared_capture
            if isinstance(shared_capture, RequestCaptureService)
            else RequestCaptureService(session)
        )
        self._replay = RequestReplayService(session)
        self._streaming = StreamingGateway(session)

    def clear_templates(self) -> None:
        self._capture.clear_templates()

    def _dump_raw_exchange(
        self,
        *,
        kind: str,
        model: str,
        capture_prompt: str,
        modified_body: str,
        raw_response: str,
    ) -> None:
        if not settings.dump_raw_response:
            return
        try:
            raw_dir = os.getenv("AISTUDIO_DUMP_RAW_RESPONSE_DIR") or (
                settings.dump_raw_response_dir
            )
            out_dir = Path(raw_dir).expanduser()
            if not out_dir.is_absolute():
                out_dir = resolve_data_dir() / out_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            safe_model = _sanitize_model_component(model)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            payload = {
                "kind": kind,
                "instance": self.account_id,
                "surface": self.surface,
                "model": model,
                "safe_prompt": sanitize_log_message(capture_prompt),
                "safe_request_body": safe_json_payload(modified_body),
                "safe_response": sanitize_log_message(raw_response),
            }
            (out_dir / f"aistudio_{kind}_{safe_model}_{timestamp}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("实例原始请求落盘失败: %s", exc)

    async def generate_content(
        self,
        *,
        model: str = DEFAULT_TEXT_MODEL,
        capture_prompt: str,
        capture_images: list[str | tuple[str, str]] | None = None,
        contents: list[AistudioContent] | None = None,
        system_instruction_content: AistudioContent | None = None,
        tools: list[list] | None = None,
        tool_config: list[object] | None = None,
        safety_settings: list[list] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        generation_config_overrides: dict | None = None,
        sanitize_plain_text: bool = True,
        force_refresh_capture: bool = False,
    ) -> ModelOutput:
        async with self._session.request_scope():
            captured = await self._capture.capture(
                prompt=capture_prompt,
                model=model,
                images=capture_images,
                contents=contents,
                system_instruction_content=system_instruction_content,
                tools=tools,
                tool_config=tool_config,
                safety_settings=safety_settings,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                max_tokens=max_tokens,
                generation_config_overrides=generation_config_overrides,
                sanitize_plain_text=sanitize_plain_text,
                force_refresh=force_refresh_capture,
            )
            if not captured:
                raise RequestError(0, "无法拦截请求")
            status, raw = await self._replay.replay(captured, body=captured.body)
            raw_text = raw.decode("utf-8", errors="replace")
            self._dump_raw_exchange(
                kind="generate_content",
                model=model,
                capture_prompt=capture_prompt,
                modified_body=captured.body,
                raw_response=raw_text,
            )
            if status != 200:
                raise classify_error(status, raw_text)
            if not raw_text.strip():
                raise BrowserTransportError("empty 200 body (connection truncated)")
            output = parse_text_output(raw_text)
            output.model = model
            return output

    async def count_tokens(self, *, packed_body: list[object]) -> int:
        """Count prompt tokens through the upstream ``CountTokens`` RPC.

        The RPC only accepts ``[model, contents]``; system/tools are serialized
        into the packed contents by the caller.  It reuses this instance's live
        page and never touches generation templates or snapshots.
        """
        async with self._session.request_scope():
            ctx = getattr(self._session, "active_context", None)
            if ctx is None:
                raise RequestError(503, "no active upstream context for token count")
            base_url = str(ctx.base_template.get("url") or "")
            if "/GenerateContent" in base_url:
                url = base_url.replace("/GenerateContent", "/CountTokens")
            else:
                url = COUNT_TOKENS_URL
            raw_headers = ctx.base_template.get("headers")
            headers = (
                {str(k): str(v) for k, v in raw_headers.items()}
                if isinstance(raw_headers, dict)
                else {}
            )
            headers["Content-Type"] = "application/json+protobuf"
            payload = json.dumps(
                packed_body, ensure_ascii=False, separators=(",", ":")
            )
            status, raw = await self._session.send_hooked_request(
                body=payload, url=url, headers=headers
            )
            text = raw.decode("utf-8", errors="replace")
            if status >= 400:
                raise classify_error(status, text)
            try:
                data = json.loads(text)
            except ValueError as exc:
                raise RequestError(
                    502, "upstream CountTokens returned invalid JSON"
                ) from exc
            total = data[0] if isinstance(data, list) and data else None
            if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                raise RequestError(
                    502, "upstream CountTokens response missing token total"
                )
            return total

    async def stream_generate_content(
        self,
        *,
        model: str = DEFAULT_TEXT_MODEL,
        capture_prompt: str,
        capture_images: list[str | tuple[str, str]] | None = None,
        contents: list[AistudioContent] | None = None,
        system_instruction_content: AistudioContent | None = None,
        tools: list[list] | None = None,
        tool_config: list[object] | None = None,
        safety_settings: list[list] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        generation_config_overrides: dict | None = None,
        sanitize_plain_text: bool = True,
        force_refresh_capture: bool = False,
    ) -> AsyncGenerator[tuple[str, object | None], None]:
        async with self._session.request_scope():
            captured = await self._capture.capture(
                prompt=capture_prompt,
                model=model,
                images=capture_images,
                contents=contents,
                system_instruction_content=system_instruction_content,
                tools=tools,
                tool_config=tool_config,
                safety_settings=safety_settings,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                max_tokens=max_tokens,
                generation_config_overrides=generation_config_overrides,
                sanitize_plain_text=sanitize_plain_text,
                force_refresh=force_refresh_capture,
            )
            if not captured:
                raise RequestError(0, "无法拦截请求")
            async for event in self._streaming.stream_chat(
                captured=captured,
                model=model,
            ):
                yield event


__all__ = ["InstanceRequestClient"]
