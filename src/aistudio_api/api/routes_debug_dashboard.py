"""Dashboard-specific UI debug routes (available only while ``ui_debug`` is on)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from aistudio_api.api.dependencies import get_runtime_state, require_web_auth
from aistudio_api.api.state import RuntimeState
from aistudio_api.api.ui_sandbox import UISandbox

DashboardScenario = Literal[
    "all_states", "mass_accounts", "all_429", "empty", "narrow_edge"
]
DashboardAction = Literal[
    "reset", "simulate_requests", "simulate_429", "toggle_formats"
]


def require_dashboard_debug(
    request: Request,
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> None:
    """404 before auth when ui_debug is off; enforce web auth when it is on."""
    sandbox = runtime_state.ui_sandbox
    if not runtime_state.ui_debug or not isinstance(sandbox, UISandbox):
        raise HTTPException(status_code=404, detail="Not Found")
    require_web_auth(request)


router = APIRouter(
    prefix="/debug/dashboard",
    dependencies=[Depends(require_dashboard_debug)],
)


class DashboardApplyRequest(BaseModel):
    scenario: DashboardScenario | None = None
    action: DashboardAction | None = None
    target_id: str | None = None


def _require_sandbox(runtime_state: RuntimeState) -> UISandbox:
    sandbox = runtime_state.ui_sandbox
    if not runtime_state.ui_debug or not isinstance(sandbox, UISandbox):
        raise HTTPException(status_code=404, detail="Not Found")
    return sandbox


@router.get("/state")
async def get_dashboard_state(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """Return the current dashboard sandbox snapshot and live statistics."""
    sandbox = _require_sandbox(runtime_state)
    return {**sandbox.snapshot(), "ok": True}


@router.post("/apply")
async def apply_dashboard_state(
    req: DashboardApplyRequest,
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """Apply a dashboard scenario or quick action (404 when ui_debug is off)."""
    sandbox = _require_sandbox(runtime_state)
    try:
        payload = sandbox.apply(
            scenario=req.scenario,
            action=req.action,
            target_id=req.target_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**payload, "ok": True}


__all__ = ["router"]
