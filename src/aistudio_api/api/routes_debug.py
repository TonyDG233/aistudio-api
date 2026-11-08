"""C2a UI debug endpoints (available only while ``ui_debug`` is enabled)."""

from __future__ import annotations

import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from aistudio_api.api.dependencies import get_runtime_state, require_web_auth
from aistudio_api.api.state import RuntimeState
from aistudio_api.api.ui_sandbox import UISandbox
from aistudio_api.infrastructure.utils.log_broadcast import get_broadcaster
from aistudio_api.infrastructure.utils.logger import get_logger, refresh_log_sinks


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
            "engine_health_ok",
            "engine_health_fail",
            "engine_switching",
            "engine_switch_done",
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


class LogInjectRequest(BaseModel):
    kind: Literal["all_levels", "sanitize_test", "long_payload"]


_SAMPLE_LOGGER = "ui-debug.sample"

_ALL_LEVELS_SAMPLES: tuple[tuple[int, str], ...] = (
    (
        logging.DEBUG,
        "[instances] 实例池选择实例 instance=inst-1 surface=chat in_flight=1",
    ),
    (
        logging.INFO,
        "[router] 路由决策 account=acc_pro_prod tier=Pro model=gemini-3.7-flash",
    ),
    (
        logging.WARNING,
        "[rotator] 429 限流，账号 acc_free_ready 进入冷却 60s",
    ),
    (
        logging.ERROR,
        "[gateway] 上游返回 502，切换账号后重试 attempt=2",
    ),
    (
        logging.CRITICAL,
        "[circuit] 熔断器打开：连续 5 次失败，暂停调度",
    ),
)

_FAKE_GOOGLE_API_KEY = "AIza" + "SyFAKE1234567890SECRETVALUE"

_SANITIZE_SAMPLE = (
    "请求头 Authorization: Bearer sk-1234567890abcdefSECRET\n"
    "Cookie: SAPISID=abc123SECRET; SID=def456SECRET; theme=dark\n"
    f"x-goog-api-key: {_FAKE_GOOGLE_API_KEY}\n"
    "HTTP DELETE /api-keys/sk-1234567890abcdefSECRET -> 200\n"
    '请求体 {"api_key": "sk-json-SECRET-123456", "note": '
    '"keep-me-ui-debug-archive"}\n'
    "SAPISID=standaloneSAPISIDSECRET"
)


def _build_long_payload() -> str:
    """Build a valid JSON + stack-trace record sized for 4-8 KB viewers."""
    entries = [
        {"index": index, "payload": "abcdef" * 8}
        for index in range(45)
    ]
    stack = "\n".join(
        f"  at frame_{index} (gateway.py:{100 + index})" for index in range(60)
    )
    payload = {
        "kind": "long_payload",
        "request_id": "ui-debug-long-payload",
        "entries": entries,
        "stack": stack,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _emit_debug_samples(kind: str) -> int:
    """Emit samples through the real logging pipeline and return the id span."""
    refresh_log_sinks()
    logger = get_logger(_SAMPLE_LOGGER)
    broadcaster = get_broadcaster()
    start_id = broadcaster.latest_id

    if kind == "all_levels":
        for level, message in _ALL_LEVELS_SAMPLES:
            logger.log(level, message)
    elif kind == "sanitize_test":
        logger.info(_SANITIZE_SAMPLE)
    else:
        logger.info(_build_long_payload())

    return broadcaster.latest_id - start_id


@router.post("/logs/inject")
async def inject_debug_logs(
    req: LogInjectRequest,
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """Emit sandbox log samples for the debug log viewer (404 when off)."""
    _require_sandbox(runtime_state)
    injected = _emit_debug_samples(req.kind)
    return {"ok": True, "kind": req.kind, "injected": injected}


@router.post("/logs/clear")
async def clear_debug_logs(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, object]:
    """Clear only the in-memory ring buffer, keeping archives and ids."""
    _require_sandbox(runtime_state)
    cleared = get_broadcaster().clear()
    return {"ok": True, "cleared": cleared}


__all__ = ["router"]
