"""Gemini-compatible API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from aistudio_api.api.response_models import GeminiGenerateContentResponse
from aistudio_api.application.api_service_gemini import handle_gemini_generate_content
from aistudio_api.application.routing_pipeline import RoutingPipeline
from aistudio_api.application.token_counter import count_tokens
from aistudio_api.infrastructure.utils.logger import get_logger

from .dependencies import get_routing_pipeline
from .schemas import GeminiGenerateContentRequest

logger = get_logger("routes.gemini")
router = APIRouter()


@router.post(
    "/v1beta/{model_path:path}:generateContent",
    response_model=GeminiGenerateContentResponse,
)
async def generate_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    pipeline: RoutingPipeline = Depends(get_routing_pipeline),
):
    logger.info("收到 Gemini generateContent 请求: model=%s", model_path)
    return await handle_gemini_generate_content(
        model_path, req, stream=False, pipeline=pipeline
    )


@router.post("/v1beta/{model_path:path}:streamGenerateContent")
async def stream_generate_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    pipeline: RoutingPipeline = Depends(get_routing_pipeline),
):
    logger.info("收到 Gemini streamGenerateContent 流式请求: model=%s", model_path)
    return await handle_gemini_generate_content(
        model_path, req, stream=True, pipeline=pipeline
    )


@router.post("/v1beta/{model_path:path}:countTokens")
async def count_tokens_route(
    model_path: str,
    req: GeminiGenerateContentRequest,
    pipeline: RoutingPipeline = Depends(get_routing_pipeline),
) -> JSONResponse:
    logger.info("Gemini countTokens request: model=%s", model_path)
    outcome = await count_tokens(
        model_path=model_path,
        req=req,
        pipeline=pipeline,
        api_format="gemini",
    )
    return JSONResponse({"totalTokens": outcome.total_tokens})
