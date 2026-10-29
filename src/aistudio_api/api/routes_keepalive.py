"""Account keepalive status and manual-run routes."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aistudio_api.api.dependencies import get_runtime_state
from aistudio_api.api.state import RuntimeState
from aistudio_api.application.account_keepalive import TASKS
from aistudio_api.infrastructure.gateway.model_defaults import (
    get_configured_keepalive_settings,
)

router = APIRouter(prefix="/keepalive")


class KeepaliveRunRequest(BaseModel):
    task: str


@router.get("")
async def get_keepalive_status(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """返回最近一次回写/刷新结果与下次运行时间。"""
    if runtime_state.keepalive is None:
        return {
            "available": False,
            "settings": get_configured_keepalive_settings(),
        }
    service = cast(Any, runtime_state.keepalive)
    return {"available": True, **service.status()}


@router.post("/run")
async def run_keepalive_task(
    req: KeepaliveRunRequest,
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """手动触发保活任务（仍受全局串行锁约束）。"""
    if req.task not in TASKS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown task: {req.task}; expected one of {', '.join(TASKS)}",
        )
    service = runtime_state.keepalive
    if service is None:
        raise HTTPException(status_code=503, detail="保活服务未初始化")
    result = await cast(Any, service).run_now(req.task, trigger="api")
    if result.get("reason") == "busy":
        raise HTTPException(status_code=409, detail="淇濇椿浠诲姟姝ｅ湪杩愯")
    if result.get("reason") == "exception":
        raise HTTPException(
            status_code=500,
            detail=str(result.get("error") or "keepalive task failed"),
        )
    return result
