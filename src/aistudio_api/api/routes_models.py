"""Models and metadata routes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from aistudio_api.api.dependencies import (
    get_runtime_state,
    require_api_key_or_web_session,
)
from aistudio_api.api.response_models import (
    GeminiModelListResponse,
    GeminiModelResponse,
)
from aistudio_api.api.state import discovery_sessions_for
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("routes.models")

if TYPE_CHECKING:
    from aistudio_api.api.state import RuntimeState
router = APIRouter()


def _ui_debug_models(
    runtime_state: RuntimeState,
) -> list[dict[str, object]] | None:
    """Return the built-in offline catalog while ui_debug is enabled."""
    if not getattr(runtime_state, "ui_debug", False):
        return None
    from aistudio_api.api.ui_sandbox import sandbox_model_catalog

    return sandbox_model_catalog()


def _to_gemini_model(model_data: Mapping[str, object]) -> GeminiModelResponse:
    raw_id = str(model_data.get("id") or model_data.get("name") or "")
    clean_id = raw_id.removeprefix("models/")
    name = f"models/{clean_id}"
    version = str(model_data.get("version") or "001")
    display_name = str(model_data.get("displayName") or clean_id)
    description = str(model_data.get("description") or f"Google {display_name} model")
    input_tokens = int(str(model_data.get("inputTokenLimit") or 1048576))
    output_tokens = int(str(model_data.get("outputTokenLimit") or 8192))
    raw_methods = model_data.get("supportedGenerationMethods")
    methods = (
        [str(x) for x in raw_methods]
        if isinstance(raw_methods, list) and raw_methods
        else [
            "generateContent",
            "countTokens",
            "createCachedContent",
        ]
    )
    raw_temp = model_data.get("temperature")
    temperature = (
        float(raw_temp)
        if isinstance(raw_temp, (int, float)) and not isinstance(raw_temp, bool)
        else 1.0
    )
    raw_top_p = model_data.get("topP")
    top_p = (
        float(raw_top_p)
        if isinstance(raw_top_p, (int, float)) and not isinstance(raw_top_p, bool)
        else 0.95
    )
    raw_top_k = model_data.get("topK")
    top_k = (
        int(raw_top_k)
        if isinstance(raw_top_k, (int, float)) and not isinstance(raw_top_k, bool)
        else 64
    )
    return GeminiModelResponse(
        name=name,
        version=version,
        displayName=display_name,
        description=description,
        inputTokenLimit=input_tokens,
        outputTokenLimit=output_tokens,
        supportedGenerationMethods=methods,
        temperature=temperature,
        topP=top_p,
        topK=top_k,
    )


@router.get(
    "/v1beta/models",
    response_model=GeminiModelListResponse,
    dependencies=[Depends(require_api_key_or_web_session)],
)
async def list_models(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> GeminiModelListResponse:
    """Return bare model IDs; discovery owns model/paid metadata."""
    discovered = _ui_debug_models(runtime_state)
    if discovered is None:
        sessions = discovery_sessions_for(runtime_state)
        from aistudio_api.infrastructure.gateway.model_discovery import (
            model_discovery,
        )

        discovered = await model_discovery.get_models(sessions=sessions)
    models = [_to_gemini_model(item) for item in discovered]
    logger.info("获取模型列表完成: 数量=%d", len(models))
    return GeminiModelListResponse(models=models)


@router.get(
    "/api/models",
    response_model=GeminiModelListResponse,
    dependencies=[Depends(require_api_key_or_web_session)],
)
async def list_models_admin(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> GeminiModelListResponse:
    """Admin model list; ui_debug returns the offline catalog, otherwise 404.

    The production admin UI consumes ``/v1beta/models``; keeping this alias
    ui_debug-only avoids adding a second real catalog surface.
    """
    discovered = _ui_debug_models(runtime_state)
    if discovered is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return GeminiModelListResponse(
        models=[_to_gemini_model(item) for item in discovered]
    )


@router.get(
    "/v1beta/models/{model_id:path}",
    response_model=GeminiModelResponse,
    dependencies=[Depends(require_api_key_or_web_session)],
)
async def get_model(
    model_id: str,
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> GeminiModelResponse:
    sandbox_models = _ui_debug_models(runtime_state)
    if sandbox_models is not None:
        from aistudio_api.api.ui_sandbox import sandbox_model_by_id

        model_info = sandbox_model_by_id(model_id)
    else:
        sessions = discovery_sessions_for(runtime_state)
        from aistudio_api.infrastructure.gateway.model_discovery import (
            model_discovery,
        )

        model_info = await model_discovery.get_model_by_id(
            model_id, sessions=sessions
        )
    if model_info is not None:
        logger.info("获取模型详情成功: model=%s", model_id)
        return _to_gemini_model(model_info)
    logger.warning("请求的模型未找到: model=%s", model_id)
    raise HTTPException(status_code=404, detail="Model not found")
