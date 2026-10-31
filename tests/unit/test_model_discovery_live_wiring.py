"""Live model discovery wiring: pool session, parsed Paid metadata, honest source."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from aistudio_api.api import routes_models, routes_openai
from aistudio_api.api.state import runtime_state
from aistudio_api.application.instance_manager import InstanceManager
from aistudio_api.application.model_access import (
    clear_paid_models,
    is_paid_model,
    set_paid_models,
)
from aistudio_api.application.routing_eligibility import (
    resolve_model_route,
    resolve_required_tiers,
    tier_allows,
)
from aistudio_api.config import settings
from aistudio_api.infrastructure.gateway import model_discovery as discovery_module
from aistudio_api.infrastructure.gateway.instance_pool import InstanceSlot, SlotState
from aistudio_api.infrastructure.gateway.model_discovery import (
    _FALLBACK_MODELS,
    ModelDiscoveryService,
    mark_discovery_source,
)

EVIDENCE = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "dev"
    / "evidence"
    / "B6"
    / "model_discovery_fix.json"
)


class _FakePage:
    def __init__(self, *, closed: bool = False) -> None:
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed


class _FakeSession:
    def __init__(self, *, closed: bool = False) -> None:
        self.page = _FakePage(closed=closed)


class _FakePool:
    max_contexts = 4
    specs: tuple[Any, ...] = ()

    def __init__(self, slots: list[InstanceSlot]) -> None:
        self._slots = slots

    def slots(self) -> tuple[InstanceSlot, ...]:
        return tuple(self._slots)

    def get(self, slot_id: str) -> InstanceSlot | None:
        return None


class _EmptyStore:
    def get_account(self, account_id: str) -> None:
        return None

    def get_auth_path_optional(
        self, account_id: str, *, require_exists: bool = False
    ) -> None:
        return None

    def list_accounts(self) -> list[Any]:
        return []

    def get_active_account(self) -> None:
        return None


class _DiscoveryManager:
    def __init__(self, session: object | None) -> None:
        self._session = session

    def discovery_session(self) -> object | None:
        return self._session


def _slot(
    lane: str, *, closed: bool = False, active_requests: int = 0
) -> InstanceSlot:
    slot = InstanceSlot(
        slot_id=f"slot-{lane}-{int(closed)}-{active_requests}",
        surface="chat",
        min_tier="Free",
        lane=lane,
        session=cast(Any, _FakeSession(closed=closed)),
        state=SlotState.READY,
    )
    slot.active_requests = active_requests
    return slot


def _raw_model(
    model_id: str,
    *,
    access_tier: int | None,
    access_modes: list[int] | None = None,
) -> list[object]:
    item: list[object] = [None] * 84
    item[0] = f"models/{model_id}"
    item[3] = model_id
    item[4] = f"{model_id} description"
    item[5] = 1048576
    item[6] = 8192
    item[7] = ["generateContent", "countTokens"]
    if access_tier is not None:
        item[77] = access_tier
    if access_modes is not None:
        item[82] = access_modes
    return item


@pytest.fixture(autouse=True)
def _isolated_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "model_cache_file", str(tmp_path / "models_cache.json")
    )
    clear_paid_models()
    saved = (
        discovery_module.model_discovery._cached_models,
        discovery_module.model_discovery._last_fetched_at,
        discovery_module.model_discovery.last_source,
        discovery_module.model_discovery.last_error,
    )
    discovery_module.model_discovery._cached_models = []
    discovery_module.model_discovery._last_fetched_at = 0.0
    discovery_module.model_discovery.last_source = "fallback"
    discovery_module.model_discovery.last_error = None
    yield
    (
        discovery_module.model_discovery._cached_models,
        discovery_module.model_discovery._last_fetched_at,
        discovery_module.model_discovery.last_source,
        discovery_module.model_discovery.last_error,
    ) = saved
    clear_paid_models()


def test_access_modes_3_or_4_mark_model_paid_without_access_tier():
    service = ModelDiscoveryService()
    parsed = service._parse_raw_models(
        [
            [
                _raw_model("modes-pro", access_tier=None, access_modes=[1, 3]),
                _raw_model("modes-ultra", access_tier=None, access_modes=[4]),
                _raw_model("modes-free", access_tier=None, access_modes=[1]),
            ]
        ]
    )
    by_id = {str(item["id"]): item for item in parsed}
    assert by_id["modes-pro"]["paid"] is True
    assert by_id["modes-ultra"]["paid"] is True
    assert by_id["modes-free"]["paid"] is False


@pytest.mark.asyncio
async def test_live_paid_item_absent_from_static_rules_flows_to_routing(monkeypatch):
    service = discovery_module.model_discovery
    parsed = service._parse_raw_models(
        [[_raw_model("gemini-3.6-flash", access_tier=2)]]
    )
    assert parsed[0]["paid"] is True
    assert parsed[0]["access_tier"] == 2

    # Prove the model is not paid because of a static config rule or a stale
    # built-in default: start from an explicitly free marker.
    set_paid_models([{"id": "gemini-3.6-flash", "paid": False}])
    assert is_paid_model("gemini-3.6-flash") is False

    monkeypatch.setattr(service, "_fetch_via_page", AsyncMock(return_value=parsed))
    await service.get_models(session=_FakeSession(), force_refresh=True)

    assert service.last_source == "live_page"
    assert is_paid_model("gemini-3.6-flash") is True
    assert resolve_required_tiers("gemini-3.6-flash", rules={}) == (
        "Pro",
        "Ultra",
        "Ultra_20X",
    )
    route = resolve_model_route("gemini-3.6-flash", rules={})
    assert route.paid is True
    assert route.lane == "paid"
    assert route.required_tier == "Pro"
    assert tier_allows("Free", route) is False
    assert tier_allows("Pro", route) is True


@pytest.mark.asyncio
async def test_live_free_model_stays_free(monkeypatch):
    service = discovery_module.model_discovery
    parsed = service._parse_raw_models(
        [
            [
                _raw_model(
                    "gemini-9-free-demo", access_tier=1, access_modes=[1]
                )
            ]
        ]
    )
    assert parsed[0]["paid"] is False

    monkeypatch.setattr(service, "_fetch_via_page", AsyncMock(return_value=parsed))
    await service.get_models(session=_FakeSession(), force_refresh=True)

    assert service.last_source == "live_page"
    assert is_paid_model("gemini-9-free-demo") is False
    route = resolve_model_route("gemini-9-free-demo", rules={})
    assert route.paid is False
    assert route.lane == "free"
    assert route.required_tiers == ()


@pytest.mark.asyncio
async def test_fallback_is_marked_fallback_and_never_live():
    service = ModelDiscoveryService()

    models = await service.get_models(force_refresh=True)

    assert len(models) == len(_FALLBACK_MODELS) == 16
    assert service.last_source == "fallback"
    assert service.last_error is not None
    assert {m["id"] for m in models} == {m["id"] for m in _FALLBACK_MODELS}
    assert service.last_source not in discovery_module.LIVE_SOURCES


def test_every_fallback_entry_has_explicit_paid_flag():
    for item in _FALLBACK_MODELS:
        assert isinstance(item.get("paid"), bool), item["id"]
    gemini_35 = next(m for m in _FALLBACK_MODELS if m["id"] == "gemini-3.5-flash")
    assert gemini_35["paid"] is False


def _install_evidence_snapshot() -> list[dict[str, object]]:
    payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    models = [dict(item) for item in payload["models"]]
    mark_discovery_source(models, "snapshot")
    discovery_module.model_discovery._cached_models = models
    set_paid_models(models)
    return models


_IMAGE_RULE = {
    "image": {
        "contains": ["image"],
        "required_tiers": ["Pro", "Ultra", "Ultra_20X"],
    }
}


def test_live_free_truth_overrides_static_image_rule():
    """Q1: a live Free record wins over a static 'image' substring rule."""
    _install_evidence_snapshot()

    assert (
        resolve_required_tiers("gemini-3.1-flash-lite-image", rules=_IMAGE_RULE)
        == ()
    )
    route = resolve_model_route("gemini-3.1-flash-lite-image", rules=_IMAGE_RULE)
    assert route.paid is False
    assert route.lane == "free"
    assert route.required_tiers == ()
    assert (
        discovery_module.model_discovery.get_required_tiers(
            "gemini-3.1-flash-lite-image"
        )
        == []
    )


def test_live_paid_truth_from_evidence_uses_paid_lane():
    _install_evidence_snapshot()

    route = resolve_model_route("antigravity-preview-09-2026", rules={})
    assert route.paid is True
    assert route.lane == "paid"
    assert route.required_tiers == ("Pro", "Ultra", "Ultra_20X")
    assert (
        discovery_module.model_discovery.get_required_tiers(
            "antigravity-preview-09-2026"
        )
        == ["Pro", "Ultra", "Ultra_20X"]
    )


def test_live_free_gemini_pro_preview_is_not_paid_from_name():
    """Q2: 'pro' in the name never implies Paid; live evidence says Free."""
    _install_evidence_snapshot()

    route = resolve_model_route("gemini-3.1-pro-preview")
    assert route.paid is False
    assert route.required_tiers == ()


def test_fallback_free_is_not_a_live_free_truth():
    """Static rules still apply to fallback/unknown records (no live Free truth)."""
    fallback = [{"id": "fallback-image-model", "paid": False}]
    mark_discovery_source(fallback, "fallback")
    discovery_module.model_discovery._cached_models = fallback

    assert (
        resolve_required_tiers("fallback-image-model", rules=_IMAGE_RULE)
        == ("Pro", "Ultra", "Ultra_20X")
    )
    route = resolve_model_route("fallback-image-model", rules=_IMAGE_RULE)
    assert route.paid is True
    assert route.lane == "paid"


def test_discovery_sessions_order_paid_idle_paid_busy_free_idle_free_busy():
    slots = [
        _slot("free", active_requests=1),
        _slot("free"),
        _slot("paid", active_requests=1),
        _slot("paid"),
    ]
    pool = _FakePool(slots)
    manager = InstanceManager(cast(Any, pool), cast(Any, _EmptyStore()))

    sessions = manager.discovery_sessions()

    assert sessions == (
        slots[3].session,
        slots[2].session,
        slots[1].session,
        slots[0].session,
    )
    # Read-only: ordering never acquires or releases in-flight counters.
    assert [slot.active_requests for slot in slots] == [1, 0, 1, 0]


def test_pool_discovery_session_prefers_paid_lane():
    pool = _FakePool([_slot("free"), _slot("paid")])
    manager = InstanceManager(cast(Any, pool), cast(Any, _EmptyStore()))

    session = manager.discovery_session()

    assert session is pool.slots()[1].session


def test_pool_discovery_session_skips_closed_and_not_ready():
    pool = _FakePool([_slot("paid", closed=True), _slot("free")])
    manager = InstanceManager(cast(Any, pool), cast(Any, _EmptyStore()))

    session = manager.discovery_session()

    assert session is pool.slots()[1].session


def test_pool_discovery_session_returns_none_without_ready_slot():
    pool = _FakePool([])
    manager = InstanceManager(cast(Any, pool), cast(Any, _EmptyStore()))

    assert manager.discovery_session() is None


@pytest.mark.asyncio
async def test_discovery_falls_back_to_next_pool_session_after_401(monkeypatch):
    service = discovery_module.model_discovery
    parsed = service._parse_raw_models(
        [[_raw_model("gemini-9-free-demo", access_tier=1)]]
    )
    calls: list[str] = []

    async def _fetch(page, **_kwargs):
        calls.append(str(id(page)))
        if len(calls) == 1:
            raise RuntimeError("ListModels XHR failed (status=401)")
        return parsed

    monkeypatch.setattr(service, "_fetch_via_page", _fetch)
    pool = _FakePool([_slot("free"), _slot("paid")])
    manager = InstanceManager(cast(Any, pool), cast(Any, _EmptyStore()))

    models = await service.get_models(
        sessions=manager.discovery_sessions(), force_refresh=True
    )

    assert service.last_source == "live_page"
    assert [m["id"] for m in models] == ["gemini-9-free-demo"]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_gemini_and_openai_model_routes_use_pool_session(monkeypatch):
    pool_session = object()
    control_session = object()
    seen: list[object] = []

    async def _get_models(**kwargs):
        seen.append(kwargs.get("sessions"))
        return [{"id": "pool-model", "displayName": "Pool Model"}]

    monkeypatch.setattr(
        discovery_module.model_discovery, "get_models", _get_models
    )
    saved_manager = runtime_state.instance_manager
    saved_client = runtime_state.client
    runtime_state.instance_manager = cast(
        Any, _DiscoveryManager(pool_session)
    )
    runtime_state.client = cast(
        Any, SimpleNamespace(_session=control_session)
    )
    try:
        await routes_models.list_models(runtime_state=runtime_state)
        await routes_openai.list_models(state=runtime_state)
    finally:
        runtime_state.instance_manager = saved_manager
        runtime_state.client = saved_client

    assert seen == [(pool_session,), (pool_session,)]
