"""Gemini output -> OpenAI Chat Completions response rendering."""

from __future__ import annotations

import base64
import json
import time
from typing import cast

from aistudio_api.application.format_converters.common import (
    image_markdown,
    map_finish_reason_openai,
    new_id,
    sse_data,
    to_openai_usage,
)
from aistudio_api.application.format_converters.signature_cache import (
    signature_cache,
)
from aistudio_api.application.generation_service import (
    GenerationOutcome,
    StreamEvent,
)
from aistudio_api.domain.models import GeneratedImage


def _image_text(images: object) -> str:
    if not isinstance(images, list):
        return ""
    rendered: list[str] = []
    for image in images:
        if isinstance(image, GeneratedImage):
            payload = base64.b64encode(image.data).decode("ascii")
            rendered.append(image_markdown(image.mime, payload))
    return "\n".join(rendered)


def _arguments_text(function_call: dict[str, object]) -> str:
    args = function_call.get("args")
    if args is None:
        args = function_call.get("arguments", {})
    if isinstance(args, str):
        return args
    return json.dumps(args, ensure_ascii=False)


def _tool_call(function_call: dict[str, object], *, index: int) -> dict[str, object]:
    call_id = function_call.get("id") or function_call.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        call_id = new_id("call_")
    signature = function_call.get("thought_signature")
    if isinstance(signature, str):
        signature_cache.put(call_id, signature)
    name = function_call.get("name")
    return {
        "index": index,
        "id": call_id,
        "type": "function",
        "function": {
            "name": name if isinstance(name, str) else "",
            "arguments": _arguments_text(function_call),
        },
    }


def openai_response(*, model: str, outcome: GenerationOutcome) -> dict[str, object]:
    output = outcome.output
    text = output.text
    images = _image_text(output.images)
    content = f"{text}\n{images}" if text and images else text or images

    message: dict[str, object] = {"role": "assistant", "content": content}
    if output.thinking:
        message["reasoning_content"] = output.thinking

    function_calls = output.function_calls
    if function_calls:
        message["tool_calls"] = [
            _tool_call(function_call, index=index)
            for index, function_call in enumerate(function_calls)
        ]

    finish_reason = (
        "tool_calls"
        if function_calls
        else map_finish_reason_openai(outcome.finish_reason)
    )
    return {
        "id": new_id("chatcmpl-"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": to_openai_usage(output.usage),
    }


class OpenAIStreamRenderer:
    """Render protocol-neutral stream events as OpenAI SSE chunks."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.id = new_id("chatcmpl-")
        self.created = int(time.time())
        self._started = False
        self._done = False
        self._tool_index = 0
        self._has_tool_calls = False

    def _emit(
        self,
        delta: dict[str, object],
        *,
        finish_reason: str | None = None,
        usage: dict[str, object] | None = None,
    ) -> str:
        payload: dict[str, object] = {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }
        if usage is not None:
            payload["usage"] = usage
        return sse_data(payload)

    def _content_delta(self, key: str, text: str) -> str:
        if not text:
            return ""
        delta: dict[str, object] = {key: text}
        if not self._started:
            self._started = True
            delta = {"role": "assistant", **delta}
        return self._emit(delta)

    def _terminal(self, finish_reason: str, usage: dict[str, object] | None) -> str:
        self._done = True
        chunk = self._emit(
            {},
            finish_reason=finish_reason,
            usage=to_openai_usage(usage),
        )
        return chunk + "data: [DONE]\n\n"

    def render(self, event: StreamEvent) -> str:
        if self._done:
            return ""

        if event.kind == "routing_wait":
            return ": resolving-instance\n\n"

        if event.kind == "usage":
            finish_reason = (
                "tool_calls"
                if self._has_tool_calls
                else map_finish_reason_openai(event.finish_reason)
            )
            usage = (
                cast("dict[str, object]", event.payload)
                if isinstance(event.payload, dict)
                else None
            )
            return self._terminal(finish_reason, usage)

        if event.kind == "body":
            text = event.payload if isinstance(event.payload, str) else ""
            return self._content_delta("content", text)

        if event.kind == "thinking":
            text = event.payload if isinstance(event.payload, str) else ""
            return self._content_delta("reasoning_content", text)

        if event.kind == "images":
            return self._content_delta("content", _image_text(event.payload))

        if event.kind == "reasoning_images":
            return self._content_delta("reasoning_content", _image_text(event.payload))

        if event.kind == "tool_calls":
            calls = event.payload if isinstance(event.payload, list) else []
            chunks: list[str] = []
            for call in calls:
                if not isinstance(call, dict):
                    continue
                self._has_tool_calls = True
                delta: dict[str, object] = {
                    "tool_calls": [
                        _tool_call(
                            cast("dict[str, object]", call),
                            index=self._tool_index,
                        )
                    ]
                }
                self._tool_index += 1
                if not self._started:
                    self._started = True
                    delta = {"role": "assistant", **delta}
                chunks.append(self._emit(delta))
            return "".join(chunks)

        return ""

    def close(self) -> str:
        if self._done:
            return ""
        finish_reason = "tool_calls" if self._has_tool_calls else "stop"
        return self._terminal(finish_reason, None)
