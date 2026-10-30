"""Shared helpers for OpenAI / Claude format conversion."""

from __future__ import annotations

import base64
import binascii
import json
import secrets
import time
from typing import Any


class ConversionError(ValueError):
    """Client request cannot be represented as a Gemini request.

    ``field`` lets protocol renderers point at the offending parameter.
    """

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


# Ark uses the same placeholder for Gemini 3 functionCall validation. It is
# accepted by the upstream when the real signature is unavailable; real
# signatures are preferred when the proxy has them cached.
DUMMY_THOUGHT_SIGNATURE = "context_engineering_is_the_way_to_go"


def strip_models_prefix(model: str) -> str:
    return model.removeprefix("models/")


def new_id(prefix: str) -> str:
    """Generate a protocol id with millisecond time and random suffix."""
    millis = time.time_ns() // 1_000_000
    return f"{prefix}{millis}_{secrets.token_hex(4)}"


def map_finish_reason_openai(reason: str | None) -> str:
    normalized = (reason or "STOP").upper()
    if normalized in {"MAX_TOKENS", "LENGTH"}:
        return "length"
    if normalized in {
        "SAFETY",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
    }:
        return "content_filter"
    return "stop"


def map_finish_reason_claude(reason: str | None, *, has_tool_use: bool) -> str:
    if has_tool_use:
        return "tool_use"
    normalized = (reason or "STOP").upper()
    if normalized in {"MAX_TOKENS", "LENGTH"}:
        return "max_tokens"
    return "end_turn"


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def to_openai_usage(usage: dict[str, object] | None) -> dict[str, object]:
    data = usage or {}
    prompt = _as_int(data.get("prompt_tokens"))
    completion = _as_int(data.get("completion_tokens"))
    total = _as_int(data.get("total_tokens")) or (prompt + completion)
    cached = 0
    reasoning = 0
    prompt_details = data.get("prompt_tokens_details")
    if isinstance(prompt_details, dict):
        cached = _as_int(prompt_details.get("cached_tokens"))
    completion_details = data.get("completion_tokens_details")
    if isinstance(completion_details, dict):
        reasoning = _as_int(completion_details.get("reasoning_tokens"))
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "prompt_tokens_details": {"cached_tokens": cached},
        "completion_tokens_details": {"reasoning_tokens": reasoning},
    }


def to_claude_usage(usage: dict[str, object] | None) -> dict[str, object]:
    data = usage or {}
    prompt = _as_int(data.get("prompt_tokens"))
    completion = _as_int(data.get("completion_tokens"))
    cached = 0
    prompt_details = data.get("prompt_tokens_details")
    if isinstance(prompt_details, dict):
        cached = _as_int(prompt_details.get("cached_tokens"))
    return {
        "input_tokens": max(0, prompt - cached),
        "output_tokens": completion,
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": 0,
    }


def sse_data(payload: object) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def sse_event(name: str, payload: object) -> str:
    return f"event: {name}\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def image_markdown(mime_type: str, data_b64: str) -> str:
    return f"![Generated Image](data:{mime_type};base64,{data_b64})"


def text_from_blocks(blocks: object) -> str:
    """Join Anthropic text blocks (or a plain string) with newlines."""
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return ""
    texts: list[str] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                texts.append(text)
    return "\n".join(texts)


def parse_arguments(value: object, *, field: str) -> dict[str, object]:
    """Parse OpenAI tool-call arguments into a JSON object."""
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    if not isinstance(value, str):
        raise ConversionError(
            "tool call arguments must be a JSON object or string",
            field=field,
        )
    try:
        parsed: Any = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ConversionError(
            f"tool call arguments are not valid JSON: {exc.msg}",
            field=field,
        ) from exc
    if not isinstance(parsed, dict):
        raise ConversionError(
            "tool call arguments must decode to a JSON object",
            field=field,
        )
    return parsed


def decode_data_url(url: str, *, field: str) -> tuple[str, str]:
    """Validate a base64 data URL and return (mime_type, base64 payload)."""
    if not url.startswith("data:"):
        raise ConversionError("expected a data: URL", field=field)
    header, separator, payload = url.partition(",")
    if not separator or ";base64" not in header:
        raise ConversionError(
            "data URL must be base64-encoded (data:<mime>;base64,<data>)",
            field=field,
        )
    mime_type = header[len("data:") :].split(";", 1)[0].strip().lower()
    if not mime_type.startswith("image/"):
        raise ConversionError(
            f"unsupported data URL media type: {mime_type or 'unknown'}",
            field=field,
        )
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConversionError(
            "data URL payload is not valid base64", field=field
        ) from exc
    if not raw:
        raise ConversionError("data URL payload is empty", field=field)
    return mime_type, base64.b64encode(raw).decode("ascii")
