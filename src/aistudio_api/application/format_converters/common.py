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


_SCHEMA_OBJECT_KEYS = ("properties", "patternProperties", "dependentSchemas")
_SCHEMA_LIST_KEYS = ("anyOf", "any_of", "oneOf", "one_of", "allOf", "all_of")
_SCHEMA_COMBINER_KEYS = ("anyOf", "any_of", "oneOf", "one_of")
_SCHEMA_SINGLE_KEYS = (
    "items",
    "additionalItems",
    "additionalProperties",
    "contains",
    "propertyNames",
    "not",
    "not_",
    "if",
    "then",
    "else",
)
_ARRAY_ITEM_FALLBACK = {"type": "string"}
_ARRAY_ITEM_FALLBACK_UPPER = {"type": "STRING"}


def _schema_type_labels(schema: dict[str, object]) -> list[str]:
    raw_type = schema.get("type")
    if isinstance(raw_type, str):
        return [raw_type]
    if isinstance(raw_type, list):
        return [item for item in raw_type if isinstance(item, str)]
    return []


def _non_null_type_labels(schema: dict[str, object]) -> list[str]:
    return [
        label
        for label in _schema_type_labels(schema)
        if label.strip().lower() != "null"
    ]


def _is_null_branch(branch: object) -> bool:
    if not isinstance(branch, dict):
        return False
    labels = _schema_type_labels(branch)
    if not labels or any(label.strip().lower() != "null" for label in labels):
        return False
    return not any(
        key in branch
        for key in (
            "properties",
            "items",
            "anyOf",
            "any_of",
            "oneOf",
            "one_of",
            "allOf",
            "all_of",
            "enum",
            "const",
        )
    )


def _array_item_fallback(type_label: str) -> dict[str, object]:
    if type_label and not any(char.islower() for char in type_label):
        return dict(_ARRAY_ITEM_FALLBACK_UPPER)
    return dict(_ARRAY_ITEM_FALLBACK)


def _normalize_array_items(value: object, type_label: str) -> dict[str, object]:
    """Return a non-empty item schema for an array node.

    Tuple-style ``items`` lists keep their first non-empty dict entry; anything
    that cannot produce a real subschema (``None``, booleans, ``{}`` or an empty
    tuple) falls back to a ``string`` item so Gemini never sees an empty schema.
    """
    fallback = _array_item_fallback(type_label)
    if isinstance(value, dict):
        normalized = _normalize_schema_dict(value)
        return normalized if normalized else fallback
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, dict) and entry:
                normalized = _normalize_schema_dict(entry)
                if normalized:
                    return normalized
    return fallback


def _prune_schema_required(schema: dict[str, object]) -> None:
    """Restrict ``required`` to fields that survive in ``properties``."""
    properties = schema.get("properties")
    type_labels = _schema_type_labels(schema)
    is_object = any(label.strip().lower() == "object" for label in type_labels) or (
        isinstance(properties, dict)
    )
    if not is_object:
        return
    if not isinstance(properties, dict) or not properties:
        schema.pop("required", None)
        return
    valid_names = {
        name
        for name, prop in properties.items()
        if isinstance(prop, dict) or prop is True
    }
    required = schema.get("required")
    if not isinstance(required, (list, tuple, set)):
        return
    filtered: list[str] = []
    for item in required:
        if isinstance(item, str) and item in valid_names and item not in filtered:
            filtered.append(item)
    if filtered:
        schema["required"] = filtered
    else:
        schema.pop("required", None)


def _normalize_schema_node(node: object, *, keep_null: bool = False) -> object:
    if isinstance(node, list):
        return [_normalize_schema_node(item, keep_null=keep_null) for item in node]
    if isinstance(node, dict):
        return _normalize_schema_dict(node, keep_null=keep_null)
    return node


def _normalize_schema_dict(
    schema: dict[str, object], *, keep_null: bool = False
) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, value in schema.items():
        if key in _SCHEMA_OBJECT_KEYS and isinstance(value, dict):
            normalized[key] = {
                name: _normalize_schema_node(sub) for name, sub in value.items()
            }
        elif key in _SCHEMA_LIST_KEYS and isinstance(value, list):
            keep_branch_null = keep_null or key in _SCHEMA_COMBINER_KEYS
            normalized[key] = [
                _normalize_schema_node(sub, keep_null=keep_branch_null) for sub in value
            ]
        elif key in _SCHEMA_SINGLE_KEYS and isinstance(value, dict):
            normalized[key] = _normalize_schema_node(value)
        else:
            normalized[key] = value

    raw_type = normalized.get("type")
    if isinstance(raw_type, list):
        labels = [item for item in raw_type if isinstance(item, str)]
        if not any(label.strip().lower() != "null" for label in labels) and (
            not keep_null
        ):
            normalized["type"] = "string"
            normalized["nullable"] = True
    elif (
        isinstance(raw_type, str)
        and raw_type.strip().lower() == "null"
        and not keep_null
    ):
        normalized["type"] = "string"
        normalized["nullable"] = True

    for key in _SCHEMA_COMBINER_KEYS:
        branches = normalized.get(key)
        if not isinstance(branches, list) or not branches:
            continue
        kept = [branch for branch in branches if not _is_null_branch(branch)]
        if len(kept) == len(branches):
            continue
        normalized["nullable"] = True
        if kept:
            normalized[key] = kept
        else:
            normalized.pop(key, None)

    type_labels = _non_null_type_labels(normalized)
    type_label = type_labels[0] if type_labels else ""
    if any("array" in label.strip().lower() for label in type_labels) or (
        "items" in normalized
    ):
        normalized["items"] = _normalize_array_items(
            normalized.get("items"), type_label
        )

    has_type = bool(type_labels)
    has_combiner = any(
        isinstance(normalized.get(key), list) and bool(normalized.get(key))
        for key in _SCHEMA_LIST_KEYS
    )
    if not has_type and not has_combiner:
        raw_labels = _schema_type_labels(normalized)
        properties = normalized.get("properties")
        enum_values = normalized.get("enum")
        infers_type_from_structure = (
            keep_null
            and bool(raw_labels)
            and all(label.strip().lower() == "null" for label in raw_labels)
        ) or (isinstance(enum_values, (list, tuple)) and bool(enum_values))
        if not infers_type_from_structure and "const" not in normalized:
            if isinstance(properties, dict) and properties:
                normalized["type"] = "object"
            elif "items" in normalized:
                normalized["type"] = "array"
            else:
                normalized["type"] = "string"

    _prune_schema_required(normalized)
    return normalized


def normalize_tool_schema(schema: dict[str, object]) -> dict[str, object]:
    """Return a Gemini-safe copy of a tool JSON schema.

    Gemini function declarations reject arrays without real ``items`` and
    reject ``required`` entries that do not exist in ``properties``.  The
    normalization also gives empty property nodes a concrete type, resolves
    ``null`` unions in favour of the non-null branch, and keeps ``anyOf`` /
    ``oneOf`` branches valid.  It is recursive, idempotent, and never mutates
    the caller's schema.
    """
    return _normalize_schema_dict(schema)


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
