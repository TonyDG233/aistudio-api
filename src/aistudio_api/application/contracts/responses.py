"""OpenAI Responses HTTP request contract.

Only the fields the converter consumes are constrained.  Unknown fields are
kept so SDK extensions do not fail validation.  Stateful fields are accepted
here and rejected (or ignored) by the converter, never by Pydantic, so the
error body can carry a precise ``param``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    input: str | list[dict[str, object]]
    instructions: str | list[dict[str, object]] | None = None
    reasoning: dict[str, object] | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    tools: list[dict[str, object]] | None = None
    tool_choice: object = None
    stream: bool = False
    previous_response_id: str | None = None
    metadata: dict[str, object] | None = None
    store: bool | None = None
    parallel_tool_calls: bool = True
    truncation: str | None = None
    include: list[str] | None = None
    text: dict[str, object] | None = None
    user: str | None = None
    service_tier: str | None = None
    seed: int | None = None
