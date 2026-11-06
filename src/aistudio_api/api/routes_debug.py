"""C2a UI debug endpoints (available only while ``ui_debug`` is enabled)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from aistudio_api.api.dependencies import get_runtime_state, require_web_auth
from aistudio_api.api.state import RuntimeState
from aistudio_api.api.ui_sandbox import UISandbox


def require_debug_access(
    request: Request,
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> None:
    """404 before auth when ui_debug is off; enforce web auth when it is on."""
    sandbox = runtime_state.ui_sandbox
    if not runtime_state.ui_debug or not isinstance(sandbox, UISandbox):
        raise HTTPException(status_code=404, detail="Not Found")
    require_web_auth(request)


router = APIRouter(
    prefix="/debug",
    dependencies=[Depends(require_debug_access)],
)


class UIScenarioRequest(BaseModel):
    scenario: Literal["all_states", "mass_accounts", "narrow_edge", "empty"] | None = (
        None
    )
    action: (
        Literal[
            "reset",
            "instance_inflight",
            "account_expired",
            "instance_cooldown",
            "keepalive_partial",
        ]
        | None
    ) = None
    target_id: str | None = None


def _require_sandbox(runtime_state: RuntimeState) -> UISandbox:
    sandbox = runtime_state.ui_sandbox
    if not runtime_state.ui_debug or not isinstance(sandbox, UISandbox):
        raise HTTPException(status_code=404, detail="Not Found")
    return sandbox


@router.get("/ui-state")
async def get_ui_state(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """Return the current offline sandbox state (404 when ui_debug is off)."""
    sandbox = _require_sandbox(runtime_state)
    return sandbox.snapshot()


@router.post("/ui-scenario")
async def set_ui_scenario(
    req: UIScenarioRequest,
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """Reset/apply a sandbox scenario or action (404 when ui_debug is off)."""
    sandbox = _require_sandbox(runtime_state)
    try:
        return sandbox.apply(
            scenario=req.scenario,
            action=req.action,
            target_id=req.target_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


__all__ = ["router"]
