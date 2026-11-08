"""Gemini output -> OpenAI Responses response/SSE rendering.

The streaming renderer owns the Responses event grammar explicitly:

* text: ``output_item.added -> content_part.added -> output_text.delta ->
  output_text.done -> content_part.done -> output_item.done``
* reasoning: ``output_item.added -> reasoning_summary_part.added ->
  reasoning_summary_text.delta -> reasoning_summary_text.done ->
  reasoning_summary_part.done -> output_item.done``
* ``output_index`` is allocated in real output-item order (reasoning first when
  it arrives first), never pinned to 0.
* ``MAX_TOKENS`` terminates with ``response.incomplete`` and
  ``incomplete_details.reason == "max_output_tokens"``.
* ``response.failed`` reuses the same ``response.id`` and only advances
  ``sequence_number``; SSE keepalive comments never touch the sequence.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import cast

from aistudio_api.application.format_converters.common import (
    image_markdown,
    new_id,
    sse_event,
    to_openai_usage,
)
from aistudio_api.application.generation_service import (
    GenerationOutcome,
    StreamEvent,
)
from aistudio_api.application.signature_cache import signature_cache
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


def responses_usage(usage: dict[str, object] | None) -> dict[str, object]:
    base = to_openai_usage(usage)
    prompt_details = base.get("prompt_tokens_details")
    completion_details = base.get("completion_tokens_details")
    cached = (
        prompt_details.get("cached_tokens", 0)
        if isinstance(prompt_details, dict)
        else 0
    )
    reasoning = (
        completion_details.get("reasoning_tokens", 0)
        if isinstance(completion_details, dict)
        else 0
    )
    return {
        "input_tokens": base["prompt_tokens"],
        "input_tokens_details": {"cached_tokens": cached},
        "output_tokens": base["completion_tokens"],
        "output_tokens_details": {"reasoning_tokens": reasoning},
        "total_tokens": base["total_tokens"],
    }


def _reasoning_item(item_id: str, text: str) -> dict[str, object]:
    return {
        "id": item_id,
        "type": "reasoning",
        "status": "completed",
        "summary": [{"type": "summary_text", "text": text}] if text else [],
    }


def _message_item(item_id: str, text: str) -> dict[str, object]:
    return {
        "id": item_id,
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": (
            [{"type": "output_text", "text": text, "annotations": []}] if text else []
        ),
    }


def _function_item(item_id: str, call: dict[str, object]) -> dict[str, object]:
    call_id = call.get("id") or call.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        call_id = new_id("call_")
    signature = call.get("thought_signature") or call.get("thoughtSignature")
    if isinstance(signature, str) and signature:
        signature_cache.put(call_id, signature)
    name = call.get("name")
    return {
        "id": item_id,
        "type": "function_call",
        "status": "completed",
        "call_id": call_id,
        "name": name if isinstance(name, str) else "",
        "arguments": _arguments_text(call),
    }


def _output_text(output: list[dict[str, object]]) -> str:
    texts: list[str] = []
    for item in output:
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str):
                    texts.append(text)
    return "".join(texts)


def _response_status(
    finish_reason: str | None,
) -> tuple[str, dict[str, object] | None]:
    normalized = (finish_reason or "STOP").upper()
    if normalized in {"MAX_TOKENS", "LENGTH"}:
        return "incomplete", {"reason": "max_output_tokens"}
    if normalized in {"ERROR", "FAILED"}:
        return "failed", None
    return "completed", None


def _response_object(
    *,
    model: str,
    response_id: str,
    created_at: int,
    status: str,
    output: list[dict[str, object]],
    usage: dict[str, object] | None,
    error: dict[str, object] | None = None,
    incomplete_details: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "error": error,
        "incomplete_details": incomplete_details,
        "instructions": None,
        "max_output_tokens": None,
        "model": model,
        "output": output,
        "output_text": _output_text(output),
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": None,
        "store": False,
        "temperature": None,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": None,
        "truncation": "disabled",
        "usage": responses_usage(usage) if usage is not None else None,
        "user": None,
        "metadata": {},
    }


def responses_response(*, model: str, outcome: GenerationOutcome) -> dict[str, object]:
    output = outcome.output
    text = output.text
    images = _image_text(output.images)
    if images:
        text = f"{text}\n{images}" if text else images
    reasoning_text = output.thinking
    for image in output.reasoning_images:
        payload = base64.b64encode(image.data).decode("ascii")
        markdown = image_markdown(image.mime, payload)
        reasoning_text = f"{reasoning_text}\n{markdown}" if reasoning_text else markdown

    items: list[dict[str, object]] = []
    if reasoning_text:
        items.append(_reasoning_item(new_id("rs_"), reasoning_text))
    if text:
        items.append(_message_item(new_id("msg_"), text))
    for call in output.function_calls:
        items.append(_function_item(new_id("fc_"), call))

    status, incomplete = _response_status(outcome.finish_reason)
    return _response_object(
        model=model,
        response_id=new_id("resp_"),
        created_at=int(time.time()),
        status=status,
        output=items,
        usage=output.usage,
        incomplete_details=incomplete,
    )


@dataclass
class _ReasoningState:
    item_id: str
    output_index: int
    text: str = ""
    item_done: bool = False


@dataclass
class _MessageState:
    item_id: str
    output_index: int
    text: str = ""
    item_done: bool = False


@dataclass
class _RenderState:
    sequence: int = 0
    next_output_index: int = 0
    final_items: dict[int, dict[str, object]] = field(default_factory=dict)


class ResponsesStreamRenderer:
    """Render protocol-neutral stream events as Responses SSE events."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.response_id = new_id("resp_")
        self.created_at = int(time.time())
        self._state = _RenderState()
        self._reasoning: _ReasoningState | None = None
        self._message: _MessageState | None = None
        self.done = False

    @property
    def sequence(self) -> int:
        return self._state.sequence

    def _event(self, event_type: str, payload: dict[str, object]) -> str:
        self._state.sequence += 1
        body = {**payload, "type": event_type, "sequence_number": self._state.sequence}
        return sse_event(event_type, body)

    def _next_output_index(self) -> int:
        index = self._state.next_output_index
        self._state.next_output_index += 1
        return index

    def _response(
        self,
        status: str,
        output: list[dict[str, object]],
        usage: dict[str, object] | None,
        *,
        error: dict[str, object] | None = None,
        incomplete_details: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return _response_object(
            model=self.model,
            response_id=self.response_id,
            created_at=self.created_at,
            status=status,
            output=output,
            usage=usage,
            error=error,
            incomplete_details=incomplete_details,
        )

    def _final_output(self) -> list[dict[str, object]]:
        return [
            item
            for _index, item in sorted(
                self._state.final_items.items(), key=lambda entry: entry[0]
            )
        ]

    def start(self) -> str:
        return self._event(
            "response.created",
            {"response": self._response("in_progress", [], None)},
        )

    # --------------------------------------------------------------- reasoning
    def _start_reasoning(self) -> str:
        if self._reasoning is not None:
            return ""
        index = self._next_output_index()
        state = _ReasoningState(new_id("rs_"), index)
        self._reasoning = state
        item = {
            "id": state.item_id,
            "type": "reasoning",
            "status": "in_progress",
            "summary": [],
        }
        chunks = [
            self._event(
                "response.output_item.added",
                {"output_index": index, "item": item},
            ),
            self._event(
                "response.reasoning_summary_part.added",
                {
                    "item_id": state.item_id,
                    "output_index": index,
                    "summary_index": 0,
                    "part": {"type": "summary_text", "text": ""},
                },
            ),
        ]
        return "".join(chunks)

    def _thinking_delta(self, text: str) -> str:
        if not text:
            return ""
        chunks = [self._start_reasoning()]
        state = self._reasoning
        if state is None:
            return ""
        state.text += text
        chunks.append(
            self._event(
                "response.reasoning_summary_text.delta",
                {
                    "item_id": state.item_id,
                    "output_index": state.output_index,
                    "summary_index": 0,
                    "delta": text,
                },
            )
        )
        return "".join(chunks)

    def _close_reasoning(self) -> str:
        state = self._reasoning
        if state is None or state.item_done:
            return ""
        state.item_done = True
        item = _reasoning_item(state.item_id, state.text)
        self._state.final_items[state.output_index] = item
        chunks = [
            self._event(
                "response.reasoning_summary_text.done",
                {
                    "item_id": state.item_id,
                    "output_index": state.output_index,
                    "summary_index": 0,
                    "text": state.text,
                },
            ),
            self._event(
                "response.reasoning_summary_part.done",
                {
                    "item_id": state.item_id,
                    "output_index": state.output_index,
                    "summary_index": 0,
                    "part": {"type": "summary_text", "text": state.text},
                },
            ),
            self._event(
                "response.output_item.done",
                {"output_index": state.output_index, "item": item},
            ),
        ]
        return "".join(chunks)

    # ----------------------------------------------------------------- message
    def _start_message(self) -> str:
        if self._message is not None:
            return ""
        index = self._next_output_index()
        state = _MessageState(new_id("msg_"), index)
        self._message = state
        item = {
            "id": state.item_id,
            "type": "message",
            "status": "in_progress",
            "role": "assistant",
            "content": [],
        }
        chunks = [
            self._event(
                "response.output_item.added",
                {"output_index": index, "item": item},
            ),
            self._event(
                "response.content_part.added",
                {
                    "item_id": state.item_id,
                    "output_index": index,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                },
            ),
        ]
        return "".join(chunks)

    def _text_delta(self, text: str) -> str:
        if not text:
            return ""
        chunks = [self._start_message()]
        state = self._message
        if state is None:
            return ""
        state.text += text
        chunks.append(
            self._event(
                "response.output_text.delta",
                {
                    "item_id": state.item_id,
                    "output_index": state.output_index,
                    "content_index": 0,
                    "delta": text,
                    "logprobs": [],
                },
            )
        )
        return "".join(chunks)

    def _close_message(self) -> str:
        state = self._message
        if state is None or state.item_done:
            return ""
        state.item_done = True
        item = _message_item(state.item_id, state.text)
        self._state.final_items[state.output_index] = item
        part = {"type": "output_text", "text": state.text, "annotations": []}
        chunks = [
            self._event(
                "response.output_text.done",
                {
                    "item_id": state.item_id,
                    "output_index": state.output_index,
                    "content_index": 0,
                    "text": state.text,
                    "logprobs": [],
                },
            ),
            self._event(
                "response.content_part.done",
                {
                    "item_id": state.item_id,
                    "output_index": state.output_index,
                    "content_index": 0,
                    "part": part,
                },
            ),
            self._event(
                "response.output_item.done",
                {"output_index": state.output_index, "item": item},
            ),
        ]
        return "".join(chunks)

    # ------------------------------------------------------------- tool calls
    def _function_call(self, call: dict[str, object]) -> str:
        item = _function_item(new_id("fc_"), call)
        index = self._next_output_index()
        self._state.final_items[index] = item
        added = {
            **item,
            "status": "in_progress",
            "arguments": "",
        }
        arguments = cast("str", item["arguments"])
        chunks = [
            self._event(
                "response.output_item.added",
                {"output_index": index, "item": added},
            ),
            self._event(
                "response.function_call_arguments.delta",
                {
                    "item_id": item["id"],
                    "output_index": index,
                    "delta": arguments,
                },
            ),
            self._event(
                "response.function_call_arguments.done",
                {
                    "item_id": item["id"],
                    "output_index": index,
                    "arguments": arguments,
                },
            ),
            self._event(
                "response.output_item.done",
                {"output_index": index, "item": item},
            ),
        ]
        return "".join(chunks)

    # ------------------------------------------------------------------- render
    def render(self, event: StreamEvent) -> str:
        if self.done:
            return ""
        if event.kind == "routing_wait":
            return ": resolving-instance\n\n"
        if event.kind == "usage":
            usage = event.payload if isinstance(event.payload, dict) else None
            return self._complete(usage, event.finish_reason)
        if event.kind == "body":
            return self._text_delta(
                event.payload if isinstance(event.payload, str) else ""
            )
        if event.kind == "thinking":
            return self._thinking_delta(
                event.payload if isinstance(event.payload, str) else ""
            )
        if event.kind == "images":
            return self._text_delta(_image_text(event.payload))
        if event.kind == "reasoning_images":
            return self._thinking_delta(_image_text(event.payload))
        if event.kind == "tool_calls":
            calls = event.payload if isinstance(event.payload, list) else []
            chunks: list[str] = []
            for call in calls:
                if isinstance(call, dict):
                    chunks.append(self._function_call(cast("dict[str, object]", call)))
            return "".join(chunks)
        return ""

    def _complete(
        self,
        usage: dict[str, object] | None,
        finish_reason: str | None,
    ) -> str:
        if self.done:
            return ""
        self.done = True
        chunks = [self._close_reasoning(), self._close_message()]
        status, incomplete = _response_status(finish_reason)
        output = self._final_output()
        if status == "failed":
            error: dict[str, object] = {
                "code": "server_error",
                "message": "generation failed",
            }
            response = self._response("failed", output, usage, error=error)
            chunks.append(self._event("response.failed", {"response": response}))
            return "".join(chunks)
        event_type = (
            "response.incomplete" if status == "incomplete" else "response.completed"
        )
        response = self._response(status, output, usage, incomplete_details=incomplete)
        chunks.append(self._event(event_type, {"response": response}))
        return "".join(chunks)

    def close(self) -> str:
        if self.done:
            return ""
        return self._complete(None, "STOP")

    def failed(self, exc: Exception) -> str:
        """Emit ``response.failed`` with the same response id (post-header)."""
        if self.done:
            return ""
        self.done = True
        chunks = [self._close_reasoning(), self._close_message()]
        error: dict[str, object] = {
            "code": "server_error",
            "message": str(exc),
        }
        response = self._response("failed", self._final_output(), None, error=error)
        chunks.append(self._event("response.failed", {"response": response}))
        return "".join(chunks)


__all__ = [
    "ResponsesStreamRenderer",
    "responses_response",
    "responses_usage",
]
