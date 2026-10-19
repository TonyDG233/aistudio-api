"""Account keepalive status and manual-run routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aistudio_api.api.dependencies import get_runtime_state
from aistudio_api.api.state import RuntimeState
from aistudio_api.application.account_keepalive import TASKS
from aistudio_api.infrastructure.gateway.model_defaults import get_features_config

router = APIRouter(prefix="/keepalive")


class KeepaliveRunRequest(BaseModel):
    task: str


@router.get("")
async def get_keepalive_status(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """最近一次刷新/回写/切号结果与下次运行时间。"""
    if runtime_state.keepalive is None:
        return {
            "available": False,
            "features": get_features_config(),
        }
    return {"available": True, **runtime_state.keepalive.status()}


@router.post("/run")
async def run_keepalive_task(
    req: KeepaliveRunRequest,
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """手动触发保活任务（验收/排障用，仍受互斥与在途检查约束）。"""
    if req.task not in TASKS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown task: {req.task}; expected one of {', '.join(TASKS)}",
        )
    service = runtime_state.keepalive
    if service is None:
        raise HTTPException(status_code=503, detail="保活服务未初始化")
    result = await service.run_now(req.task, trigger="api")
    if result.get("reason") == "busy":
        raise HTTPException(status_code=409, detail="保活任务正在运行")
    return result
