"""Removes stale synthetic tool parts from conversation history.

Ported from Ark ``src/atg/historyPurge.js``. Each request generates a fresh
nonce-based tool name, so any previously injected synthetic calls in history
are orphaned and must be purged before fresh injection. Contents that end up
with zero parts are dropped entirely (Gemini rejects empty parts).

Supports both the output transport prefix (``tool_prefix``, default
``atg_emit_``) and the input-wrapper prefix (``atg_ctx_``) plus its
``"Initialize context retrieval."`` anchor turn so multi-turn replays never
nest stale wrappers.
"""

from __future__ import annotations

from typing import overload

from .config import DEFAULT_TOOL_PREFIX
from .input_wrapper import INIT_CONTEXT_TEXT, INPUT_TOOL_PREFIX


def _is_synthetic_name(name: object, prefixes: tuple[str, ...]) -> bool:
    return isinstance(name, str) and any(
        prefix and name.startswith(prefix) for prefix in prefixes
    )


def _purge_contents(
    contents: list[object],
    prefixes: tuple[str, ...],
) -> list[object]:
    kept: list[object] = []
    for content in contents:
        if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
            kept.append(content)
            continue

        parts: list[object] = []
        for part in content["parts"]:
            if not isinstance(part, dict):
                continue
            fc = part.get("functionCall")
            if isinstance(fc, dict) and _is_synthetic_name(fc.get("name"), prefixes):
                continue
            fr = part.get("functionResponse")
            if isinstance(fr, dict) and _is_synthetic_name(fr.get("name"), prefixes):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip() == INIT_CONTEXT_TEXT:
                continue
            parts.append(part)

        if not parts:
            continue

        kept.append({**content, "parts": parts})

    return kept


def _purge_input_ctx_tools(
    request_dict: dict[str, object],
    input_prefix: str = INPUT_TOOL_PREFIX,
) -> dict[str, object]:
    updated = dict(request_dict)
    raw_tools = updated.get("tools")
    if isinstance(raw_tools, list):
        cleaned_tools: list[object] = []
        for tool in raw_tools:
            if not isinstance(tool, dict):
                cleaned_tools.append(tool)
                continue
            decls = tool.get("functionDeclarations")
            if isinstance(decls, list):
                kept_decls = [
                    d
                    for d in decls
                    if not (
                        isinstance(d, dict)
                        and isinstance(d.get("name"), str)
                        and d["name"].startswith(input_prefix)
                    )
                ]
                new_tool = {**tool, "functionDeclarations": kept_decls}
                # Drop tool entry if it only had functionDeclarations and is now empty
                if not kept_decls and set(new_tool.keys()) == {"functionDeclarations"}:
                    continue
                cleaned_tools.append(new_tool)
            else:
                cleaned_tools.append(tool)
        if cleaned_tools:
            updated["tools"] = cleaned_tools
        else:
            updated.pop("tools", None)

    raw_tool_config = updated.get("toolConfig")
    if isinstance(raw_tool_config, dict):
        fcc = raw_tool_config.get("functionCallingConfig")
        if isinstance(fcc, dict):
            allowed = fcc.get("allowedFunctionNames")
            if isinstance(allowed, list):
                kept_allowed = [
                    name
                    for name in allowed
                    if not (isinstance(name, str) and name.startswith(input_prefix))
                ]
                updated["toolConfig"] = {
                    **raw_tool_config,
                    "functionCallingConfig": {
                        **fcc,
                        "allowedFunctionNames": kept_allowed,
                    },
                }
    return updated


@overload
def purge_synthetic_history(
    target: dict[str, object],
    tool_prefix: str = DEFAULT_TOOL_PREFIX,
) -> dict[str, object]: ...


@overload
def purge_synthetic_history(
    target: list[object],
    tool_prefix: str = DEFAULT_TOOL_PREFIX,
) -> list[object]: ...


def purge_synthetic_history(
    target: object,
    tool_prefix: str = DEFAULT_TOOL_PREFIX,
) -> object:
    """Drop stale synthetic ``functionCall``/``functionResponse`` parts and anchors.

    Accepts either a ``contents`` list or a full Gemini ``request_dict``.
    Purges both ``tool_prefix`` (e.g. ``atg_emit_``) and ``atg_ctx_`` parts,
    as well as the ``"Initialize context retrieval."`` input-wrapper anchor.
    """
    prefixes = (tool_prefix, INPUT_TOOL_PREFIX)
    if isinstance(target, list):
        return _purge_contents(target, prefixes)
    if isinstance(target, dict):
        cleaned = _purge_input_ctx_tools(target)
        contents = cleaned.get("contents")
        if isinstance(contents, list):
            cleaned["contents"] = _purge_contents(contents, prefixes)
        return cleaned
    return target


__all__ = ["purge_synthetic_history"]
