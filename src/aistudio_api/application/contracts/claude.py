"""Anthropic Messages HTTP contracts.

Only the fields the converter consumes are constrained. Unknown fields are kept
so newer Anthropic convenience fields (for example ``seed``) do not fail
validation; unsupported semantics are handled where they matter.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ClaudeMessagesRequest(BaseModel):
    """Client payload for ``POST /v1/messages``."""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[dict[str, object]]
    system: str | list[dict[str, object]] | None = None
    max_tokens: int
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    stop_sequences: list[str] | None = None
    tools: list[dict[str, object]] | None = None
    tool_choice: dict[str, object] | None = None
    thinking: dict[str, object] | None = None
    metadata: dict[str, object] | None = None
    output_format: dict[str, object] | None = None
    output_config: dict[str, object] | None = None


class ClaudeCountTokensRequest(BaseModel):
    """Client payload for ``POST /v1/messages/count_tokens``.

    Anthropic only requires the prompt-bearing fields here; generation-only
    fields such as ``max_tokens`` are optional and ignored by the counter.
    """

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[dict[str, object]]
    system: str | list[dict[str, object]] | None = None
    max_tokens: int | None = None
    tools: list[dict[str, object]] | None = None
    tool_choice: dict[str, object] | None = None
    thinking: dict[str, object] | None = None


__all__ = ["ClaudeCountTokensRequest", "ClaudeMessagesRequest"]
