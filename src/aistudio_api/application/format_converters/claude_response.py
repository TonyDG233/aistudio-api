"""Gemini generation outcome to Claude Messages response rendering."""

from __future__ import annotations

import base64
import json
from typing import cast

from aistudio_api.application.format_converters.common import (
    image_markdown,
    map_finish_reason_claude,
    new_id,
    sse_event,
    to_claude_usage,
)
from aistudio_api.application.format_converters.signature_cache import (
    signature_cache,
)
from aistudio_api.application.generation_service import (
    GenerationOutcome,
    StreamEvent,
)
from aistudio_api.domain.models import GeneratedImage


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [cast("dict[str, object]", item) for item in value if isinstance(item, dict)]


def _image_markdown_chunks(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    chunks: list[str] = []
    for item in value:
        if isinstance(item, GeneratedImage):
            chunks.append(
                image_markdown(item.mime, base64.b64encode(item.data).decode("ascii"))
            )
    return chunks


def _tool_call_id(call: dict[str, object]) -> str:
    raw = call.get("id") or call.get("call_id")
    if isinstance(raw, str) and raw:
        call_id = raw
    elif raw is not None:
        call_id = str(raw)
    else:
        call_id = new_id("toolu_")
    signature = call.get("thought_signature")
    if isinstance(signature, str):
        signature_cache.put(call_id, signature)
    return call_id


def _tool_call_input(call: dict[str, object]) -> dict[str, object]:
    raw = call.get("args")
    if raw is None:
        raw = call.get("arguments")
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed: object = json.loads(raw)
        except json.JSONDecodeError:
            return {"value": raw}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return {"value": raw}


def claude_response(*, model: str, outcome: GenerationOutcome) -> dict[str, object]:
    """Render a non-streaming Claude ``message`` object."""
    output = outcome.output
    candidate = output.candidates[0] if output.candidates else None
    thinking = candidate.thinking if candidate is not None else ""
    text = candidate.text if candidate is not None else ""
    images = candidate.images if candidate is not None else []
    calls = candidate.function_calls if candidate is not None else []

    content: list[dict[str, object]] = []
    if thinking:
        signature = (
            candidate.thought_signature
            if candidate is not None and candidate.thought_signature
            else new_id("proxy_thinking_")
        )
        content.append(
            {
                "type": "thinking",
                "thinking": thinking,
                "signature": signature,
            }
        )

    text_parts: list[str] = []
    if text:
        text_parts.append(text)
    text_parts.extend(
        image_markdown(image.mime, base64.b64encode(image.data).decode("ascii"))
        for image in images
    )
    if text_parts:
        content.append({"type": "text", "text": "\n".join(text_parts)})

    for call in calls:
        content.append(
            {
                "type": "tool_use",
                "id": _tool_call_id(call),
                "name": str(call.get("name") or "unknown"),
                "input": _tool_call_input(call),
            }
        )

    if not content:
        content.append({"type": "text", "text": ""})

    return {
        "id": new_id("msg_"),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": map_finish_reason_claude(
            outcome.finish_reason, has_tool_use=bool(calls)
        ),
        "stop_sequence": None,
        "usage": to_claude_usage(output.usage),
    }


class ClaudeStreamRenderer:
    """Render protocol-neutral stream events as Anthropic SSE events."""

    def __init__(self, model: str) -> None:
        self._model = model
        self._message_id = new_id("msg_")
        self._started = False
        self._closed = False
        self._next_index = 0
        self._open_block: str | None = None
        self._open_index = -1
        self._has_tool_use = False
        self._finish_reason = "STOP"
        self._usage: dict[str, object] | None = None
        self._thinking_signature: str | None = None

    def start(self) -> str:
        if self._started:
            return ""
        self._started = True
        message = {
            "id": self._message_id,
            "type": "message",
            "role": "assistant",
            "model": self._model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": to_claude_usage(None),
        }
        return sse_event("ping", {}) + sse_event(
            "message_start", {"type": "message_start", "message": message}
        )

    def render(self, event: StreamEvent) -> str:
        if self._closed:
            return ""
        kind = event.kind
        if kind == "routing_wait":
            return ": resolving-instance\n\n"
        if kind == "usage":
            if isinstance(event.payload, dict):
                self._usage = cast("dict[str, object]", event.payload)
            if event.finish_reason is not None:
                self._finish_reason = event.finish_reason
            return ""
        if kind == "thought_signature":
            if isinstance(event.payload, str) and event.payload:
                self._thinking_signature = event.payload
            return ""
        if kind == "thinking":
            return self._render_thinking(event)
        if kind == "body":
            if not isinstance(event.payload, str):
                return ""
            return self._render_text(event.payload)
        if kind == "tool_calls":
            return self._render_tool_calls(event.payload)
        if kind in {"images", "reasoning_images"}:
            images = _image_markdown_chunks(event.payload)
            if not images:
                return ""
            return self._render_text("\n".join(images))
        return ""

    def close(self) -> str:
        if self._closed:
            return ""
        self._closed = True
        chunks = [self._ensure_started(), self._close_open_block()]
        chunks.append(
            sse_event(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {
                        "stop_reason": map_finish_reason_claude(
                            self._finish_reason, has_tool_use=self._has_tool_use
                        ),
                        "stop_sequence": None,
                    },
                    "usage": to_claude_usage(self._usage),
                },
            )
        )
        chunks.append(sse_event("message_stop", {"type": "message_stop"}))
        return "".join(chunks)

    def _ensure_started(self) -> str:
        return "" if self._started else self.start()

    def _start_block(self, block_type: str, content_block: dict[str, object]) -> str:
        self._open_index = self._next_index
        self._next_index += 1
        self._open_block = block_type
        return sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": self._open_index,
                "content_block": content_block,
            },
        )

    def _close_open_block(self) -> str:
        if self._open_block is None:
            return ""
        chunks: list[str] = []
        if self._open_block == "thinking":
            signature = self._thinking_signature or new_id("proxy_thinking_")
            chunks.append(
                sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": self._open_index,
                        "delta": {
                            "type": "signature_delta",
                            "signature": signature,
                        },
                    },
                )
            )
        chunks.append(
            sse_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": self._open_index},
            )
        )
        self._open_block = None
        return "".join(chunks)

    def _render_thinking(self, event: StreamEvent) -> str:
        if not isinstance(event.payload, str):
            return ""
        chunks = [self._ensure_started()]
        if self._open_block != "thinking":
            chunks.append(self._close_open_block())
            chunks.append(
                self._start_block(
                    "thinking",
                    {"type": "thinking", "thinking": "", "signature": ""},
                )
            )
        chunks.append(
            sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": self._open_index,
                    "delta": {
                        "type": "thinking_delta",
                        "thinking": event.payload,
                    },
                },
            )
        )
        if event.thought_signature:
            self._thinking_signature = event.thought_signature
        return "".join(chunks)

    def _render_text(self, text: str) -> str:
        chunks = [self._ensure_started()]
        if self._open_block != "text":
            chunks.append(self._close_open_block())
            chunks.append(self._start_block("text", {"type": "text", "text": ""}))
        chunks.append(
            sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": self._open_index,
                    "delta": {"type": "text_delta", "text": text},
                },
            )
        )
        return "".join(chunks)

    def _render_tool_calls(self, payload: object) -> str:
        calls = _as_dict_list(payload)
        if not calls:
            return ""
        chunks = [self._ensure_started(), self._close_open_block()]
        for call in calls:
            tool_id = _tool_call_id(call)
            name = str(call.get("name") or "unknown")
            self._has_tool_use = True
            chunks.append(
                self._start_block(
                    "tool_use",
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": name,
                        "input": {},
                    },
                )
            )
            chunks.append(
                sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": self._open_index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(
                                _tool_call_input(call), ensure_ascii=False
                            ),
                        },
                    },
                )
            )
            chunks.append(self._close_open_block())
        return "".join(chunks)
