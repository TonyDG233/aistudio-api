"""OpenAI Chat Completions request schema.

Only the fields the converter consumes are constrained. Unknown fields are kept
so OpenAI SDK extensions (``extra_body`` flattening, provider-specific options)
do not fail validation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class OpenAIChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[dict[str, object]]
    stream: bool = False
    stream_options: dict[str, object] | None = None
    tools: list[dict[str, object]] | None = None
    functions: list[dict[str, object]] | None = None
    tool_choice: object = None
    function_call: object = None
    response_format: dict[str, object] | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    stop: str | list[str] | None = None
    n: int = 1
    reasoning_effort: str | None = None
    extra_body: dict[str, object] | None = None
