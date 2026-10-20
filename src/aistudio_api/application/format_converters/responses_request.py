"""OpenAI Responses request -> Gemini request conversion."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from aistudio_api.api.schemas_openai_responses import ResponsesRequest
from aistudio_api.application.format_converters.common import (
    DUMMY_THOUGHT_SIGNATURE,
    ConversionError,
    decode_data_url,
    parse_arguments,
)
from aistudio_api.application.format_converters.media import MediaFetcher
from aistudio_api.application.format_converters.signature_cache import signature_cache

_SYSTEM_ROLES = frozenset({"system", "developer"})
_THINKING_LEVELS = frozenset({"MINIMAL", "LOW", "MEDIUM", "HIGH"})
_TEXT_PART_TYPES = frozenset({"input_text", "output_text", "text", "refusal"})


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
                f"unsupported content part type: {part_type}", field=f"{field}[{index}]"
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
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(cast("str", part["text"]))
    return "\n".join(texts)


def _function_output(output: object, *, field: str) -> object:
    if output is None:
        return {}
    if isinstance(output, str):
        try:
            parsed: object = json.loads(output)
        except json.JSONDecodeError:
            return {"result": output}
        return parsed if isinstance(parsed, (dict, list)) else {"result": parsed}
    if isinstance(output, list):
        texts: list[str] = []
        for index, part in enumerate(output):
            if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                raise ConversionError(
                    "function_call_output parts must be text",
                    field=f"{field}[{index}]",
                )
            texts.append(cast("str", part["text"]))
        return {"result": "\n".join(texts)}
    raise ConversionError(
        "function_call_output must be a string or text parts", field=field
    )


def _function_declaration(
    tool: Mapping[str, object], *, field: str
) -> dict[str, object]:
    tool_type = tool.get("type")
    if tool_type != "function":
        raise ConversionError(
            f"unsupported tool type: {tool_type} (only function tools are supported)",
            field=f"{field}.type",
        )
    name = tool.get("name")
    if not isinstance(name, str) or not name:
        raise ConversionError("function name is required", field=f"{field}.name")
    declaration: dict[str, object] = {"name": name}
    description = tool.get("description")
    if isinstance(description, str) and description:
        declaration["description"] = description
    parameters = tool.get("parameters")
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
        mode_by_name = {"auto": "AUTO", "none": "NONE", "required": "ANY"}
        mode = mode_by_name.get(normalized)
        if mode is None:
            raise ConversionError(
                f"unsupported tool_choice value: {choice}", field="tool_choice"
            )
        return {"mode": mode}
    if isinstance(choice, dict):
        choice_type = choice.get("type")
        if choice_type in {"auto", "none", "required"}:
            mode = {"auto": "AUTO", "none": "NONE", "required": "ANY"}[
                cast("str", choice_type)
            ]
            return {"mode": mode}
        name = choice.get("name")
        if choice_type == "function" and isinstance(name, str) and name:
            return {"mode": "ANY", "allowedFunctionNames": [name]}
    raise ConversionError("unsupported tool_choice object", field="tool_choice")


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

    text = req.text
    if isinstance(text, dict):
        fmt = text.get("format")
        if isinstance(fmt, dict):
            fmt_type = fmt.get("type")
            if fmt_type == "json_schema":
                schema = fmt.get("schema")
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


async def convert_responses_request(
    req: ResponsesRequest, media: MediaFetcher
) -> dict[str, object]:
    """Convert a Responses request into a Gemini request-shaped dict."""
    if req.store:
        raise ConversionError(
            "store=true is not supported by this stateless proxy", field="store"
        )
    if req.previous_response_id:
        raise ConversionError(
            "previous_response_id is not supported by this stateless proxy",
            field="previous_response_id",
        )

    contents: list[dict[str, object]] = []
    system_texts: list[str] = []
    tool_names: dict[str, str] = {}

    if req.instructions:
        system_texts.append(_text_from_content(req.instructions, field="instructions"))

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
        if item_type == "message":
            role = item.get("role")
            if not isinstance(role, str):
                raise ConversionError("message role is required", field=f"{field}.role")
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
            call_id = item.get("call_id")
            name = item.get("name")
            if not isinstance(call_id, str) or not call_id:
                raise ConversionError(
                    "function_call requires call_id", field=f"{field}.call_id"
                )
            if not isinstance(name, str) or not name:
                raise ConversionError(
                    "function_call requires name", field=f"{field}.name"
                )
            args = parse_arguments(item.get("arguments"), field=f"{field}.arguments")
            signature = signature_cache.get(call_id) or DUMMY_THOUGHT_SIGNATURE
            tool_names[call_id] = name
            append_content(
                "model",
                [
                    {
                        "functionCall": {"name": name, "args": args, "id": call_id},
                        "thoughtSignature": signature,
                    }
                ],
            )
            continue

        if item_type == "function_call_output":
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                raise ConversionError(
                    "function_call_output requires call_id",
                    field=f"{field}.call_id",
                )
            name = tool_names.get(call_id)
            if name is None:
                raise ConversionError(
                    f"call_id '{call_id}' does not match any prior function_call",
                    field=f"{field}.call_id",
                )
            append_content(
                "user",
                [
                    {
                        "functionResponse": {
                            "name": name,
                            "response": _function_output(
                                item.get("output"), field=f"{field}.output"
                            ),
                            "id": call_id,
                        }
                    }
                ],
            )
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
