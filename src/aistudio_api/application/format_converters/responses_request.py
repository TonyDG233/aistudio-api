"""OpenAI Responses request -> Gemini request conversion.

The converter is stateless and lenient by design:

* ``previous_response_id`` (when non-empty) and ``item_reference`` are the only
  stateful inputs that fail, because they cannot be resolved without a server
  side store.
* ``store``/``parallel_tool_calls``/``truncation``/``include``/``metadata``/
  ``user``/``service_tier``/``reasoning.summary``/``seed`` are accepted and
  ignored (seed is Gemini-only, E-04).
* Historical tool calls without signatures fall back to ``signature_cache`` and
  then ``DUMMY_THOUGHT_SIGNATURE`` (PG-01).
* Tool arguments and function outputs are always emitted as Gemini Structs
  (dicts), and an unresolved ``function_call_output.call_id`` is mapped from
  context or a deterministic fallback name instead of being rejected (PG-08).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from aistudio_api.application.contracts.responses import ResponsesRequest
from aistudio_api.application.format_converters.common import (
    DUMMY_THOUGHT_SIGNATURE,
    ConversionError,
    decode_data_url,
)
from aistudio_api.application.format_converters.media import MediaFetcher
from aistudio_api.application.signature_cache import signature_cache
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("format.responses")

_SYSTEM_ROLES = frozenset({"system", "developer"})
_THINKING_LEVELS = frozenset({"MINIMAL", "LOW", "MEDIUM", "HIGH"})
_TEXT_PART_TYPES = frozenset({"input_text", "output_text", "text", "refusal"})
_UNKNOWN_TOOL_NAME = "unknown_tool"
_LENIENT_TOOL_NAME = "unknown_tool"


def _warn_ignored(field: str) -> None:
    logger.warning("[responses] ignored unsupported field: %s", field)


def _debug_ignored(field: str) -> None:
    logger.debug("[responses] ignored unsupported field: %s", field)


def _text_from_content(content: object, *, field: str) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ConversionError(
            "content must be a string or an array of parts", field=field
        )
    texts: list[str] = []
    for index, part in enumerate(content):
        if not isinstance(part, dict):
            raise ConversionError(
                "content part must be an object", field=f"{field}[{index}]"
            )
        part_type = part.get("type")
        if part_type in _TEXT_PART_TYPES:
            text = part.get("text")
            if not isinstance(text, str):
                raise ConversionError(
                    f"{part_type} part requires a string 'text'",
                    field=f"{field}[{index}]",
                )
            texts.append(text)
        else:
            raise ConversionError(
                f"unsupported content part type: {part_type}",
                field=f"{field}[{index}]",
            )
    return "\n".join(texts)


async def _content_parts(
    content: object,
    *,
    field: str,
    media: MediaFetcher,
) -> list[dict[str, object]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"text": content}]
    if not isinstance(content, list):
        raise ConversionError(
            "content must be a string or an array of parts", field=field
        )

    parts: list[dict[str, object]] = []
    for index, part in enumerate(content):
        part_field = f"{field}[{index}]"
        if not isinstance(part, dict):
            raise ConversionError("content part must be an object", field=part_field)
        part_type = part.get("type")
        if part_type in _TEXT_PART_TYPES:
            text = part.get("text")
            if not isinstance(text, str):
                raise ConversionError(
                    f"{part_type} part requires a string 'text'", field=part_field
                )
            parts.append({"text": text})
        elif part_type == "input_image":
            image_url = part.get("image_url")
            if isinstance(image_url, dict):
                image_url = image_url.get("url")
            if not isinstance(image_url, str) or not image_url:
                raise ConversionError(
                    "input_image requires image_url (file_id is not supported)",
                    field=f"{part_field}.image_url",
                )
            data_url = await media.to_data_url(image_url, field=part_field)
            mime_type, data = decode_data_url(data_url, field=part_field)
            parts.append({"inlineData": {"mimeType": mime_type, "data": data}})
        elif part_type == "input_file":
            raise ConversionError(
                "input_file is not supported; send image data inline",
                field=part_field,
            )
        else:
            raise ConversionError(
                f"unsupported content part type: {part_type}", field=part_field
            )
    return parts


def _reasoning_text(item: Mapping[str, object]) -> str:
    texts: list[str] = []
    for key in ("summary", "content"):
        value = item.get(key)
        if isinstance(value, list):
            for part in value:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        texts.append(text)
    return "\n".join(texts)


def _lenient_arguments(value: object) -> dict[str, object]:
    """Decode historical call arguments into a Gemini Struct.

    Valid JSON objects pass through; scalars, arrays and non-JSON text are
    wrapped under ``content`` so the upstream always receives an object
    (PG-04) without rejecting a replayed conversation (PG-08).
    """
    if isinstance(value, dict):
        return cast("dict[str, object]", value)
    if value is None:
        return {}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed: object = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return {"content": value}
        if isinstance(parsed, dict):
            return cast("dict[str, object]", parsed)
        return {"content": parsed}
    return {"content": value}


def _function_output(output: object) -> dict[str, object]:
    """Normalise a function_call_output payload into a Gemini Struct (PG-04)."""
    if output is None:
        return {}
    if isinstance(output, dict):
        return cast("dict[str, object]", output)
    if isinstance(output, str):
        try:
            parsed: object = json.loads(output)
        except (json.JSONDecodeError, ValueError):
            return {"result": output}
        if isinstance(parsed, dict):
            return cast("dict[str, object]", parsed)
        return {"result": parsed}
    if isinstance(output, list):
        texts: list[str] = []
        all_text = True
        for part in output:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(cast("str", part["text"]))
            elif isinstance(part, str):
                texts.append(part)
            else:
                all_text = False
                break
        if all_text:
            return {"result": "\n".join(texts)}
        return {"result": output}
    return {"result": output}


def _function_declaration(
    tool: Mapping[str, object], *, field: str
) -> dict[str, object]:
    tool_type = tool.get("type")
    if tool_type != "function":
        raise ConversionError(
            f"unsupported tool type: {tool_type} (only function tools are supported)",
            field=f"{field}.type",
        )
    nested = tool.get("function")
    source = nested if isinstance(nested, dict) else tool
    name = source.get("name")
    if not isinstance(name, str) or not name:
        raise ConversionError("function name is required", field=f"{field}.name")

    declaration: dict[str, object] = {"name": name}
    description = source.get("description")
    if isinstance(description, str) and description:
        declaration["description"] = description

    parameters = source.get("parameters")
    if parameters is None:
        parameters = source.get("parametersJsonSchema")
    if parameters is not None:
        if not isinstance(parameters, (dict, list)):
            raise ConversionError(
                "function parameters must be a JSON schema object",
                field=f"{field}.parameters",
            )
        declaration["parametersJsonSchema"] = parameters
    return declaration


def _tool_choice(choice: object) -> dict[str, object] | None:
    if choice is None:
        return None
    if isinstance(choice, str):
        normalized = choice.strip().lower()
        mode = {"auto": "AUTO", "none": "NONE", "required": "ANY"}.get(normalized)
        if mode is None:
            raise ConversionError(
                f"unsupported tool_choice value: {choice}", field="tool_choice"
            )
        return {"mode": mode}
    if isinstance(choice, dict):
        choice_type = choice.get("type")
        if isinstance(choice_type, str) and choice_type in {
            "auto",
            "none",
            "required",
        }:
            mode = {"auto": "AUTO", "none": "NONE", "required": "ANY"}[choice_type]
            return {"mode": mode}
        nested = choice.get("function")
        name = nested.get("name") if isinstance(nested, dict) else choice.get("name")
        if choice_type == "function" and isinstance(name, str) and name:
            return {"mode": "ANY", "allowedFunctionNames": [name]}
    raise ConversionError("unsupported tool_choice object", field="tool_choice")


def _text_format_config(text: Mapping[str, object]) -> dict[str, object]:
    config: dict[str, object] = {}
    fmt = text.get("format")
    if not isinstance(fmt, dict):
        return config
    fmt_type = fmt.get("type")
    if fmt_type == "json_schema":
        schema = fmt.get("schema")
        if schema is None:
            nested = fmt.get("json_schema")
            schema = nested.get("schema") if isinstance(nested, dict) else None
        if not isinstance(schema, (dict, list)):
            raise ConversionError(
                "text.format.schema must be a JSON schema object",
                field="text.format.schema",
            )
        config["responseMimeType"] = "application/json"
        config["responseSchema"] = schema
    elif fmt_type == "json_object":
        config["responseMimeType"] = "application/json"
        config["responseSchema"] = {"type": "object", "properties": {}}
    elif fmt_type not in {None, "text"}:
        raise ConversionError(
            f"unsupported text.format.type: {fmt_type}",
            field="text.format.type",
        )
    return config


def _generation_config(req: ResponsesRequest) -> dict[str, object]:
    config: dict[str, object] = {}
    if req.max_output_tokens is not None:
        config["maxOutputTokens"] = req.max_output_tokens
    if req.temperature is not None:
        config["temperature"] = req.temperature
    if req.top_p is not None:
        config["topP"] = req.top_p

    reasoning = req.reasoning
    if isinstance(reasoning, dict):
        effort = reasoning.get("effort")
        if isinstance(effort, str):
            level = effort.strip().upper()
            if level not in _THINKING_LEVELS:
                raise ConversionError(
                    "reasoning.effort must be minimal, low, medium or high",
                    field="reasoning.effort",
                )
            config["thinkingConfig"] = {"thinkingLevel": level}
        if reasoning.get("summary") is not None:
            _debug_ignored("reasoning.summary")

    if req.text is not None:
        config.update(_text_format_config(req.text))

    if req.seed is not None:
        # E-04: seed is Gemini-only; Responses must accept and ignore it.
        _warn_ignored("seed")
    return config


def _warn_lenient(reason: str, *, index: int, **fields: object) -> None:
    detail = " ".join(f"{key}={value}" for key, value in sorted(fields.items()))
    logger.warning(
        "[pg08] lenient responses history reason=%s item=%d%s",
        reason,
        index,
        f" {detail}" if detail else "",
    )


def _match_tool_name(
    names: dict[str, str],
    pending: list[tuple[str, str]],
    *,
    call_id: str,
    declared_name: str | None,
) -> tuple[str, str]:
    """Resolve a function output name without ever rejecting the request."""
    existing = names.get(call_id)
    if existing is not None:
        pending[:] = [item for item in pending if item[0] != call_id]
        return call_id, existing
    if declared_name:
        for position, (_pending_id, pending_name) in enumerate(pending):
            if pending_name == declared_name:
                pending.pop(position)
                names[call_id] = pending_name
                return call_id, pending_name
        names[call_id] = declared_name
        return call_id, declared_name
    if len(pending) == 1:
        _pending_id, pending_name = pending.pop()
        names[call_id] = pending_name
        return call_id, pending_name
    names[call_id] = _LENIENT_TOOL_NAME
    return call_id, _LENIENT_TOOL_NAME


def _optional_str(item: Mapping[str, object], key: str) -> str | None:
    value = item.get(key)
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _message_role(item: Mapping[str, object], *, field: str) -> str:
    role = item.get("role")
    if not isinstance(role, str):
        raise ConversionError("message role is required", field=f"{field}.role")
    return role


def _function_call_id(
    item: Mapping[str, object], *, index: int, call_index: int
) -> str:
    raw = _optional_str(item, "call_id") or _optional_str(item, "id")
    if raw is not None:
        return raw
    _warn_lenient("function-call-id-missing", index=index, call=call_index)
    return f"call_auto_{index}_{call_index}"


def _function_call_part(
    item: Mapping[str, object],
    *,
    index: int,
    call_index: int,
) -> tuple[str, str, dict[str, object]]:
    call_id = _function_call_id(item, index=index, call_index=call_index)
    name = _optional_str(item, "name") or _UNKNOWN_TOOL_NAME
    if name == _UNKNOWN_TOOL_NAME:
        _warn_lenient("function-call-name-missing", index=index, call=call_index)
    args = _lenient_arguments(item.get("arguments"))
    explicit = _optional_str(item, "thought_signature") or _optional_str(
        item, "thoughtSignature"
    )
    signature = explicit or signature_cache.get(call_id) or DUMMY_THOUGHT_SIGNATURE
    function_call = {"name": name, "args": args, "id": call_id}
    part: dict[str, object] = {
        "functionCall": function_call,
        "thoughtSignature": signature,
    }
    return call_id, name, part


def _function_output_part(
    item: Mapping[str, object],
    *,
    index: int,
    names: dict[str, str],
    pending: list[tuple[str, str]],
) -> dict[str, object]:
    call_id = _optional_str(item, "call_id") or _optional_str(item, "id")
    if call_id is None:
        _warn_lenient("function-output-id-missing", index=index)
        call_id = f"call_output_{index}"
    declared_name = _optional_str(item, "name")
    resolved_id, name = _match_tool_name(
        names, pending, call_id=call_id, declared_name=declared_name
    )
    if name == _LENIENT_TOOL_NAME:
        _warn_lenient("unresolved-function-output", index=index)
    response = _function_output(item.get("output"))
    return {
        "functionResponse": {
            "name": name,
            "response": response,
            "id": resolved_id,
        }
    }


async def convert_responses_request(
    req: ResponsesRequest, media: MediaFetcher
) -> dict[str, object]:
    """Convert a Responses request into a Gemini request-shaped dict."""
    previous = (req.previous_response_id or "").strip()
    if previous:
        raise ConversionError(
            "previous_response_id requires server-side state and is not supported "
            "by this stateless proxy",
            field="previous_response_id",
        )
    if req.store is not None:
        _debug_ignored("store")
    provided = req.model_fields_set
    for field in (
        "parallel_tool_calls",
        "truncation",
        "include",
        "metadata",
        "user",
        "service_tier",
    ):
        if field in provided:
            _debug_ignored(field)

    contents: list[dict[str, object]] = []
    system_texts: list[str] = []
    names: dict[str, str] = {}
    pending: list[tuple[str, str]] = []

    if req.instructions:
        text = _text_from_content(req.instructions, field="instructions")
        if text:
            system_texts.append(text)

    def append_content(role: str, parts: list[dict[str, object]]) -> None:
        if not parts:
            return
        if contents and contents[-1].get("role") == role:
            existing = cast("list[dict[str, object]]", contents[-1].get("parts", []))
            existing.extend(parts)
            return
        contents.append({"role": role, "parts": parts})

    if isinstance(req.input, str):
        append_content("user", [{"text": req.input}])
        items: list[dict[str, object]] = []
    else:
        items = req.input

    for index, item in enumerate(items):
        field = f"input[{index}]"
        item_type = item.get("type")
        if item_type is None and item.get("role") is not None:
            # EasyInputMessage: ``{role, content}`` without a ``type`` field.
            item_type = "message"
        if item_type == "message":
            role = _message_role(item, field=field)
            if role in _SYSTEM_ROLES:
                text = _text_from_content(item.get("content"), field=f"{field}.content")
                if text:
                    system_texts.append(text)
                continue
            if role not in {"user", "assistant"}:
                raise ConversionError(
                    f"unsupported message role: {role}", field=f"{field}.role"
                )
            parts = await _content_parts(
                item.get("content"), field=f"{field}.content", media=media
            )
            append_content("user" if role == "user" else "model", parts)
            continue

        if item_type == "function_call":
            call_id, name, part = _function_call_part(item, index=index, call_index=0)
            names[call_id] = name
            pending.append((call_id, name))
            append_content("model", [part])
            continue

        if item_type == "function_call_output":
            part = _function_output_part(
                item, index=index, names=names, pending=pending
            )
            append_content("user", [part])
            continue

        if item_type == "reasoning":
            text = _reasoning_text(item)
            if text:
                append_content("model", [{"text": text, "thought": True}])
            continue

        if item_type == "item_reference":
            raise ConversionError(
                "item_reference requires server-side state and is not supported",
                field=f"{field}.type",
            )
        raise ConversionError(
            f"unsupported input item type: {item_type}", field=f"{field}.type"
        )

    body: dict[str, object] = {"contents": contents}
    if system_texts:
        body["systemInstruction"] = {"parts": [{"text": "\n".join(system_texts)}]}

    declarations = [
        _function_declaration(tool, field=f"tools[{index}]")
        for index, tool in enumerate(req.tools or [])
    ]
    if declarations:
        body["tools"] = [{"functionDeclarations": declarations}]

    choice = _tool_choice(req.tool_choice)
    if choice is not None:
        body["toolConfig"] = {"functionCallingConfig": choice}

    config = _generation_config(req)
    if config:
        body["generationConfig"] = config
    return body


__all__ = ["convert_responses_request"]
