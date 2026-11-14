"""Gemini routes must surface domain tier/routing errors before streaming."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.api.dependencies import get_routing_pipeline
from aistudio_api.application import routing_pipeline as routing_pipeline_module
from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.application.atg.extractor import SyntheticRequiredError
from aistudio_api.application.routing_pipeline import RoutingPipeline
from aistudio_api.config import settings
from aistudio_api.domain.errors import (
    RequestError,
    SurfaceCapacityError,
    TierRequiredError,
    UsageLimitExceeded,
)
from aistudio_api.domain.models import Candidate, ModelOutput
from aistudio_api.infrastructure.account.account_store import AccountMeta
from tests.unit.routing_fakes import (
    FakeHandle,
    FakeSession,
    RebindFakeManager,
    ScriptedPipeline,
    make_attempt,
    make_context,
    make_instance,
)

_HEADERS = {"x-goog-api-key": "test-key"}
_BODY = {
    "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
}
_PG_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "pg"


def _pg_fixture(name: str) -> dict[str, Any]:
    return json.loads((_PG_FIXTURES / name).read_text(encoding="utf-8"))


class _RaisingPipeline:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def begin(self, *, model_path: str, requested_surface: str | None = None):
        del model_path, requested_surface
        raise self._exc


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"test-key"}))
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_tier_required_maps_to_http_403_with_fields() -> None:
    app.dependency_overrides[get_routing_pipeline] = lambda: _RaisingPipeline(
        TierRequiredError(
            model="gemini-4-argon",
            tier="Pro",
            required_tier="Pro",
            current_tier="Free",
        )
    )
    async with _client() as client:
        response = await client.post(
            "/v1beta/gemini-4-argon:generateContent",
            headers=_HEADERS,
            json=_BODY,
        )
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["status"] == "PERMISSION_DENIED"
    assert error["tier"] == "Pro"
    assert error["required_tier"] == "Pro"
    assert error["current_tier"] == "Free"
    assert error["model"] == "gemini-4-argon"


@pytest.mark.asyncio
async def test_capacity_maps_to_http_503_with_code() -> None:
    app.dependency_overrides[get_routing_pipeline] = lambda: _RaisingPipeline(
        SurfaceCapacityError(surface="chat")
    )
    async with _client() as client:
        response = await client.post(
            "/v1beta/gemini-3.7-flash:generateContent",
            headers=_HEADERS,
            json=_BODY,
        )
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["status"] == "UNAVAILABLE"
    assert error["type"] == "surface_quota_exceeded"


@pytest.mark.asyncio
async def test_synthetic_required_non_stream_maps_to_http_502_json() -> None:
    app.dependency_overrides[get_routing_pipeline] = lambda: _RaisingPipeline(
        SyntheticRequiredError("synthetic tool call required")
    )
    async with _client() as client:
        response = await client.post(
            "/v1beta/gemini-3.7-flash:generateContent",
            headers=_HEADERS,
            json=_BODY,
        )
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == 502
    assert error["status"] == "UNAVAILABLE"
    assert "synthetic tool call required" in error["message"]


@pytest.mark.asyncio
async def test_synthetic_required_pre_stream_maps_to_http_502_json() -> None:
    app.dependency_overrides[get_routing_pipeline] = lambda: _RaisingPipeline(
        SyntheticRequiredError("synthetic tool call required")
    )
    async with _client() as client:
        response = await client.post(
            "/v1beta/gemini-3.7-flash:streamGenerateContent",
            headers=_HEADERS,
            json=_BODY,
        )
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == 502
    assert error["status"] == "UNAVAILABLE"
    assert "synthetic tool call required" in error["message"]


@pytest.mark.asyncio
async def test_non_streaming_success_uses_pipeline_instance() -> None:
    fake_client = MagicMock()
    fake_client.generate_content = AsyncMock(
        return_value=ModelOutput(
            candidates=[Candidate(text="hello from instance")],
            usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        )
    )
    attempt = make_attempt(fake_client)
    pipeline = ScriptedPipeline([attempt], context=make_context())
    app.dependency_overrides[get_routing_pipeline] = lambda: pipeline

    async with _client() as client:
        response = await client.post(
            "/v1beta/gemini-3.7-flash:generateContent",
            headers=_HEADERS,
            json=_BODY,
        )
    assert response.status_code == 200
    data = response.json()
    assert data["candidates"][0]["content"]["parts"][0]["text"] == "hello from instance"
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True


@pytest.mark.asyncio
async def test_streaming_success_uses_pipeline_instance() -> None:
    async def fake_stream(*_args, **_kwargs):
        yield ("body", "streamed")
        yield ("finish_reason", 1)
        yield ("usage", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    fake_client = MagicMock()
    fake_client.stream_generate_content = fake_stream
    attempt = make_attempt(fake_client)
    pipeline = ScriptedPipeline([attempt], context=make_context())
    app.dependency_overrides[get_routing_pipeline] = lambda: pipeline

    async with _client() as client:
        response = await client.post(
            "/v1beta/gemini-3.7-flash:streamGenerateContent",
            headers=_HEADERS,
            json=_BODY,
        )
    assert response.status_code == 200
    assert "streamed" in response.text
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True


@pytest.mark.asyncio
async def test_native_stream_alt_sse_emits_complete_frames() -> None:
    async def fake_stream(*_args, **_kwargs):
        yield ("response_id", "resp-stream-1")
        yield ("body", "hello")
        yield ("body", " world")
        yield ("finish_reason", 1)
        yield (
            "usage",
            {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        )

    fake_client = MagicMock()
    fake_client.stream_generate_content = fake_stream
    attempt = make_attempt(fake_client)
    pipeline = ScriptedPipeline([attempt], context=make_context())
    app.dependency_overrides[get_routing_pipeline] = lambda: pipeline

    async with _client() as client:
        response = await client.post(
            "/v1beta/models/gemini-3.8-flash:streamGenerateContent?alt=sse",
            headers=_HEADERS,
            json=_BODY,
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert frames
    text = "".join(
        str(part.get("text") or "")
        for frame in frames
        for candidate in frame.get("candidates", [])
        for part in (candidate.get("content") or {}).get("parts", [])
    )
    assert "hello world" in text
    assert any(
        candidate.get("finishReason") == "STOP"
        for frame in frames
        for candidate in frame.get("candidates", [])
    )
    assert any("usageMetadata" in frame for frame in frames)
    assert pipeline.attempts == []
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True


@pytest.mark.asyncio
async def test_native_stream_skips_empty_first_body_chunk() -> None:
    async def fake_stream(*_args, **_kwargs):
        yield ("body", "")
        yield ("body", "after-empty")
        yield ("finish_reason", 1)
        yield ("usage", {"prompt_tokens": 1, "completion_tokens": 1})

    fake_client = MagicMock()
    fake_client.stream_generate_content = fake_stream
    attempt = make_attempt(fake_client)
    pipeline = ScriptedPipeline([attempt], context=make_context())
    app.dependency_overrides[get_routing_pipeline] = lambda: pipeline

    async with _client() as client:
        response = await client.post(
            "/v1beta/models/gemini-3.8-flash:streamGenerateContent?alt=sse",
            headers=_HEADERS,
            json=_BODY,
        )

    assert response.status_code == 200
    assert "after-empty" in response.text
    assert "finishReason" in response.text


@pytest.mark.asyncio
async def test_native_stream_without_frames_returns_real_http_error(
    monkeypatch,
) -> None:
    from aistudio_api.application import generation_service
    from aistudio_api.application.generation_service import StreamEvent

    async def empty_events(**_kwargs):
        yield StreamEvent(kind="routing_wait", payload="resolving-instance")

    monkeypatch.setattr(generation_service, "stream_generation", empty_events)
    app.dependency_overrides[get_routing_pipeline] = lambda: object()

    async with _client() as client:
        response = await client.post(
            "/v1beta/models/gemini-3.8-flash:streamGenerateContent?alt=sse",
            headers=_HEADERS,
            json=_BODY,
        )

    assert response.status_code == 502
    assert "text/event-stream" not in response.headers["content-type"]
    error = response.json()["error"]
    assert error["code"] == 502
    assert "no stream events" in error["message"]



@pytest.mark.asyncio
async def test_pg02_prestream_failure_returns_real_http_status_not_sse() -> None:
    fixture = _pg_fixture("f02_prestream_400.json")
    upstream = fixture["upstream"]
    expected = fixture["expected"]
    assert isinstance(upstream, dict)
    assert isinstance(expected, dict)
    error_body = upstream["error"]
    assert isinstance(error_body, dict)

    async def failing_stream(*_args, **_kwargs):
        raise RequestError(int(upstream["status"]), str(error_body["message"]))
        yield ("body", "never")  # pragma: no cover

    fake_client = MagicMock()
    fake_client.stream_generate_content = failing_stream
    attempt = make_attempt(fake_client)
    pipeline = ScriptedPipeline([attempt], context=make_context())
    app.dependency_overrides[get_routing_pipeline] = lambda: pipeline

    async with _client() as client:
        response = await client.post(
            "/v1beta/gemini-3.7-flash:streamGenerateContent",
            headers=_HEADERS,
            json=_BODY,
        )

    assert response.status_code == expected["http_status"]
    content_type = response.headers["content-type"]
    assert content_type.startswith(expected["content_type"])
    assert "text/event-stream" not in content_type
    assert expected["sse"] is False
    error = response.json()["error"]
    assert error["code"] == expected["error_code"]
    assert error["status"] == expected["error_status"]
    assert str(error_body["message"]) in error["message"]
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True


def _account(account_id: str, tier: str) -> AccountMeta:
    return AccountMeta(
        id=account_id,
        name=account_id,
        email=None,
        created_at="2026-01-01T00:00:00+00:00",
        tier=tier,
    )


class _RebindStore:
    def __init__(self, accounts: list[AccountMeta]) -> None:
        self._accounts = {account.id: account for account in accounts}
        self.active_account_id: str | None = None

    def list_accounts(self) -> list[AccountMeta]:
        return list(self._accounts.values())

    def get_account(self, account_id: str) -> AccountMeta | None:
        return self._accounts.get(account_id)

    def get_auth_path_optional(
        self, account_id: str, *, require_exists: bool = False
    ):
        del require_exists
        if account_id not in self._accounts:
            return None
        return Path("data") / "accounts" / account_id / "auth.json"

    def get_active_account(self) -> AccountMeta | None:
        if self.active_account_id is None:
            return None
        return self._accounts.get(self.active_account_id)

    def set_active_account(self, account_id: str) -> None:
        self.active_account_id = account_id


class _RebindClient:
    def __init__(self, session, *, account_id: str, tier: str, surface: str) -> None:
        self._session = session
        self.account_id = account_id
        self.tier = tier
        self.surface = surface
        self.clear_calls = 0

    def clear_templates(self) -> bool:
        self.clear_calls += 1
        return True

    async def generate_content(self, **kwargs):
        assert self._session.generate is not None
        return await self._session.generate(
            account_id=self.account_id, model=kwargs.get("model")
        )

    async def stream_generate_content(self, **kwargs):
        assert self._session.stream is not None
        async for event in self._session.stream(
            account_id=self.account_id, model=kwargs.get("model")
        ):
            yield event

    async def count_tokens(self, *, packed_body):
        del packed_body
        return 1


def _rebind_setup(
    monkeypatch,
    *,
    extra_free_account: bool,
    generate_a,
    generate_b=None,
):
    accounts = [_account("free-a", "Free")]
    if extra_free_account:
        accounts.append(_account("free-b", "Free"))
    accounts.append(_account("pro-1", "Pro"))
    store = _RebindStore(accounts)
    rotator = AccountRotator(store)  # type: ignore[arg-type]
    manager = RebindFakeManager(
        [
            make_instance("inst-1", account_id="free-a", tier="Free", number=1),
            make_instance("inst-2", account_id="pro-1", tier="Pro", number=2),
        ],
        sessions={
            "inst-1": FakeSession(account_id="free-a", generate=generate_a),
        },
        account_sessions=(
            {"free-b": FakeSession(account_id="free-b", generate=generate_b)}
            if generate_b is not None
            else {}
        ),
    )
    manager.account_tiers["free-b"] = "Free"
    clients: list[_RebindClient] = []

    def factory(session, *, account_id, tier, surface):
        client = _RebindClient(
            session, account_id=account_id, tier=tier, surface=surface
        )
        clients.append(client)
        return client

    monkeypatch.setattr(
        routing_pipeline_module, "InstanceRequestClient", factory
    )
    pipeline = RoutingPipeline(
        instance_manager=manager,
        account_store=store,  # type: ignore[arg-type]
        rotator=rotator,
        routing_settings_provider=lambda: {"max_switches": 2},
    )
    app.dependency_overrides[get_routing_pipeline] = lambda: pipeline
    return manager, rotator, clients, pipeline


@pytest.mark.asyncio
async def test_429_single_free_slot_rebinds_to_alternate_free_account(
    monkeypatch,
) -> None:
    free_a_calls = 0
    free_b_calls = 0

    async def generate_a(**_kwargs):
        nonlocal free_a_calls
        free_a_calls += 1
        raise UsageLimitExceeded("429 from free-a")

    async def generate_b(**_kwargs):
        nonlocal free_b_calls
        free_b_calls += 1
        return ModelOutput(
            candidates=[Candidate(text="rebound ok")],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    manager, rotator, clients, _pipeline = _rebind_setup(
        monkeypatch,
        extra_free_account=True,
        generate_a=generate_a,
        generate_b=generate_b,
    )

    async with _client() as client:
        response = await client.post(
            "/v1beta/gemini-3.8-flash:generateContent",
            headers=_HEADERS,
            json=_BODY,
        )

    assert response.status_code == 200
    data = response.json()
    assert data["candidates"][0]["content"]["parts"][0]["text"] == "rebound ok"
    assert manager.reassign_calls == [("inst-1", "free-b")]
    # 首次 429 即切号：free-a 只被调用一次，绝不同号二次撞 429。
    assert free_a_calls == 1
    assert free_b_calls == 1
    assert manager.begin_calls == ["inst-1", "inst-1"]
    assert manager.active_requests == 0
    assert manager.last_handles["inst-1"].released is True
    assert clients[0].account_id == "free-a"
    assert clients[0].clear_calls >= 1
    assert clients[-1].account_id == "free-b"
    assert not rotator.is_account_available("free-a", model="gemini-3.8-flash")
    assert rotator.is_account_available("free-b", model="gemini-3.8-flash")


@pytest.mark.asyncio
async def test_429_single_free_slot_without_alternate_returns_503(
    monkeypatch,
) -> None:
    async def generate_a(**_kwargs):
        raise UsageLimitExceeded("429 from free-a")

    manager, rotator, _clients, _pipeline = _rebind_setup(
        monkeypatch,
        extra_free_account=False,
        generate_a=generate_a,
    )

    async with _client() as client:
        response = await client.post(
            "/v1beta/gemini-3.8-flash:generateContent",
            headers=_HEADERS,
            json=_BODY,
        )

    assert response.status_code == 503
    assert manager.reassign_calls == []
    assert manager.active_requests == 0
    assert not rotator.is_account_available("free-a", model="gemini-3.8-flash")
    error = response.json()["error"]
    assert error["code"] == 503
