"""User-facing instance routes (WP5 v4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from aistudio_api.api.dependencies import get_runtime_state
from aistudio_api.application.instance_manager import InstanceManager
from aistudio_api.domain.instances import Instance, InstanceList

if TYPE_CHECKING:
    from aistudio_api.api.state import RuntimeState

router = APIRouter(prefix="/instances")


class InstanceResponse(BaseModel):
    id: str
    number: int
    account_id: str
    state: str
    channel: str
    in_flight: int
    latest: bool


class InstanceListResponse(BaseModel):
    max_instances: int
    latest_instance_id: str | None
    current_instance_id: str | None
    instances: list[InstanceResponse]


class CreateInstanceRequest(BaseModel):
    account_id: str = Field(min_length=1)
    channel: str | None = None


class ReassignInstanceRequest(BaseModel):
    account_id: str = Field(min_length=1)


class SwitchInstanceRequest(BaseModel):
    channel: str | None = None


def to_instance_response(instance: Instance) -> InstanceResponse:
    return InstanceResponse(
        id=instance.id,
        number=instance.number,
        account_id=instance.account_id,
        state=instance.state.value,
        channel=instance.channel,
        in_flight=instance.in_flight,
        latest=instance.latest,
    )


def get_instance_manager(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> InstanceManager:
    manager = runtime_state.instance_manager
    if manager is None:
        raise HTTPException(503, detail="实例服务未初始化")
    return manager


@router.get("", response_model=InstanceListResponse)
async def list_instances(
    manager: InstanceManager = Depends(get_instance_manager),
) -> InstanceListResponse:
    result: InstanceList = await manager.list_instances()
    return InstanceListResponse(
        max_instances=result.max_instances,
        latest_instance_id=result.latest_instance_id,
        current_instance_id=result.current_instance_id,
        instances=[to_instance_response(item) for item in result.instances],
    )


@router.post("", response_model=InstanceResponse, status_code=201)
async def create_instance(
    payload: CreateInstanceRequest,
    manager: InstanceManager = Depends(get_instance_manager),
) -> InstanceResponse:
    instance = await manager.create(payload.account_id, payload.channel)
    return to_instance_response(instance)


@router.delete("/{instance_id}", response_model=InstanceResponse, status_code=202)
async def close_instance(
    instance_id: str,
    manager: InstanceManager = Depends(get_instance_manager),
) -> InstanceResponse:
    instance = await manager.close(instance_id)
    return to_instance_response(instance)


@router.post("/{instance_id}/switch", response_model=InstanceResponse)
async def switch_instance(
    instance_id: str,
    payload: SwitchInstanceRequest | None = None,
    manager: InstanceManager = Depends(get_instance_manager),
) -> InstanceResponse:
    instance = await manager.switch_to(
        instance_id, payload.channel if payload is not None else None
    )
    return to_instance_response(instance)


@router.post("/{instance_id}/reassign", response_model=InstanceResponse)
async def reassign_instance(
    instance_id: str,
    payload: ReassignInstanceRequest,
    manager: InstanceManager = Depends(get_instance_manager),
) -> InstanceResponse:
    instance = await manager.reassign(instance_id, payload.account_id)
    return to_instance_response(instance)


__all__ = ["router"]
