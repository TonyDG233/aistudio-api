"""Models and metadata routes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from aistudio_api.api.dependencies import get_runtime_state
from aistudio_api.api.response_models import (
    GeminiModelListResponse,
    GeminiModelResponse,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("routes.models")

if TYPE_CHECKING:
    from aistudio_api.api.state import RuntimeState
router = APIRouter()


def _to_gemini_model(
    model_data: Mapping[str, object], *, id_override: str | None = None
) -> GeminiModelResponse:
    raw_id = id_override or str(model_data.get("id") or model_data.get("name") or "")
    clean_id = raw_id.removeprefix("models/")
    name = f"models/{clean_id}"
    display_name = str(model_data.get("displayName") or clean_id)
    description = str(model_data.get("description") or f"Google {display_name} model")
    input_tokens = int(str(model_data.get("inputTokenLimit") or 1048576))
    output_tokens = int(str(model_data.get("outputTokenLimit") or 8192))
    raw_methods = model_data.get("supportedGenerationMethods")
    methods = (
        [str(x) for x in raw_methods]
        if isinstance(raw_methods, list)
        else [
            "generateContent",
            "countTokens",
            "createCachedContent",
        ]
    )
    return GeminiModelResponse(
        name=name,
        version="001",
        displayName=display_name,
        description=description,
        inputTokenLimit=input_tokens,
        outputTokenLimit=output_tokens,
        supportedGenerationMethods=methods,
        temperature=1.0,
        topP=0.95,
        topK=64,
    )


@router.get("/v1beta/models", response_model=GeminiModelListResponse)
async def list_models(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> GeminiModelListResponse:
    session = runtime_state.client._session if runtime_state.client else None
    from aistudio_api.application.model_catalog import (
        clean_model_id,
        project_model_ids,
        split_catalog_prefix,
    )
    from aistudio_api.infrastructure.gateway.model_defaults import get_features_config
    from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

    discovered = await model_discovery.get_models(session=session)
    projected = project_model_ids(discovered, get_features_config())
    by_clean = {
        clean_model_id(item.get("id") or item.get("name")): item for item in discovered
    }
    models = [
        _to_gemini_model(
            by_clean[split_catalog_prefix(projected_id)[0]],
            id_override=projected_id,
        )
        for projected_id in projected
    ]
    logger.info("获取模型列表完成 (共发现 %d 个)", len(models))
    return GeminiModelListResponse(models=models)


@router.get("/v1beta/models/{model_id:path}", response_model=GeminiModelResponse)
async def get_model(
    model_id: str,
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> GeminiModelResponse:
    session = runtime_state.client._session if runtime_state.client else None
    from aistudio_api.application.model_catalog import resolve_model_request
    from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

    clean_id, _channel = resolve_model_request(model_id)
    model_info = await model_discovery.get_model_by_id(clean_id, session=session)
    if model_info is not None:
        logger.info("成功获取模型元数据: %s", model_id)
        return _to_gemini_model(
            model_info, id_override=model_id.removeprefix("models/")
        )
    raise HTTPException(status_code=404, detail="Model not found")
