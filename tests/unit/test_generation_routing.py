"""Behavior contracts for generation-service routing integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.api.schemas import (
    GeminiContent,
    GeminiGenerateContentRequest,
    GeminiPart,
)
from aistudio_api.application import generation_service
from aistudio_api.application.routing_safety import RoutingBudget
from aistudio_api.application.surface_orchestrator import SurfacePolicyError
from aistudio_api.domain.errors import AuthError, RequestError
from aistudio_api.domain.models import Candidate, ModelOutput
from aistudio_api.infrastructure.gateway.client import AIStudioClient


@dataclass
class RouteCtx:
    clean_model: str = "gemini-3.8-flash"
    surface: str = "chat"
    retry_same: bool = False
    budget: RoutingBudget = field(default_factory=lambda: RoutingBudget(2, 2))


class FakePipe:
    def __init__(self, ctx: RouteCtx, *, candidates: list[str] | None = None):
        self.ctx = ctx
        self.ids = candidates or ["acc_a"]
        self.next_calls = 0
        self.land_calls = 0
        self.switches: list[str] = []
        self.retries: list[str] = []
        self.success = False
        self.released: list[str] = []

    def next_candidate(self, ctx, failed_account_id=None):
        self.next_calls += 1
        index = min(self.next_calls - 1, len(self.ids) - 1)
        return SimpleNamespace(account_id=self.ids[index])

    async def acquire(self, ctx, candidate, require_idle=True):
        self.land_calls += 1
        return SimpleNamespace(
            account_id=candidate.account_id,
            realm_id=f"slot-{candidate.account_id}",
            is_borrow=False,
        )

    async def land(self, ctx, candidate, require_idle=True):
        return await self.acquire(ctx, candidate, require_idle=require_idle)

    async def release(self, ctx, lease):
        if lease is not None:
            self.released.append(lease.realm_id)

    def record_retry(self, ctx, reason):
        self.retries.append(reason)
        ctx.budget.record_retry()
        ctx.retry_same = True

    def record_switch(self, ctx, reason):
        if not ctx.budget.record_switch():
            raise SurfacePolicyError(
                "surface_switch_exhausted", message="cap", advice="wait"
            )
        self.switches.append(reason)

    def record_success(self, ctx):
        self.success = True


class FakeClient:
    def __init__(self, outcomes: list[Any], surface_log: list[str] | None = None):
        self.outcomes = outcomes
        self.surface_log = surface_log if surface_log is not None else []
        self._session = None

    def clear_templates(self) -> None:
        pass

    async def generate_content(self, **kwargs):
        self.surface_log.append(str(kwargs.get("surface")))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeStreamClient(FakeClient):
    async def stream_generate_content(self, **kwargs):
        self.surface_log.append(str(kwargs.get("surface")))
        yield ("body", "hello")


def _request() -> GeminiGenerateContentRequest:
    return GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hi")])]
    )


def _output() -> ModelOutput:
    return ModelOutput(
        candidates=[Candidate(text="hello")],
        model="gemini-3.8-flash",
        usage={"prompt_tokens": 1, "completion_tokens": 1},
    )


def _install(monkeypatch, pipe: FakePipe, ctx: RouteCtx) -> None:
    monkeypatch.setattr(
        generation_service, "_begin_route", lambda model, fmt: (pipe, ctx)
    )
    monkeypatch.setattr(
        generation_service, "record_rotator_event", lambda *a, **k: None
    )
    monkeypatch.setattr(
        generation_service.runtime_state, "record", lambda *a, **k: None
    )


async def test_generate_uses_pipeline_surface_and_marks_success(monkeypatch) -> None:
    ctx = RouteCtx(surface="chat")
    pipe = FakePipe(ctx)
    client = FakeClient([_output()], surface_log=[])
    _install(monkeypatch, pipe, ctx)
    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        generation_service,
        "record_rotator_event",
        lambda *_args, **kwargs: events.append(kwargs),
    )

    outcome = await generation_service.generate(
        model_path="gemini-3.8-flash",
        req=_request(),
        client=cast("AIStudioClient", client),
        atg_factory=cast(Any, lambda: SimpleNamespace(enabled=False)),
    )

    assert outcome.surface == "chat"
    assert client.surface_log == ["chat"]
    assert pipe.next_calls == 1
    assert pipe.land_calls == 1
    assert pipe.success is True
    assert pipe.released == ["slot-acc_a"]
    assert events[-1]["account_id"] == "acc_a"


async def test_capacity_defer_waits_then_retries(monkeypatch) -> None:
    ctx = SimpleNamespace(
        budget=RoutingBudget(max_switches=2, max_defers=2),
        attempted=set(),
        request_id="route-defer",
    )
    candidate = SimpleNamespace(account_id="acc_a")

    class CapacityPipe:
        def __init__(self) -> None:
            self.acquire_calls = 0

        def next_candidate(self, _ctx, _failed_account_id=None):
            return None

        async def acquire(self, _ctx, _candidate):
            self.acquire_calls += 1
            if self.acquire_calls == 1:
                raise SurfacePolicyError(
                    "surface_quota_exceeded", message="full", advice="wait"
                )
            return SimpleNamespace(
                account_id="acc_a", realm_id="slot-a", is_borrow=False
            )

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    pipe = CapacityPipe()
    _, lease = await generation_service._acquire_candidate_lease(
        cast("Any", pipe), cast("Any", ctx), candidate
    )

    assert lease.realm_id == "slot-a"
    assert pipe.acquire_calls == 2
    assert ctx.budget.snapshot()["defers"] == 1


async def test_generate_retries_same_candidate_without_new_switch(monkeypatch) -> None:
    ctx = RouteCtx()
    pipe = FakePipe(ctx)
    client = FakeClient([RequestError(204, "empty"), _output()])
    _install(monkeypatch, pipe, ctx)

    await generation_service.generate(
        model_path="gemini-3.8-flash",
        req=_request(),
        client=cast("AIStudioClient", client),
        atg_factory=cast(Any, lambda: SimpleNamespace(enabled=False)),
    )

    assert pipe.retries == ["empty_response_204"]
    assert pipe.switches == []
    assert pipe.next_calls == 1
    assert pipe.land_calls == 1


async def test_generate_switch_counts_against_budget(monkeypatch) -> None:
    ctx = RouteCtx()
    pipe = FakePipe(ctx, candidates=["acc_a", "acc_b"])
    client = FakeClient([AuthError("403"), _output()])
    _install(monkeypatch, pipe, ctx)

    await generation_service.generate(
        model_path="gemini-3.8-flash",
        req=_request(),
        client=cast("AIStudioClient", client),
        atg_factory=cast(Any, lambda: SimpleNamespace(enabled=False)),
    )

    assert pipe.switches == ["auth_error"]
    assert pipe.next_calls == 2
    assert pipe.success is True


async def test_generate_raises_when_switch_budget_exhausted(monkeypatch) -> None:
    ctx = RouteCtx(budget=RoutingBudget(max_switches=0))
    pipe = FakePipe(ctx, candidates=["acc_a", "acc_b"])
    client = FakeClient([AuthError("403"), _output()])
    _install(monkeypatch, pipe, ctx)

    with pytest.raises(SurfacePolicyError) as caught:
        await generation_service.generate(
            model_path="gemini-3.8-flash",
            req=_request(),
            client=cast("AIStudioClient", client),
            atg_factory=cast(Any, lambda: SimpleNamespace(enabled=False)),
        )
    assert caught.value.code == "surface_switch_exhausted"
    assert pipe.released == ["slot-acc_a"]


async def test_generate_cancel_releases_lease(monkeypatch) -> None:
    ctx = RouteCtx()
    pipe = FakePipe(ctx)
    client = FakeClient([asyncio.CancelledError()])
    _install(monkeypatch, pipe, ctx)

    with pytest.raises(asyncio.CancelledError):
        await generation_service.generate(
            model_path="gemini-3.8-flash",
            req=_request(),
            client=cast("AIStudioClient", client),
            atg_factory=cast(Any, lambda: SimpleNamespace(enabled=False)),
        )

    assert pipe.released == ["slot-acc_a"]


async def test_generate_skips_capacity_denied_candidate(monkeypatch) -> None:
    ctx = RouteCtx()
    pipe = FakePipe(ctx, candidates=["acc_a", "acc_b", "acc_c"])

    async def acquire(ctx, candidate, require_idle=True):
        if candidate.account_id == "acc_b":
            raise SurfacePolicyError(
                "surface_quota_exceeded", message="capacity", advice="wait"
            )
        return await FakePipe.acquire(pipe, ctx, candidate, require_idle=require_idle)

    monkeypatch.setattr(pipe, "acquire", acquire)
    client = FakeClient([AuthError("403"), _output()])
    _install(monkeypatch, pipe, ctx)

    await generation_service.generate(
        model_path="gemini-3.8-flash",
        req=_request(),
        client=cast("AIStudioClient", client),
        atg_factory=cast(Any, lambda: SimpleNamespace(enabled=False)),
    )

    assert pipe.next_calls == 3
    assert pipe.land_calls == 2
    assert pipe.success is True


async def test_stream_cancel_releases_lease(monkeypatch) -> None:
    ctx = RouteCtx()
    pipe = FakePipe(ctx)
    client = FakeStreamClient([])
    _install(monkeypatch, pipe, ctx)

    stream = generation_service.stream_generation(
        model_path="gemini-3.8-flash",
        req=_request(),
        client=cast("AIStudioClient", client),
        atg_factory=cast(Any, lambda: SimpleNamespace(enabled=False)),
    )
    first = await anext(stream)
    assert first.kind == "routing_wait"
    second = await anext(stream)
    assert second.kind == "body"
    assert pipe.released == []

    await cast("Any", stream).aclose()
    assert pipe.released == ["slot-acc_a"]


def test_routing_wait_renders_as_sse_comment() -> None:
    from aistudio_api.application.api_service_gemini import _render_stream_event
    from aistudio_api.application.format_converters.claude_response import (
        ClaudeStreamRenderer,
    )
    from aistudio_api.application.format_converters.openai_response import (
        OpenAIStreamRenderer,
    )

    event = generation_service.StreamEvent(kind="routing_wait", payload="resolving")
    assert _render_stream_event(event) == ": resolving-instance\n\n"
    assert OpenAIStreamRenderer("m").render(event) == ": resolving-instance\n\n"
    assert ClaudeStreamRenderer("m").render(event) == ": resolving-instance\n\n"
