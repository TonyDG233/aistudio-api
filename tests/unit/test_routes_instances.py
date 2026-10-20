"""Contract tests for the WP5 v4 instance API."""

from __future__ import annotations

from typing import Any, cast

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
    "channels",
    "in_flight",
    "latest",
}


class FakeManager:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.closed: list[str] = []
        self.reassigned: list[tuple[str, str]] = []
        self.fail: Exception | None = None

    async def list_instances(self) -> InstanceList:
        return InstanceList(
            max_instances=2,
            latest_instance_id="inst-1",
            instances=(
                Instance(
                    id="inst-1",
                    number=1,
                    account_id="a",
                    state=InstanceState.ACTIVE,
                    channels=("pg",),
                    in_flight=1,
                    latest=True,
                ),
            ),
        )

    async def create(self, account_id: str) -> Instance:
        if self.fail:
            raise self.fail
        self.created.append(account_id)
        return Instance(
            id="inst-2",
            number=2,
            account_id=account_id,
            state=InstanceState.STARTING,
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
        )


@pytest.fixture
def manager() -> Any:
    fake = FakeManager()
    previous = runtime_state.instance_manager
    runtime_state.instance_manager = cast("Any", fake)
    try:
        yield fake
    finally:
        runtime_state.instance_manager = previous


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_list_instances_has_strict_user_fields(manager) -> None:
    async with _client() as client:
        resp = await client.get("/api/instances")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["max_instances"] == 2
    assert payload["latest_instance_id"] == "inst-1"
    item = payload["instances"][0]
    assert set(item) == EXPECTED_KEYS
    assert "context" not in str(payload).lower()
    assert "pages" not in str(payload).lower()


@pytest.mark.asyncio
async def test_create_and_close_instance(manager) -> None:
    async with _client() as client:
        created = await client.post("/api/instances", json={"account_id": "a"})
        closed = await client.delete("/api/instances/inst-2")
    assert created.status_code == 201
    assert set(created.json()) == EXPECTED_KEYS
    assert manager.created == ["a"]
    assert closed.status_code == 202
    assert closed.json()["state"] == "reclaiming"
    assert manager.closed == ["inst-2"]


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
