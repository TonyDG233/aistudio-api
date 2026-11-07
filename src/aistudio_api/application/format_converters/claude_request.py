"""Claude Messages -> Gemini request conversion.

The converter is deliberately lenient about replayed tool history (PG-08): a
``tool_result`` that references an unknown ``tool_use_id`` must never become a
client 400.  It resolves the name from context, then from an explicit ``name``
field, and finally falls back to a deterministic ``unknown_tool`` label.  Every
``functionCall.args`` / ``functionResponse.response`` payload is coerced to a
JSON object so PG-04 always sees a Struct.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import cast

from aistudio_api.application.contracts.claude import (
    ClaudeCountTokensRequest,
    ClaudeMessagesRequest,
)
from aistudio_api.application.format_converters.common import (
    DUMMY_THOUGHT_SIGNATURE,
    ConversionError,
    decode_data_url,
)
from aistudio_api.application.format_converters.media import MediaFetcher
from aistudio_api.application.signature_cache import signature_cache
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("format.claude")

_SUPPORTED_BLOCK_TYPES = frozenset(
    {"text", "image", "tool_use", "tool_result", "thinking"}
)
_THINKING_TYPES = frozenset({"enabled", "adaptive", "between_tools"})
_UNKNOWN_TOOL_NAME = "unknown_tool"


def _require_str(value: object, *, field: str, what: str) -> str:
    if not isinstance(value, str):
        raise ConversionError(f"{what} must be a string", field=field)
    return value


def _thinking_level(budget: object, *, field: str) -> str:
    if isinstance(budget, bool) or not isinstance(budget, (int, float)):
        raise ConversionError("budget_tokens must be an integer", field=field)
    if budget < 2000:
        return "LOW"
    if budget < 8000:
        return "MEDIUM"
    return "HIGH"


def _system_instruction(system: object) -> dict[str, object] | None:
    if system is None:
        return None
    if isinstance(system, str):
        return {"parts": [{"text": system}]} if system else None
    if not isinstance(system, list):
        raise ConversionError(
            "system must be a string or a list of blocks", field="system"
        )
    texts: list[str] = []
    for index, block in enumerate(system):
        if not isinstance(block, dict) or block.get("type") != "text":
            raise ConversionError("system only supports text blocks", field="system")
        texts.append(
            _require_str(
                block.get("text"),
                field=f"system[{index}].text",
                what="system text",
            )
        )
    text = "\n".join(texts)
    return {"parts": [{"text": text}]} if text else None


async def _image_part(
    source: object, media: MediaFetcher, *, field: str
) -> dict[str, object]:
    if not isinstance(source, dict):
        raise ConversionError("image source must be an object", field=field)
    source_type = source.get("type")
    if source_type == "base64":
        media_type = _require_str(
            source.get("media_type"),
            field=f"{field}.source.media_type",
            what="image media_type",
        )
        data = _require_str(
            source.get("data"), field=f"{field}.source.data", what="image data"
        )
        mime_type, payload = decode_data_url(
            f"data:{media_type};base64,{data}", field=field
        )
        return {"inlineData": {"mimeType": mime_type, "data": payload}}
    if source_type == "url":
        url = _require_str(
            source.get("url"), field=f"{field}.source.url", what="image URL"
        )
        data_url = await media.to_data_url(url, field=field)
        mime_type, payload = decode_data_url(data_url, field=field)
        return {"inlineData": {"mimeType": mime_type, "data": payload}}
    raise ConversionError(
        f"unsupported image source type: {source_type!r}", field=field
    )


def _tool_input(value: object) -> dict[str, object]:
    """Coerce a Claude ``tool_use.input`` payload into a JSON object (PG-04)."""
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


async def _tool_result_response(
    content: object,
    *,
    is_error: bool,
    media: MediaFetcher,
    field: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Return a Struct response dict plus any image parts (PG-04)."""
    if content is None:
        return {}, []
    if isinstance(content, dict):
        return cast("dict[str, object]", content), []
    if isinstance(content, str):
        key = "error" if is_error else "result"
        return {key: content}, []
    if not isinstance(content, list):
        raise ConversionError(
            "tool_result content must be a string, object or a list of blocks",
            field=field,
        )

    texts: list[str] = []
    parts: list[dict[str, object]] = []
    for index, block in enumerate(content):
        item_field = f"{field}.content[{index}]"
        if not isinstance(block, dict):
            raise ConversionError("content block must be an object", field=item_field)
        block_type = block.get("type")
        if block_type == "text":
            texts.append(
                _require_str(block.get("text"), field=f"{item_field}.text", what="text")
            )
        elif block_type == "image":
            parts.append(
                await _image_part(block.get("source"), media, field=item_field)
            )
        else:
            raise ConversionError(
                f"unsupported tool_result block type: {block_type!r}",
                field=item_field,
            )
    key = "error" if is_error else "result"
    return {key: "\n".join(texts)}, parts


@dataclass
class _ToolHistory:
    """Lenient tool-use/tool-result reconciliation for replayed history."""

    names: dict[str, str] = field(default_factory=dict)
    pending: list[tuple[str, str]] = field(default_factory=list)
    used_ids: set[str] = field(default_factory=set)
    synthetic_seq: int = 0

    def synthesize_id(self, message_index: int, call_index: int) -> str:
        base = f"toolu_auto_{message_index}_{call_index}"
        candidate = base
        suffix = 0
        while candidate in self.used_ids:
            suffix += 1
            candidate = f"{base}_{suffix}"
        return candidate

    def resolve_result_name(
        self, tool_id: str | None, declared_name: str | None
    ) -> str | None:
        if tool_id is not None and tool_id in self.names:
            name = self.names[tool_id]
            self.pending = [item for item in self.pending if item[0] != tool_id]
            return name
        if declared_name:
            for position, (_pending_id, pending_name) in enumerate(self.pending):
                if pending_name == declared_name:
                    self.pending.pop(position)
                    return pending_name
            return declared_name
        if self.pending:
            _pending_id, pending_name = self.pending.pop(0)
            return pending_name
        return None


def _collect_tool_names(messages: list[dict[str, object]]) -> dict[str, str]:
    """Pre-scan the whole history so replayed results can resolve any call id."""
    names: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            raw_id = block.get("id")
            raw_name = block.get("name")
            if (
                isinstance(raw_id, str)
                and raw_id.strip()
                and isinstance(raw_name, str)
                and raw_name.strip()
            ):
                names[raw_id.strip()] = raw_name.strip()
    return names


def _merge_content(
    contents: list[dict[str, object]],
    role: str,
    parts: list[dict[str, object]],
) -> None:
    if not parts:
        return
    if contents and contents[-1].get("role") == role:
        existing = contents[-1].get("parts")
        if isinstance(existing, list):
            existing.extend(parts)
            return
    contents.append({"role": role, "parts": parts})


def _claude_role_to_gemini(role: object, *, field: str) -> str:
    if role == "user":
        return "user"
    if role == "assistant":
        return "model"
    raise ConversionError(f"unsupported message role: {role!r}", field=field)


def _tool_use_part(
    block: dict[str, object],
    *,
    index: int,
    call_index: int,
    history: _ToolHistory,
) -> dict[str, object]:
    raw_id = block.get("id")
    tool_id = raw_id.strip() if isinstance(raw_id, str) else ""
    if not tool_id or tool_id in history.used_ids:
        tool_id = history.synthesize_id(index, call_index)
        logger.warning(
            "[pg08] lenient tool history reason=tool-use-id-%s message=%d call=%d",
            "duplicate" if raw_id else "missing",
            index,
            call_index,
        )
    raw_name = block.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    if not name:
        name = _UNKNOWN_TOOL_NAME
        logger.warning(
            "[pg08] lenient tool history reason=tool-use-name-missing message=%d",
            index,
        )
    history.used_ids.add(tool_id)
    history.names[tool_id] = name
    history.pending.append((tool_id, name))
    signature = signature_cache.get(tool_id)
    return {
        "functionCall": {
            "id": tool_id,
            "name": name,
            "args": _tool_input(block.get("input")),
        },
        "thoughtSignature": signature or DUMMY_THOUGHT_SIGNATURE,
    }


async def _tool_result_part(
    block: dict[str, object],
    *,
    index: int,
    field: str,
    history: _ToolHistory,
    media: MediaFetcher,
) -> dict[str, object]:
    raw_id = block.get("tool_use_id")
    tool_id = raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else None
    raw_name = block.get("name")
    declared_name = (
        raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
    )
    name = history.resolve_result_name(tool_id, declared_name)
    if name is None:
        name = _UNKNOWN_TOOL_NAME
        logger.warning(
            "[pg08] lenient tool history reason=unknown-tool-use-id message=%d "
            "tool_use_id=%s",
            index,
            tool_id or "(missing)",
        )
    if tool_id is None:
        history.synthetic_seq += 1
        tool_id = f"toolu_output_{index}_{history.synthetic_seq}"
        logger.warning(
            "[pg08] lenient tool history reason=missing-tool-use-id message=%d",
            index,
        )
    response, parts = await _tool_result_response(
        block.get("content"),
        is_error=bool(block.get("is_error")),
        media=media,
        field=field,
    )
    function_response: dict[str, object] = {
        "id": tool_id,
        "name": name,
        "response": response,
    }
    if parts:
        function_response["parts"] = parts
    return {"functionResponse": function_response}


async def _convert_block(
    block: object,
    media: MediaFetcher,
    *,
    index: int,
    block_index: int,
    field: str,
    history: _ToolHistory,
) -> dict[str, object] | None:
    if not isinstance(block, dict):
        raise ConversionError("content block must be an object", field=field)
    block_type = block.get("type")
    if block_type not in _SUPPORTED_BLOCK_TYPES:
        raise ConversionError(
            f"unsupported content block type: {block_type!r}", field=field
        )

    if block_type == "text":
        return {
            "text": _require_str(block.get("text"), field=f"{field}.text", what="text")
        }

    if block_type == "image":
        return await _image_part(block.get("source"), media, field=field)

    if block_type == "thinking":
        return {
            "text": _require_str(
                block.get("thinking"),
                field=f"{field}.thinking",
                what="thinking",
            ),
            "thought": True,
        }

    if block_type == "tool_use":
        return _tool_use_part(
            block,
            index=index,
            call_index=block_index,
            history=history,
        )

    return await _tool_result_part(
        block,
        index=index,
        field=field,
        history=history,
        media=media,
    )


async def _convert_messages(
    messages: list[dict[str, object]], media: MediaFetcher
) -> list[dict[str, object]]:
    contents: list[dict[str, object]] = []
    history = _ToolHistory(names=_collect_tool_names(messages))
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ConversionError(
                "message must be an object", field=f"messages[{index}]"
            )
        role = _claude_role_to_gemini(
            message.get("role"), field=f"messages[{index}].role"
        )
        content = message.get("content")
        parts: list[dict[str, object]] = []
        if isinstance(content, str):
            parts.append({"text": content})
        elif isinstance(content, list):
            for block_index, block in enumerate(content):
                converted = await _convert_block(
                    block,
                    media,
                    index=index,
                    block_index=block_index,
                    field=f"messages[{index}].content[{block_index}]",
                    history=history,
                )
                if converted is not None:
                    parts.append(converted)
        else:
            raise ConversionError(
                "message content must be a string or a list of blocks",
                field=f"messages[{index}].content",
            )
        _merge_content(contents, role, parts)
    return contents


def _convert_tools(
    tools: list[dict[str, object]] | None,
) -> list[dict[str, object]] | None:
    if tools is None:
        return None
    declarations: list[dict[str, object]] = []
    for index, tool in enumerate(tools):
        field = f"tools[{index}]"
        if not isinstance(tool, dict):
            raise ConversionError("tool must be an object", field=field)
        tool_type = tool.get("type")
        if tool_type not in (None, "custom"):
            raise ConversionError(
                f"built-in tool type is not supported: {tool_type!r}", field=field
            )
        name = _require_str(tool.get("name"), field=f"{field}.name", what="tool name")
        schema = tool.get("input_schema")
        if not isinstance(schema, dict):
            raise ConversionError(
                "input_schema must be an object", field=f"{field}.input_schema"
            )
        declaration: dict[str, object] = {
            "name": name,
            "parametersJsonSchema": schema,
        }
        description = tool.get("description")
        if isinstance(description, str) and description:
            declaration["description"] = description
        declarations.append(declaration)
    return [{"functionDeclarations": declarations}]


def _convert_tool_choice(
    tool_choice: dict[str, object] | None,
) -> dict[str, object] | None:
    if tool_choice is None:
        return None
    if tool_choice.get("disable_parallel_tool_use"):
        logger.debug(
            "Claude disable_parallel_tool_use ignored: upstream cannot enforce it"
        )
    choice_type = tool_choice.get("type")
    if choice_type == "auto":
        mode = "AUTO"
    elif choice_type == "none":
        mode = "NONE"
    elif choice_type == "any":
        mode = "ANY"
    elif choice_type == "tool":
        name = _require_str(
            tool_choice.get("name"),
            field="tool_choice.name",
            what="tool_choice name",
        )
        return {
            "functionCallingConfig": {
                "mode": "ANY",
                "allowedFunctionNames": [name],
            }
        }
    else:
        raise ConversionError(
            f"unsupported tool_choice type: {choice_type!r}",
            field="tool_choice.type",
        )
    return {"functionCallingConfig": {"mode": mode}}


def _convert_thinking(
    thinking: dict[str, object] | None,
) -> dict[str, object] | None:
    if thinking is None:
        return None
    thinking_type = thinking.get("type")
    if thinking_type == "disabled":
        # The upstream always enables thinking; only the level is adjustable.
        return None
    if thinking_type not in _THINKING_TYPES:
        raise ConversionError(
            f"unsupported thinking type: {thinking_type!r}", field="thinking.type"
        )
    budget = thinking.get("budget_tokens", 0)
    return {"thinkingLevel": _thinking_level(budget, field="thinking.budget_tokens")}


def _output_constraints(
    request: ClaudeMessagesRequest,
) -> tuple[str, dict[str, object]] | None:
    output_format = request.output_format
    if output_format is None and request.output_config is not None:
        nested = request.output_config.get("format")
        if isinstance(nested, dict):
            output_format = nested
    if output_format is None:
        return None
    format_type = output_format.get("type")
    if format_type == "text":
        return None
    if format_type == "json_object":
        return "application/json", {"type": "object", "properties": {}}
    if format_type == "json_schema":
        schema = output_format.get("schema")
        if schema is None:
            wrapped = output_format.get("json_schema")
            if isinstance(wrapped, dict):
                schema = wrapped.get("schema")
        if not isinstance(schema, dict):
            raise ConversionError(
                "json_schema output requires an object schema",
                field="output_format.schema",
            )
        return "application/json", cast("dict[str, object]", schema)
    raise ConversionError(
        f"unsupported output format type: {format_type!r}",
        field="output_format.type",
    )


def _log_ignored_seed(req: ClaudeMessagesRequest) -> None:
    """E-04: accept ``seed`` but do not forward it to Gemini."""
    seed = getattr(req, "seed", None)
    if seed is None:
        return
    logger.warning(
        "[E-04] Claude seed=%r accepted but ignored; "
        "Gemini generationConfig.seed is not set",
        seed,
    )


async def convert_claude_request(
    req: ClaudeMessagesRequest, media: MediaFetcher
) -> dict[str, object]:
    """Convert a Claude Messages payload into a Gemini request dictionary."""
    _log_ignored_seed(req)
    body: dict[str, object] = {"contents": await _convert_messages(req.messages, media)}

    system_instruction = _system_instruction(req.system)
    if system_instruction is not None:
        body["systemInstruction"] = system_instruction

    tools = _convert_tools(req.tools)
    if tools is not None:
        body["tools"] = tools

    tool_choice = _convert_tool_choice(req.tool_choice)
    if tool_choice is not None:
        body["toolConfig"] = tool_choice

    generation_config: dict[str, object] = {"maxOutputTokens": req.max_tokens}
    if req.temperature is not None:
        generation_config["temperature"] = req.temperature
    if req.top_p is not None:
        generation_config["topP"] = req.top_p
    if req.top_k is not None:
        generation_config["topK"] = req.top_k
    if req.stop_sequences is not None:
        generation_config["stopSequences"] = req.stop_sequences
    thinking_config = _convert_thinking(req.thinking)
    if thinking_config is not None:
        generation_config["thinkingConfig"] = thinking_config
    output = _output_constraints(req)
    if output is not None:
        mime_type, schema = output
        generation_config["responseMimeType"] = mime_type
        generation_config["responseSchema"] = schema
    body["generationConfig"] = generation_config

    return body


async def convert_claude_count_request(
    req: ClaudeCountTokensRequest, media: MediaFetcher
) -> dict[str, object]:
    """Convert a Claude count_tokens payload into a prompt-only Gemini body.

    Generation config and tool-choice config do not contribute tokens, so they
    are stripped before the request reaches the upstream counter.
    """
    messages_request = ClaudeMessagesRequest(
        model=req.model,
        messages=req.messages,
        system=req.system,
        max_tokens=req.max_tokens if req.max_tokens is not None else 1,
        tools=req.tools,
        tool_choice=req.tool_choice,
        thinking=req.thinking,
    )
    body = await convert_claude_request(messages_request, media)
    body.pop("generationConfig", None)
    body.pop("toolConfig", None)
    return body


__all__ = ["convert_claude_count_request", "convert_claude_request"]
