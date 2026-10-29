"""Contract tests for the single-instance/single-surface instance API."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.api.state import runtime_state
from aistudio_api.application.instance_manager import (
    InstanceBusyError,
    InstanceCapacityExhaustedError,
)
from aistudio_api.domain.instances import Instance, InstanceList, InstanceState

EXPECTED_KEYS = {
    "id",
    "number",
    "account_id",
    "state",
    "surface",
    "min_tier",
    "in_flight",
    "latest",
}


class FakeManager:
    def __init__(self) -> None:
        self.created: list[tuple[str, str | None]] = []
        self.closed: list[str] = []
        self.reassigned: list[tuple[str, str]] = []
        self.restarted: list[str] = []
        self.fail: Exception | None = None

    async def list_instances(self) -> InstanceList:
        return InstanceList(
            max_instances=2,
            latest_instance_id="inst-1",
            current_instance_id="inst-1",
            instances=(
                Instance(
                    id="inst-1",
                    number=1,
                    account_id="a",
                    state=InstanceState.ACTIVE,
                    surface="chat",
                    min_tier="Free",
                    in_flight=1,
                    latest=True,
                ),
            ),
        )

    async def create(self, account_id: str, surface: str | None = None) -> Instance:
        if self.fail:
            raise self.fail
        self.created.append((account_id, surface))
        return Instance(
            id="inst-2",
            number=2,
            account_id=account_id,
            state=InstanceState.STARTING,
            surface=surface or "chat",
            min_tier="Free",
        )

    async def close(self, instance_id: str) -> Instance:
        if self.fail:
            raise self.fail
        self.closed.append(instance_id)
        return Instance(
            id=instance_id,
            number=2,
            account_id="b",
            state=InstanceState.RECLAIMING,
            surface="chat",
            min_tier="Free",
        )

    async def switch_to(self, instance_id: str, surface: str | None = None) -> Instance:
        if self.fail:
            raise self.fail
        return Instance(
            id=instance_id,
            number=2,
            account_id="b",
            state=InstanceState.ACTIVE,
            surface=surface or "chat",
            min_tier="Free",
        )

    async def reassign(self, instance_id: str, account_id: str) -> Instance:
        if self.fail:
            raise self.fail
        self.reassigned.append((instance_id, account_id))
        return Instance(
            id=instance_id,
            number=2,
            account_id=account_id,
            state=InstanceState.STARTING,
            surface="chat",
            min_tier="Free",
        )

    async def restart(self, instance_id: str) -> Instance:
        if self.fail:
            raise self.fail
        self.restarted.append(instance_id)
        return Instance(
            id=instance_id,
            number=2,
            account_id="b",
            state=InstanceState.ACTIVE,
            surface="chat",
            min_tier="Free",
        )


@pytest.fixture
def manager() -> Any:
    fake = FakeManager()
    previous = runtime_state.instance_manager
    runtime_state.instance_manager = fake
    try:
        yield fake
    finally:
        runtime_state.instance_manager = previous


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_list_instances_exposes_single_surface_fields(manager) -> None:
    async with _client() as client:
        resp = await client.get("/api/instances")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["max_instances"] == 2
    assert payload["latest_instance_id"] == "inst-1"
    assert payload["current_instance_id"] == "inst-1"
    item = payload["instances"][0]
    assert set(item) == EXPECTED_KEYS
    assert item["surface"] == "chat"
    assert "channel" not in item
    assert "context" not in str(payload).lower()
    assert "pages" not in str(payload).lower()


@pytest.mark.asyncio
async def test_create_and_close_instance(manager) -> None:
    async with _client() as client:
        created = await client.post(
            "/api/instances", json={"account_id": "a", "surface": "build"}
        )
        closed = await client.delete("/api/instances/inst-2")
    assert created.status_code == 201
    assert set(created.json()) == EXPECTED_KEYS
    assert manager.created == [("a", "build")]
    assert closed.status_code == 202
    assert closed.json()["state"] == "reclaiming"
    assert manager.closed == ["inst-2"]


@pytest.mark.asyncio
async def test_switch_instance_surface(manager) -> None:
    async with _client() as client:
        resp = await client.post(
            "/api/instances/inst-1/switch", json={"surface": "build"}
        )
    assert resp.status_code == 200
    assert resp.json()["id"] == "inst-1"
    assert resp.json()["surface"] == "build"


@pytest.mark.asyncio
async def test_reassign_instance(manager) -> None:
    async with _client() as client:
        resp = await client.post(
            "/api/instances/inst-2/reassign", json={"account_id": "c"}
        )
    assert resp.status_code == 200
    assert resp.json()["account_id"] == "c"
    assert manager.reassigned == [("inst-2", "c")]


@pytest.mark.asyncio
async def test_restart_instance(manager) -> None:
    async with _client() as client:
        resp = await client.post("/api/instances/inst-1/restart")
    assert resp.status_code == 200
    assert resp.json()["id"] == "inst-1"
    assert manager.restarted == ["inst-1"]


@pytest.mark.asyncio
async def test_capacity_and_busy_map_to_409(manager) -> None:
    manager.fail = InstanceCapacityExhaustedError("实例上限已满")
    async with _client() as client:
        resp = await client.post("/api/instances", json={"account_id": "a"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "instance_capacity_exhausted"

    manager.fail = InstanceBusyError("该实例仍有在途请求")
    async with _client() as client:
        resp = await client.delete("/api/instances/inst-1")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "instance_busy"


@pytest.mark.asyncio
async def test_missing_manager_returns_503() -> None:
    previous = runtime_state.instance_manager
    runtime_state.instance_manager = None
    try:
        async with _client() as client:
            resp = await client.get("/api/instances")
    finally:
        runtime_state.instance_manager = previous
    assert resp.status_code == 503
