"""Anthropic Messages request schema (WP8 Claude format face)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ClaudeMessagesRequest(BaseModel):
    """Client payload for ``POST /v1/messages``.

    Unknown keys are accepted so that newer Anthropic convenience fields do not
    turn into hard failures; unsupported semantics are rejected in the request
    converter where they actually matter.
    """

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
