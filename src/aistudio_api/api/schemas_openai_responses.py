"""OpenAI Responses API request schema (WP11.1).

Only the fields the converter consumes are constrained; extra fields are kept
so SDK extensions do not fail validation. Unsupported stateful fields are
rejected explicitly in the converter.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    input: str | list[dict[str, object]]
    instructions: str | list[dict[str, object]] | None = None
    tools: list[dict[str, object]] | None = None
    tool_choice: object = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stream: bool = False
    reasoning: dict[str, object] | None = None
    text: dict[str, object] | None = None
    store: bool = False
    previous_response_id: str | None = None
    parallel_tool_calls: bool = True
    metadata: dict[str, object] | None = None
    user: str | None = None
    truncation: str | None = None
    include: list[str] | None = None
