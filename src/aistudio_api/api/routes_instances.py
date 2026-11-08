"""User-facing instance routes (one account + one Context + one Page)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from aistudio_api.api.dependencies import get_runtime_state
from aistudio_api.application.instance_manager import InstanceManager
from aistudio_api.domain.instances import Instance, InstanceList
from aistudio_api.domain.model_access import TierSelectorError
from aistudio_api.infrastructure.account.account_store import AccountMeta
from aistudio_api.infrastructure.browser.fingerprint import get_account_fingerprint

if TYPE_CHECKING:
    from aistudio_api.api.state import RuntimeState

router = APIRouter(prefix="/instances")


class InstanceTierSelectorResponse(BaseModel):
    only_tier: str = ""
    min_tier: str = ""
    max_tier: str = ""


class InstanceResponse(BaseModel):
    id: str
    number: int
    account_id: str
    state: str
    surface: str
    only_tier: str
    min_tier: str
    max_tier: str
    in_flight: int
    latest: bool
    account_email: str | None = None
    account_name: str = ""
    account_tier: str = ""
    tier: str = ""
    fingerprint: str = ""
    fingerprint_summary: str = ""
    last_used: float | None = None
    tier_selector: InstanceTierSelectorResponse = Field(
        default_factory=InstanceTierSelectorResponse
    )


class InstanceListResponse(BaseModel):
    max_instances: int
    latest_instance_id: str | None
    current_instance_id: str | None
    instances: list[InstanceResponse]
    engine: str = ""
    engine_phase: str = ""
    engine_target: str = ""
    rebuilt: int = 0
    total: int = 0
    transport_state: str = ""
    worker_state: str = ""
    proof_latency_ms: float | None = None
    fingerprint_profile: str = ""


class CreateInstanceRequest(BaseModel):
    account_id: str = Field(min_length=1)
    surface: str | None = None
    only_tier: str | None = None
    min_tier: str | None = None
    max_tier: str | None = None


class ReassignInstanceRequest(BaseModel):
    account_id: str = Field(min_length=1)


class SwitchInstanceRequest(BaseModel):
    surface: str | None = None


def to_instance_response(
    instance: Instance,
    *,
    account: AccountMeta | None = None,
) -> InstanceResponse:
    account_tier = account.tier if account is not None else ""
    fingerprint = get_account_fingerprint(instance.account_id)
    return InstanceResponse(
        id=instance.id,
        number=instance.number,
        account_id=instance.account_id,
        state=instance.state.value,
        surface=instance.surface,
        only_tier=instance.only_tier,
        min_tier=instance.min_tier,
        max_tier=instance.max_tier,
        in_flight=instance.in_flight,
        latest=instance.latest,
        account_email=account.email if account is not None else None,
        account_name=account.name if account is not None else "",
        account_tier=account_tier,
        tier=account_tier,
        fingerprint=fingerprint,
        fingerprint_summary=fingerprint,
        last_used=instance.last_used,
        tier_selector=InstanceTierSelectorResponse(
            only_tier=instance.only_tier,
            min_tier=instance.min_tier,
            max_tier=instance.max_tier,
        ),
    )


def _account_for(runtime_state: RuntimeState, account_id: str) -> AccountMeta | None:
    service = runtime_state.account_service
    if service is None or not account_id:
        return None
    return service.get_account(account_id)


def get_instance_manager(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> InstanceManager:
    manager = runtime_state.instance_manager
    if manager is None:
        raise HTTPException(503, detail="实例服务未初始化")
    return manager  # type: ignore[return-value]


@router.get("", response_model=InstanceListResponse)
async def list_instances(
    manager: InstanceManager = Depends(get_instance_manager),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> InstanceListResponse:
    result: InstanceList = await manager.list_instances()
    engine_status: dict[str, object] = {}
    switcher = runtime_state.engine_switcher
    getter = getattr(switcher, "status", None)
    if callable(getter):
        try:
            raw_engine_status = getter()
        except Exception:
            raw_engine_status = {}
        if isinstance(raw_engine_status, dict):
            engine_status = {
                str(key): value for key, value in raw_engine_status.items()
            }
    return InstanceListResponse(
        max_instances=result.max_instances,
        latest_instance_id=result.latest_instance_id,
        current_instance_id=result.current_instance_id,
        instances=[
            to_instance_response(
                item, account=_account_for(runtime_state, item.account_id)
            )
            for item in result.instances
        ],
        engine=str(engine_status.get("engine") or ""),
        engine_phase=str(engine_status.get("engine_phase") or ""),
        engine_target=str(engine_status.get("engine_target") or ""),
        rebuilt=_engine_int(engine_status.get("rebuilt")),
        total=_engine_int(engine_status.get("total")),
        transport_state=str(engine_status.get("transport_state") or ""),
        worker_state=str(engine_status.get("worker_state") or ""),
        proof_latency_ms=_engine_float(engine_status.get("proof_latency_ms")),
        fingerprint_profile=str(engine_status.get("fingerprint_profile") or ""),
    )


def _engine_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _engine_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


@router.post("", response_model=InstanceResponse, status_code=201)
async def create_instance(
    payload: CreateInstanceRequest,
    manager: InstanceManager = Depends(get_instance_manager),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> InstanceResponse:
    selector = {
        "only_tier": payload.only_tier,
        "min_tier": payload.min_tier,
        "max_tier": payload.max_tier,
    }
    try:
        if any(value for value in selector.values()):
            instance = await manager.create(
                payload.account_id,
                payload.surface,
                only_tier=payload.only_tier,
                min_tier=payload.min_tier,
                max_tier=payload.max_tier,
            )
        else:
            instance = await manager.create(payload.account_id, payload.surface)
    except TierSelectorError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    return to_instance_response(
        instance, account=_account_for(runtime_state, instance.account_id)
    )


@router.delete("/{instance_id}", response_model=InstanceResponse, status_code=202)
async def close_instance(
    instance_id: str,
    manager: InstanceManager = Depends(get_instance_manager),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> InstanceResponse:
    instance = await manager.close(instance_id)
    if instance is None:
        raise HTTPException(404, detail="实例不存在")
    return to_instance_response(
        instance, account=_account_for(runtime_state, instance.account_id)
    )


@router.post("/{instance_id}/switch", response_model=InstanceResponse)
async def switch_instance(
    instance_id: str,
    payload: SwitchInstanceRequest | None = None,
    manager: InstanceManager = Depends(get_instance_manager),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> InstanceResponse:
    instance = await manager.switch_to(
        instance_id, payload.surface if payload is not None else None
    )
    return to_instance_response(
        instance, account=_account_for(runtime_state, instance.account_id)
    )


@router.post("/{instance_id}/restart", response_model=InstanceResponse)
async def restart_instance(
    instance_id: str,
    manager: InstanceManager = Depends(get_instance_manager),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> InstanceResponse:
    instance = await manager.restart(instance_id)
    return to_instance_response(
        instance, account=_account_for(runtime_state, instance.account_id)
    )


@router.post("/{instance_id}/reassign", response_model=InstanceResponse)
async def reassign_instance(
    instance_id: str,
    payload: ReassignInstanceRequest,
    manager: InstanceManager = Depends(get_instance_manager),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> InstanceResponse:
    instance = await manager.reassign(instance_id, payload.account_id)
    return to_instance_response(
        instance, account=_account_for(runtime_state, instance.account_id)
    )


@router.post("/{instance_id}/wake", response_model=InstanceResponse)
async def wake_instance(
    instance_id: str,
    manager: InstanceManager = Depends(get_instance_manager),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> InstanceResponse:
    waker = cast(
        Callable[[str], Awaitable[Instance]] | None,
        getattr(manager, "wake", None),
    )
    if waker is None:
        raise HTTPException(409, detail="该实例不支持唤醒")
    instance = await waker(instance_id)
    return to_instance_response(
        instance, account=_account_for(runtime_state, instance.account_id)
    )


__all__ = ["router"]
