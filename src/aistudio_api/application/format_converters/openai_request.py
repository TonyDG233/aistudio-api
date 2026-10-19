"""OpenAI Chat Completions -> Gemini request conversion."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from aistudio_api.api.schemas_openai import OpenAIChatRequest
from aistudio_api.application.format_converters.common import (
    DUMMY_THOUGHT_SIGNATURE,
    ConversionError,
    decode_data_url,
    parse_arguments,
)
from aistudio_api.application.format_converters.media import MediaFetcher
from aistudio_api.application.format_converters.signature_cache import (
    signature_cache,
)

_SYSTEM_ROLES = frozenset({"system", "developer"})
_THINKING_LEVELS = frozenset({"MINIMAL", "LOW", "MEDIUM", "HIGH"})


def _tool_response(content: object, *, field: str) -> object:
    """Turn an OpenAI tool message body into a Gemini functionResponse payload.

    JSON objects / arrays are passed through unchanged; any other text is
    wrapped as ``{"result": <text>}`` so the upstream always receives a struct.
    """
    if content is None:
        return {}
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        texts: list[str] = []
        for index, part in enumerate(content):
            if not isinstance(part, dict):
                raise ConversionError(
                    "tool message content parts must be objects",
                    field=f"{field}[{index}]",
                )
            part_type = part.get("type")
            text_value = part.get("text")
            if part_type != "text" or not isinstance(text_value, str):
                raise ConversionError(
                    "tool message only supports text content parts",
                    field=f"{field}[{index}]",
                )
            texts.append(text_value)
        text = "\n".join(texts)
    else:
        raise ConversionError(
            "tool message content must be a string or text parts",
            field=field,
        )

    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        return {"result": text}
    if isinstance(parsed, (dict, list)):
        return parsed
    return {"result": parsed}


def _system_text(message: Mapping[str, object], *, field: str) -> str:
    content = message.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ConversionError(
            "system message content must be a string or text parts",
            field=f"{field}.content",
        )
    texts: list[str] = []
    for index, part in enumerate(content):
        part_type = part.get("type") if isinstance(part, dict) else None
        text = part.get("text") if isinstance(part, dict) else None
        if part_type != "text" or not isinstance(text, str):
            raise ConversionError(
                "system message only supports text content parts",
                field=f"{field}.content[{index}]",
            )
        texts.append(text)
    return "\n".join(texts)


async def _image_part(
    part: Mapping[str, object],
    *,
    field: str,
    media: MediaFetcher,
) -> dict[str, object]:
    image = part.get("image_url")
    if isinstance(image, str):
        url = image
    elif isinstance(image, dict) and isinstance(image.get("url"), str):
        url = cast("str", image["url"])
    else:
        raise ConversionError(
            "image_url content part requires image_url.url",
            field=field,
        )
    data_url = await media.to_data_url(url, field=field)
    mime_type, data = decode_data_url(data_url, field=field)
    return {"inlineData": {"mimeType": mime_type, "data": data}}


async def _content_parts(
    message: Mapping[str, object],
    *,
    field: str,
    media: MediaFetcher,
) -> list[dict[str, object]]:
    content = message.get("content")
    if content is None:
        return []
    if isinstance(content, str):
        return [{"text": content}]
    if not isinstance(content, list):
        raise ConversionError(
            "message content must be a string or an array of parts",
            field=f"{field}.content",
        )

    parts: list[dict[str, object]] = []
    for index, part in enumerate(content):
        part_field = f"{field}.content[{index}]"
        if not isinstance(part, dict):
            raise ConversionError("content part must be an object", field=part_field)
        part_type = part.get("type")
        if part_type == "text":
            text = part.get("text")
            if not isinstance(text, str):
                raise ConversionError(
                    "text content part requires a string 'text'",
                    field=part_field,
                )
            parts.append({"text": text})
        elif part_type == "image_url":
            parts.append(await _image_part(part, field=part_field, media=media))
        else:
            raise ConversionError(
                f"unsupported content part type: {part_type}",
                field=part_field,
            )
    return parts


def _tool_call_parts(
    message: Mapping[str, object],
    *,
    index: int,
    tool_names: dict[str, str],
) -> list[dict[str, object]]:
    raw_calls = message.get("tool_calls")
    if raw_calls is None:
        return []
    if not isinstance(raw_calls, list):
        raise ConversionError(
            "assistant tool_calls must be an array",
            field=f"messages[{index}].tool_calls",
        )

    parts: list[dict[str, object]] = []
    for call_index, call in enumerate(raw_calls):
        field = f"messages[{index}].tool_calls[{call_index}]"
        if not isinstance(call, dict):
            raise ConversionError("tool call must be an object", field=field)
        function = call.get("function")
        if not isinstance(function, dict):
            raise ConversionError(
                "tool call requires a function object", field=f"{field}.function"
            )
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise ConversionError(
                "tool call requires a function name",
                field=f"{field}.function.name",
            )
        args = parse_arguments(
            function.get("arguments"),
            field=f"{field}.function.arguments",
        )
        function_call: dict[str, object] = {"name": name, "args": args}
        call_id = call.get("id")
        if not isinstance(call_id, str) or not call_id:
            raise ConversionError(
                "historical tool call requires an id", field=f"{field}.id"
            )
        signature = signature_cache.get(call_id)
        function_call["id"] = call_id
        tool_names[call_id] = name
        parts.append(
            {
                "functionCall": function_call,
                "thoughtSignature": signature or DUMMY_THOUGHT_SIGNATURE,
            }
        )
    return parts


def _tool_result_parts(
    message: Mapping[str, object],
    *,
    index: int,
    tool_names: dict[str, str],
) -> list[dict[str, object]]:
    call_id = message.get("tool_call_id")
    if not isinstance(call_id, str) or not call_id:
        raise ConversionError(
            "tool message requires tool_call_id",
            field=f"messages[{index}].tool_call_id",
        )
    name = tool_names.get(call_id)
    if name is None:
        raise ConversionError(
            f"tool_call_id '{call_id}' does not match any prior assistant tool call",
            field=f"messages[{index}].tool_call_id",
        )
    response = _tool_response(
        message.get("content"), field=f"messages[{index}].content"
    )
    return [
        {
            "functionResponse": {
                "name": name,
                "response": response,
                "id": call_id,
            }
        }
    ]


def _function_declaration(
    function: Mapping[str, object], *, field: str
) -> dict[str, object]:
    name = function.get("name")
    if not isinstance(name, str) or not name:
        raise ConversionError("function name is required", field=f"{field}.name")

    declaration: dict[str, object] = {"name": name}
    description = function.get("description")
    if isinstance(description, str) and description:
        declaration["description"] = description

    parameters = function.get("parameters")
    if parameters is None:
        parameters = function.get("parametersJsonSchema")
    if parameters is not None:
        if not isinstance(parameters, (dict, list)):
            raise ConversionError(
                "function parameters must be a JSON schema object",
                field=f"{field}.parameters",
            )
        declaration["parametersJsonSchema"] = parameters
    return declaration


def _function_calling_config(choice: object) -> dict[str, object]:
    if isinstance(choice, str):
        normalized = choice.strip().lower()
        mode_by_name = {"auto": "AUTO", "none": "NONE", "required": "ANY"}
        mode = mode_by_name.get(normalized)
        if mode is None:
            raise ConversionError(
                f"unsupported tool_choice value: {choice}", field="tool_choice"
            )
        return {"mode": mode}

    if not isinstance(choice, dict):
        raise ConversionError(
            "tool_choice must be a string or object", field="tool_choice"
        )

    choice_type = choice.get("type")
    if isinstance(choice_type, str) and choice_type in {"auto", "none"}:
        return {"mode": "AUTO" if choice_type == "auto" else "NONE"}
    if choice_type == "required":
        return {"mode": "ANY"}

    function = choice.get("function")
    name = function.get("name") if isinstance(function, dict) else choice.get("name")
    if isinstance(name, str) and name:
        return {"mode": "ANY", "allowedFunctionNames": [name]}

    raise ConversionError("unsupported tool_choice object", field="tool_choice")


def _generation_config(req: OpenAIChatRequest) -> dict[str, object]:
    config: dict[str, object] = {}

    max_tokens = (
        req.max_completion_tokens
        if req.max_completion_tokens is not None
        else req.max_tokens
    )
    if max_tokens is not None:
        config["maxOutputTokens"] = max_tokens
    if req.temperature is not None:
        config["temperature"] = req.temperature
    if req.top_p is not None:
        config["topP"] = req.top_p
    if req.top_k is not None:
        config["topK"] = req.top_k

    if req.stop is not None:
        if isinstance(req.stop, str):
            sequences = [req.stop]
        else:
            sequences = [str(item) for item in req.stop]
        config["stopSequences"] = sequences

    if req.reasoning_effort is not None:
        level = req.reasoning_effort.strip().upper()
        if level not in _THINKING_LEVELS:
            raise ConversionError(
                "reasoning_effort must be one of minimal, low, medium, high",
                field="reasoning_effort",
            )
        config["thinkingConfig"] = {"thinkingLevel": level}

    response_format = req.response_format
    if response_format is not None:
        response_type = response_format.get("type")
        if response_type == "json_schema":
            raw_schema = response_format.get("json_schema")
            schema = raw_schema.get("schema") if isinstance(raw_schema, dict) else None
            if schema is None:
                schema = response_format.get("schema")
            if not isinstance(schema, (dict, list)):
                raise ConversionError(
                    "json_schema response_format requires a schema object",
                    field="response_format.json_schema.schema",
                )
            config["responseMimeType"] = "application/json"
            config["responseSchema"] = schema
        elif response_type == "json_object":
            config["responseMimeType"] = "application/json"
            config["responseSchema"] = {"type": "object", "properties": {}}
        elif response_type != "text":
            raise ConversionError(
                f"unsupported response_format.type: {response_type}",
                field="response_format.type",
            )

    return config


async def convert_openai_request(
    req: OpenAIChatRequest, media: MediaFetcher
) -> dict[str, object]:
    """Convert an OpenAI chat request into a Gemini request-shaped dict."""
    if req.n != 1:
        raise ConversionError("only n=1 is supported", field="n")

    contents: list[dict[str, object]] = []
    system_texts: list[str] = []
    tool_names: dict[str, str] = {}

    def append_content(role: str, parts: list[dict[str, object]]) -> None:
        if not parts:
            return
        if contents and contents[-1].get("role") == role:
            existing = cast("list[dict[str, object]]", contents[-1].get("parts", []))
            existing.extend(parts)
            return
        contents.append({"role": role, "parts": parts})

    for index, message in enumerate(req.messages):
        field = f"messages[{index}]"
        role = message.get("role")
        if not isinstance(role, str):
            raise ConversionError("message role is required", field=f"{field}.role")

        if role in _SYSTEM_ROLES:
            text = _system_text(message, field=field)
            if text:
                system_texts.append(text)
            continue

        if role == "tool":
            append_content(
                "user",
                _tool_result_parts(message, index=index, tool_names=tool_names),
            )
            continue

        if role == "user":
            append_content(
                "user",
                await _content_parts(message, field=field, media=media),
            )
            continue

        if role == "assistant":
            parts = await _content_parts(message, field=field, media=media)
            parts.extend(_tool_call_parts(message, index=index, tool_names=tool_names))
            append_content("model", parts)
            continue

        raise ConversionError(
            f"unsupported message role: {role}", field=f"{field}.role"
        )

    body: dict[str, object] = {"contents": contents}
    if system_texts:
        body["systemInstruction"] = {"parts": [{"text": "\n".join(system_texts)}]}

    declarations: list[dict[str, object]] = []
    for index, tool in enumerate(req.tools or []):
        function = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(function, dict):
            if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                function = tool
            else:
                raise ConversionError(
                    "tools entries must be function tools",
                    field=f"tools[{index}].function",
                )
        declarations.append(
            _function_declaration(function, field=f"tools[{index}].function")
        )

    for index, function in enumerate(req.functions or []):
        declarations.append(
            _function_declaration(function, field=f"functions[{index}]")
        )

    if declarations:
        body["tools"] = [{"functionDeclarations": declarations}]

    choice = req.tool_choice if req.tool_choice is not None else req.function_call
    if choice is not None:
        body["toolConfig"] = {"functionCallingConfig": _function_calling_config(choice)}

    generation_config = _generation_config(req)
    if generation_config:
        body["generationConfig"] = generation_config

    return body
