"""Structured HTTP response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class GeminiUsageMetadata(BaseModel):
    promptTokenCount: int = 0
    candidatesTokenCount: int = 0
    thoughtsTokenCount: int = 0
    totalTokenCount: int = 0


class GeminiFunctionCallPayload(BaseModel):
    name: str
    args: object | None = None
    id: str | None = None


class GeminiFunctionResponsePayload(BaseModel):
    name: str
    response: object | None = None
    id: str | None = None


class GeminiInlineDataResponse(BaseModel):
    mimeType: str
    data: str


class GeminiPartResponse(BaseModel):
    text: str | None = None
    thought: bool | None = None
    inlineData: GeminiInlineDataResponse | None = None
    thoughtSignature: str | None = None
    functionCall: GeminiFunctionCallPayload | None = None
    functionResponse: GeminiFunctionResponsePayload | None = None


class GeminiContentResponse(BaseModel):
    role: Literal["model"] = "model"
    parts: list[GeminiPartResponse]


class GeminiCandidateResponse(BaseModel):
    content: GeminiContentResponse
    finishReason: str | None = None
    index: int = 0


class GeminiGenerateContentResponse(BaseModel):
    candidates: list[GeminiCandidateResponse]
    usageMetadata: GeminiUsageMetadata | None = None
    modelVersion: str | None = None
    responseId: str | None = None


class HealthResponse(BaseModel):
    status: str
    busy: bool


class StatsBucketResponse(BaseModel):
    requests: int = 0
    attempts: int = 0
    success: int = 0
    rate_limited: int = 0
    errors: int = 0
    client_requests: int | None = None
    client_success: int | None = None
    client_errors: int | None = None
    client_rate_limited: int | None = None
    client_aborted: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    total_tokens: int = 0
    cache_hit_rate: float | None = None
    count_requests: int | None = None
    count_success: int | None = None
    count_errors: int | None = None
    count_input_tokens: int | None = None
    usage_missing: int = 0
    last_used: str | None = None


class ModelStatsResponse(StatsBucketResponse):
    paid: bool | None = None
    formats: dict[str, StatsBucketResponse] = Field(default_factory=dict)
    surfaces: dict[str, StatsBucketResponse] = Field(default_factory=dict)


class StatsRangeResponse(BaseModel):
    key: str
    start: str | None = None
    end: str | None = None
    timezone: str = "Asia/Shanghai"
    data_start: str | None = None
    retention_days: int = 90
    partial: bool = False


class StatsResponse(BaseModel):
    schema_version: int = 2
    generated_at: str
    range: StatsRangeResponse
    models: dict[str, ModelStatsResponse]
    totals: ModelStatsResponse


class StatsTimeseriesPoint(BaseModel):
    date: str
    totals: StatsBucketResponse | None = None


class StatsTimeseriesResponse(BaseModel):
    generated_at: str
    range: StatsRangeResponse
    bucket: str = "day"
    points: list[StatsTimeseriesPoint]


class GeminiModelResponse(BaseModel):
    name: str
    version: str | None = "001"
    displayName: str | None = None
    description: str | None = None
    inputTokenLimit: int | None = None
    outputTokenLimit: int | None = None
    supportedGenerationMethods: list[str] = [
        "generateContent",
        "countTokens",
        "createCachedContent",
    ]
    temperature: float | None = None
    topP: float | None = None
    topK: int | None = None


class GeminiModelListResponse(BaseModel):
    models: list[GeminiModelResponse]
