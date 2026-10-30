"""Removes stale synthetic tool parts from conversation history.

Ported from Ark ``src/atg/historyPurge.js``. Each request generates a fresh
nonce-based tool name, so any previously injected synthetic calls in history
are orphaned and must be purged before fresh injection. Contents that end up
with zero parts are dropped entirely (Gemini rejects empty parts).

Operates on the Gemini REST ``contents: list`` shape (list of
``{"role": ..., "parts": [...]}`` dicts).
"""

from __future__ import annotations


def purge_synthetic_history(contents: object, tool_prefix: str) -> object:
    """Drop functionCall/functionResponse parts whose name starts with the prefix."""
    if not isinstance(contents, list):
        return contents

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
            if (
                isinstance(fc, dict)
                and isinstance(fc.get("name"), str)
                and fc["name"].startswith(tool_prefix)
            ):
                continue
            fr = part.get("functionResponse")
            if (
                isinstance(fr, dict)
                and isinstance(fr.get("name"), str)
                and fr["name"].startswith(tool_prefix)
            ):
                continue
            parts.append(part)

        if not parts:
            continue

        kept.append({**content, "parts": parts})

    return kept


__all__ = ["purge_synthetic_history"]
