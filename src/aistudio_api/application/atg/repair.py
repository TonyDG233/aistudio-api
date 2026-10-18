"""Deterministic repair of synthetic tool arguments.

Ported from Ark ``src/atg/repair.js`` (itself ported from Antigravity-gateway
``internal/synthetic/repair.go``). Strategy, in order:

1. fast path: standard ``json`` parse of a ``{"content": "..."}`` envelope;
2. strip Markdown code fences and retry;
3. structural repair (unclosed strings, trailing commas, unbalanced braces);
4. bounded scanner fallback over content-ish keys.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

ARGUMENT_KEYS = ("content", "text", "answer", "response", "result", "output", "message")


@dataclass
class RepairResult:
    content: str | None = None
    error: str | None = None
    repaired: bool = False


def _parse_content_json(text: str) -> str | None:
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(obj, dict) and isinstance(obj.get("content"), str):
        return obj["content"]
    return None


def _strip_markdown_code_fences(text: str) -> str:
    out = text.strip()
    if out.startswith("```"):
        first_newline = out.find("\n")
        out = out[first_newline + 1 :] if first_newline != -1 else out.lstrip("`")
        close_idx = out.rfind("```")
        if close_idx != -1:
            out = out[:close_idx]
        out = out.strip()
    return out


def _attempt_structural_repair(text: str) -> str:
    out = text.strip()
    if not out.startswith("{"):
        idx = out.find("{")
        if idx != -1:
            out = out[idx:]
        else:
            return out

    result: list[str] = []
    in_string = False
    escaped = False
    open_braces = 0
    open_brackets = 0

    for ch in out:
        if in_string:
            if escaped:
                escaped = False
                result.append(ch)
            elif ch == "\\":
                escaped = True
                result.append(ch)
            elif ch == '"':
                in_string = False
                result.append(ch)
            elif ch == "\n":
                result.append("\\n")
            elif ch == "\r":
                result.append("\\r")
            elif ch == "\t":
                result.append("\\t")
            else:
                result.append(ch)
        else:
            if ch == '"':
                in_string = True
                result.append(ch)
            elif ch == "{":
                open_braces += 1
                result.append(ch)
            elif ch == "}":
                if open_braces > 0:
                    open_braces -= 1
                    result.append(ch)
            elif ch == "[":
                open_brackets += 1
                result.append(ch)
            elif ch == "]":
                if open_brackets > 0:
                    open_brackets -= 1
                    result.append(ch)
            else:
                result.append(ch)

    if in_string:
        result.append('"')

    repaired = "".join(result)
    repaired = repaired.rstrip(" \t\r\n,")

    while open_brackets > 0:
        repaired += "]"
        open_brackets -= 1
    while open_braces > 0:
        repaired += "}"
        open_braces -= 1

    return repaired


def _scan_and_extract_content(text: str) -> str | None:
    for key in ARGUMENT_KEYS:
        key_token = f'"{key}"'
        key_idx = text.find(key_token)
        if key_idx == -1:
            continue
        sub = text[key_idx + len(key_token) :]
        colon_idx = sub.find(":")
        if colon_idx == -1:
            continue
        sub = sub[colon_idx + 1 :].lstrip()
        if not sub.startswith('"'):
            continue

        val_buf: list[str] = []
        escaped = False
        started = False

        i = 0
        while i < len(sub):
            ch = sub[i]
            if not started:
                if ch == '"':
                    started = True
                i += 1
                continue

            if escaped:
                escaped = False
                if ch in ('"', "\\", "/"):
                    val_buf.append(ch)
                elif ch == "b":
                    val_buf.append("\b")
                elif ch == "f":
                    val_buf.append("\f")
                elif ch == "n":
                    val_buf.append("\n")
                elif ch == "r":
                    val_buf.append("\r")
                elif ch == "t":
                    val_buf.append("\t")
                elif ch == "u":
                    hex_str = sub[i + 1 : i + 5]
                    try:
                        code = int(hex_str, 16)
                    except (TypeError, ValueError):
                        val_buf.append("\\u")
                    else:
                        val_buf.append(chr(code))
                        i += 4
                else:
                    val_buf.append(ch)
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                rest = sub[i + 1 :].lstrip()
                if rest == "" or rest.startswith(("}", ",")):
                    return "".join(val_buf)
                val_buf.append('"')
            else:
                val_buf.append(ch)
            i += 1

        if val_buf:
            return "".join(val_buf)

    return None


def repair_synthetic_arguments(raw_args: object, max_bytes: int) -> RepairResult:
    """Deterministically repair a synthetic tool-call argument payload."""
    if not isinstance(raw_args, str) or len(raw_args) > max_bytes:
        return RepairResult(
            content=None, error="arguments exceed maximum allowed size", repaired=False
        )

    trimmed = raw_args.strip()
    if trimmed == "":
        return RepairResult(
            content=None, error="arguments decode failed", repaired=False
        )

    # 1. Fast path: standard json parse.
    fast = _parse_content_json(trimmed)
    if fast is not None:
        return RepairResult(content=fast, error=None, repaired=False)

    # 2. Strip Markdown code fences.
    cleaned = _strip_markdown_code_fences(trimmed)

    fenced = _parse_content_json(cleaned)
    if fenced is not None:
        return RepairResult(content=fenced, error=None, repaired=True)

    # 3. Structural repair.
    repaired = _parse_content_json(_attempt_structural_repair(cleaned))
    if repaired is not None:
        return RepairResult(content=repaired, error=None, repaired=True)

    # 4. Bounded scanner fallback for content-ish fields.
    scanned = _scan_and_extract_content(cleaned)
    if scanned is not None:
        return RepairResult(content=scanned, error=None, repaired=True)

    return RepairResult(
        content=None, error="synthetic tool arguments decode failed", repaired=False
    )


__all__ = ["ARGUMENT_KEYS", "RepairResult", "repair_synthetic_arguments"]
