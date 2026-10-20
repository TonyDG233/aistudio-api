"""Gemini output -> OpenAI Responses response/SSE rendering."""

from __future__ import annotations

import base64
import json
import time
from typing import cast

from aistudio_api.application.format_converters.common import (
    image_markdown,
    new_id,
    sse_event,
    to_openai_usage,
)
from aistudio_api.application.format_converters.signature_cache import signature_cache
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


def _function_item(
    item_id: str,
    call: dict[str, object],
) -> dict[str, object]:
    call_id = call.get("id") or call.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        call_id = new_id("call_")
    signature = call.get("thought_signature")
    if isinstance(signature, str):
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


def _response_object(
    *,
    model: str,
    response_id: str,
    created_at: int,
    status: str,
    output: list[dict[str, object]],
    usage: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "model": model,
        "output": output,
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
        "usage": responses_usage(usage),
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

    return _response_object(
        model=model,
        response_id=outcome.response_id or new_id("resp_"),
        created_at=int(time.time()),
        status="completed"
        if outcome.finish_reason not in {"error", "failed"}
        else "failed",
        output=items,
        usage=outcome.output.usage,
    )


class ResponsesStreamRenderer:
    """Render protocol-neutral stream events as Responses SSE events."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.response_id = new_id("resp_")
        self.created_at = int(time.time())
        self.sequence = 0
        self.text = ""
        self.thinking = ""
        self.tool_calls: list[dict[str, object]] = []
        self.message_id = new_id("msg_")
        self.reasoning_id = new_id("rs_")
        self.message_started = False
        self.message_closed = False
        self.reasoning_started = False
        self.reasoning_closed = False
        self.done = False

    def _event(self, event_type: str, payload: dict[str, object]) -> str:
        self.sequence += 1
        payload = {**payload, "type": event_type, "sequence_number": self.sequence}
        return sse_event(event_type, payload)

    def _response(self, status: str, output: list[dict[str, object]], usage):
        return _response_object(
            model=self.model,
            response_id=self.response_id,
            created_at=self.created_at,
            status=status,
            output=output,
            usage=usage,
        )

    def start(self) -> str:
        return self._event(
            "response.created",
            {"response": self._response("in_progress", [], None)},
        )

    def _start_message(self) -> str:
        if self.message_started:
            return ""
        self.message_started = True
        item = {
            "id": self.message_id,
            "type": "message",
            "status": "in_progress",
            "role": "assistant",
            "content": [],
        }
        return self._event(
            "response.output_item.added",
            {"output_index": 0, "item": item},
        )

    def _start_reasoning(self) -> str:
        if self.reasoning_started:
            return ""
        self.reasoning_started = True
        item = {
            "id": self.reasoning_id,
            "type": "reasoning",
            "status": "in_progress",
            "summary": [],
        }
        return self._event(
            "response.output_item.added",
            {"output_index": 0, "item": item},
        )

    def _close_reasoning(self) -> str:
        if not self.reasoning_started or self.reasoning_closed:
            return ""
        self.reasoning_closed = True
        return self._event(
            "response.output_item.done",
            {
                "output_index": 0,
                "item": _reasoning_item(self.reasoning_id, self.thinking),
            },
        )

    def _close_message(self) -> str:
        if not self.message_started or self.message_closed:
            return ""
        self.message_closed = True
        return self._event(
            "response.output_item.done",
            {
                "output_index": 0,
                "item": _message_item(self.message_id, self.text),
            },
        )

    def _text_delta(self, text: str) -> str:
        if not text:
            return ""
        chunks = [self._start_message()]
        self.text += text
        chunks.append(
            self._event(
                "response.output_text.delta",
                {
                    "item_id": self.message_id,
                    "output_index": 0,
                    "content_index": 0,
                    "delta": text,
                    "logprobs": [],
                },
            )
        )
        return "".join(chunks)

    def _thinking_delta(self, text: str) -> str:
        if not text:
            return ""
        chunks = [self._start_reasoning()]
        self.thinking += text
        chunks.append(
            self._event(
                "response.reasoning_summary_text.delta",
                {
                    "item_id": self.reasoning_id,
                    "output_index": 0,
                    "summary_index": 0,
                    "delta": text,
                },
            )
        )
        return "".join(chunks)

    def _function_call(self, call: dict[str, object], output_index: int) -> str:
        self.tool_calls.append(call)
        item = _function_item(new_id("fc_"), call)
        chunks = [
            self._event(
                "response.output_item.added",
                {
                    "output_index": output_index,
                    "item": {**item, "status": "in_progress", "arguments": ""},
                },
            ),
            self._event(
                "response.function_call_arguments.delta",
                {
                    "item_id": item["id"],
                    "output_index": output_index,
                    "delta": item["arguments"],
                },
            ),
            self._event(
                "response.function_call_arguments.done",
                {
                    "item_id": item["id"],
                    "output_index": output_index,
                    "arguments": item["arguments"],
                },
            ),
            self._event(
                "response.output_item.done",
                {"output_index": output_index, "item": item},
            ),
        ]
        return "".join(chunks)

    def render(self, event: StreamEvent) -> str:
        if self.done:
            return ""
        if event.kind == "routing_wait":
            return ": resolving-instance\n\n"
        if event.kind == "usage":
            return self._complete(
                event.payload if isinstance(event.payload, dict) else None
            )
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
                if not isinstance(call, dict):
                    continue
                chunks.append(
                    self._function_call(
                        cast("dict[str, object]", call),
                        output_index=len(self.tool_calls),
                    )
                )
            return "".join(chunks)
        return ""

    def _final_output(self) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        if self.thinking:
            items.append(_reasoning_item(self.reasoning_id, self.thinking))
        if self.text:
            items.append(_message_item(self.message_id, self.text))
        for call in self.tool_calls:
            items.append(_function_item(new_id("fc_"), call))
        return items

    def _complete(self, usage: dict[str, object] | None) -> str:
        if self.done:
            return ""
        self.done = True
        chunks = [self._close_reasoning(), self._close_message()]
        final = self._response("completed", self._final_output(), usage)
        chunks.append(self._event("response.completed", {"response": final}))
        return "".join(chunks)

    def close(self) -> str:
        if self.done:
            return ""
        return self._complete(None)


def response_failed_event(exc: Exception) -> str:
    payload = {
        "type": "response.failed",
        "response": {
            "id": new_id("resp_"),
            "object": "response",
            "status": "failed",
            "error": {"code": "server_error", "message": str(exc)},
        },
        "sequence_number": 1,
    }
    return sse_event("response.failed", payload)
