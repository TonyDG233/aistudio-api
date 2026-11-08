"""Step C4 client/attempt/countTokens ledger and daily window contracts."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, cast

import httpx
import pytest

from aistudio_api.api import app as app_module
from aistudio_api.api.state import (
    SHANGHAI_TZ,
    ClientStats,
    CountTokensStats,
    ModelStatsItem,
    RuntimeState,
    build_stats_payload,
    daily_keys,
)
from aistudio_api.application import generation_service, token_counter
from aistudio_api.application.contracts.gemini import GeminiGenerateContentRequest
from aistudio_api.application.routing_pipeline import RoutingPipeline
from aistudio_api.domain.errors import (
    AuthError,
    RequestError,
    SurfaceCapacityError,
    TierRequiredError,
    UsageLimitExceeded,
)
from aistudio_api.domain.models import Candidate, ModelOutput
from tests.unit.routing_fakes import (
    FakeManager,
    ScriptedPipeline,
    make_attempt,
    make_context,
    make_instance,
)

MODEL = "gemini-3.7-flash"


def _request() -> GeminiGenerateContentRequest:
    return GeminiGenerateContentRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "hello"}]}]}
    )


def _output(text: str = "ok") -> ModelOutput:
    return ModelOutput(
        candidates=[Candidate(text=text)],
        usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        response_id="resp-1",
    )


def _state(tmp_path, monkeypatch) -> RuntimeState:
    stats_file = tmp_path / "data" / "stats.json"
    monkeypatch.setenv("AISTUDIO_STATS_FILE", str(stats_file))
    monkeypatch.setenv("AISTUDIO_PERSIST_STATS", "1")
    return RuntimeState()


class _Client:
    def __init__(self, outcomes: list[object], stream: bool = False) -> None:
        self.outcomes = outcomes
        self.stream = stream
        self.calls = 0

    async def generate_content(self, **_kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def stream_generate_content(self, **_kwargs):
        async def _gen():
            self.calls += 1
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            for event in outcome:  # type: ignore[union-attr]
                yield event

        return _gen()

    async def count_tokens(self, **_kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def clear_templates(self) -> None:
        return None


@pytest.mark.asyncio
async def test_generate_429_retry_records_single_rescued_client_success():
    client = _Client([UsageLimitExceeded("429"), _output("recovered")])
    attempt = make_attempt(client)
    pipeline = ScriptedPipeline(
        [attempt], actions=["retry_same"], context=make_context()
    )

    outcome = await generation_service.generate(
        model_path=MODEL, req=_request(), pipeline=pipeline
    )

    assert outcome.output.text == "recovered"
    assert pipeline.client_terminals == [(MODEL, "success", True)]
    assert len(pipeline.successes) == 1


@pytest.mark.asyncio
async def test_generate_switch_then_success_records_one_client_success():
    first = _Client([UsageLimitExceeded("429")])
    second = _Client([_output("second")])
    pipeline = ScriptedPipeline(
        [make_attempt(first, instance_id="inst-1"), make_attempt(second, instance_id="inst-2")],
        actions=["switch"],
        context=make_context(),
    )

    await generation_service.generate(
        model_path=MODEL, req=_request(), pipeline=pipeline
    )

    assert pipeline.client_terminals == [(MODEL, "success", True)]


@pytest.mark.asyncio
async def test_generate_terminal_failure_records_one_client_error():
    client = _Client([RuntimeError("boom")])
    pipeline = ScriptedPipeline([make_attempt(client)], context=make_context())

    with pytest.raises(RuntimeError):
        await generation_service.generate(
            model_path=MODEL, req=_request(), pipeline=pipeline
        )

    assert pipeline.client_terminals == [(MODEL, "errors", False)]


@pytest.mark.asyncio
async def test_generate_429_exhaustion_records_client_rate_limited():
    client = _Client([UsageLimitExceeded("429")])
    pipeline = ScriptedPipeline([make_attempt(client)], context=make_context())
    pipeline.actions = ["raise"]

    with pytest.raises(UsageLimitExceeded):
        await generation_service.generate(
            model_path=MODEL, req=_request(), pipeline=pipeline
        )

    assert pipeline.client_terminals == [(MODEL, "rate_limited", False)]


@pytest.mark.asyncio
async def test_generate_capacity_failure_records_client_error_before_attempts():
    class _CapacityPipeline(ScriptedPipeline):
        async def begin(self, *, model_path: str, requested_surface=None):
            del model_path, requested_surface
            raise SurfaceCapacityError(surface="chat")

    pipeline = _CapacityPipeline([])

    with pytest.raises(SurfaceCapacityError):
        await generation_service.generate(
            model_path=MODEL, req=_request(), pipeline=pipeline
        )

    assert pipeline.client_terminals == [(MODEL, "errors", False)]


@pytest.mark.asyncio
async def test_stream_cancel_records_client_aborted():
    async def _stream(**_kwargs):
        yield ("body", "first")
        await __import__("asyncio").Event().wait()

    client = _Client([])
    client.stream_generate_content = _stream  # type: ignore[assignment]
    attempt = make_attempt(client)
    pipeline = ScriptedPipeline([attempt], context=make_context())

    iterator = generation_service.stream_generation(
        model_path=MODEL, req=_request(), pipeline=pipeline
    )
    await iterator.__anext__()
    await iterator.__anext__()
    await iterator.aclose()

    assert pipeline.client_terminals == [(MODEL, "aborted", False)]


@pytest.mark.asyncio
async def test_stream_success_records_one_client_success():
    async def _stream(**_kwargs):
        yield ("body", "answer")
        yield ("finish_reason", 1)
        yield ("usage", {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3})

    client = _Client([])
    client.stream_generate_content = _stream  # type: ignore[assignment]
    pipeline = ScriptedPipeline(
        [make_attempt(client)], context=make_context()
    )

    [_event async for _event in generation_service.stream_generation(
        model_path=MODEL, req=_request(), pipeline=pipeline
    )]

    assert pipeline.client_terminals == [(MODEL, "success", False)]


def test_real_pipeline_attempt_and_client_ledgers_stay_separate():
    attempts: list[tuple[str, str]] = []
    clients: list[tuple[str, str, bool]] = []

    def _attempt_recorder(model: str, result: str, **_kwargs: object) -> None:
        attempts.append((model, result))

    def _client_recorder(
        model: str, result: str, *, rescued: bool = False, **_kwargs: object
    ) -> None:
        clients.append((model, result, rescued))

    pipeline = RoutingPipeline(
        instance_manager=FakeManager(),
        stats_recorder=_attempt_recorder,
        client_recorder=_client_recorder,
    )
    context = make_context([make_instance("inst-1", account_id="acc-1")])
    attempt = make_attempt(object())

    pipeline._record_error("rate_limited", context, attempt)
    pipeline.record_success(context, attempt, usage={}, finish_reason="STOP")
    pipeline.record_client(context.route.model, "success", rescued=True)

    assert attempts == [(MODEL, "rate_limited"), (MODEL, "success")]
    assert clients == [(MODEL, "success", True)]


@pytest.mark.asyncio
async def test_204_retry_records_failed_attempt_then_success_attempt():
    attempts: list[str] = []

    def _attempt_recorder(model: str, result: str, **_kwargs: object) -> None:
        assert model == MODEL
        attempts.append(result)

    pipeline = RoutingPipeline(
        instance_manager=FakeManager(),
        stats_recorder=_attempt_recorder,
    )
    context = make_context([make_instance("inst-1", account_id="acc-1")])
    attempt = make_attempt(_Client([]))

    action = await pipeline.handle_error(
        context,
        attempt,
        RequestError(204, "no content"),
        has_yielded_data=False,
    )

    assert action == "retry_same"
    assert attempts == ["errors"]
    assert attempt.same_retries == 1

    pipeline.record_success(context, attempt, usage={}, finish_reason="STOP")
    assert attempts == ["errors", "success"]


@pytest.mark.asyncio
async def test_transient_404_retry_records_failed_attempt():
    attempts: list[str] = []

    def _attempt_recorder(model: str, result: str, **_kwargs: object) -> None:
        assert model == MODEL
        attempts.append(result)

    pipeline = RoutingPipeline(
        instance_manager=FakeManager(),
        stats_recorder=_attempt_recorder,
    )
    context = make_context([make_instance("inst-1", account_id="acc-1")])
    attempt = make_attempt(_Client([]))
    transient = RequestError(
        404,
        "Ambiguous request for service '' and method "
        "'/GenerativeService.GenerateContent'.",
    )

    action = await pipeline.handle_error(
        context, attempt, transient, has_yielded_data=False
    )

    assert action == "retry_same"
    assert attempts == ["errors"]


@pytest.mark.asyncio
async def test_auth_error_on_tierless_candidate_records_attempt():
    attempts: list[str] = []

    def _attempt_recorder(model: str, result: str, **_kwargs: object) -> None:
        assert model == MODEL
        attempts.append(result)

    pipeline = RoutingPipeline(
        instance_manager=FakeManager(),
        stats_recorder=_attempt_recorder,
    )
    context = make_context(
        [make_instance("inst-1", account_id="acc-1", tier="Free")],
        paid=True,
        required_tiers=("Pro",),
    )
    attempt = make_attempt(object(), tier="Free")

    with pytest.raises(TierRequiredError):
        await pipeline.handle_error(
            context,
            attempt,
            AuthError("HTTP 403: WAA denied"),
            has_yielded_data=False,
        )

    assert attempts == ["errors"]


@pytest.mark.asyncio
async def test_generate_204_retry_success_keeps_single_client_success():
    client = _Client([RequestError(204, "no content"), _output("recovered")])
    pipeline = ScriptedPipeline(
        [make_attempt(client)],
        actions=["retry_same"],
        context=make_context(),
    )

    await generation_service.generate(
        model_path=MODEL, req=_request(), pipeline=pipeline
    )

    assert pipeline.client_terminals == [(MODEL, "success", True)]


def test_runtime_state_client_ledger_never_touches_attempts(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)

    state.record_client(MODEL, "success", rescued=True)
    state.record_client(MODEL, "rate_limited")
    state.record_client(MODEL, "aborted")

    item = state.model_stats[MODEL]
    assert item.requests == 0
    assert item.client.to_dict() == {
        "requests": 3,
        "success": 1,
        "errors": 0,
        "aborted": 1,
        "rate_limited": 1,
        "rescued_requests": 1,
        "success_rate": 33.33,
    }
    daily = state.daily_stats[datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")]
    assert daily[MODEL].client.requests == 3


def test_stats_payload_zero_denominator_success_rate_is_null():
    payload = build_stats_payload(
        model_stats={},
        daily_stats={},
        count_tokens=CountTokensStats(),
        days=7,
    )
    assert cast("dict[str, Any]", payload["client"])["success_rate"] is None
    assert cast("dict[str, Any]", payload["attempt"])["success_rate"] is None
    assert len(cast("list[dict[str, Any]]", payload["daily"])) == 7


def test_build_stats_payload_daily_is_continuous_and_model_scoped():
    today = datetime.now(SHANGHAI_TZ).date()
    keys = daily_keys(5, today=today)
    daily = {
        keys[-1]: {
            MODEL: ModelStatsItem(
                requests=3,
                success=2,
                rate_limited=1,
                prompt_tokens=10,
                cached_tokens=4,
                completion_tokens=6,
                reasoning_tokens=2,
                total_tokens=16,
                client=ClientStats(
                    requests=2,
                    success=2,
                    rescued_requests=1,
                ),
            )
        }
    }

    payload = build_stats_payload(
        model_stats=daily[keys[-1]],
        daily_stats=daily,
        count_tokens=CountTokensStats(requests=2, success=2, input_tokens=9),
        days=5,
    )
    daily_payload = cast("list[dict[str, Any]]", payload["daily"])
    dates = [entry["date"] for entry in daily_payload]
    assert dates == keys
    last = daily_payload[-1]
    assert last["tokens"] == {
        "prompt_tokens": 10,
        "cached_tokens": 4,
        "completion_tokens": 6,
        "reasoning_tokens": 2,
        "total_tokens": 16,
    }
    assert set(last["by_model"]) == {MODEL}
    client_stats = cast("dict[str, Any]", payload["client"])
    assert client_stats["requests"] == 2
    assert client_stats["rescued_requests"] == 1
    assert cast("dict[str, Any]", payload["count_tokens"])["input_tokens"] == 9


def test_load_prunes_daily_beyond_90_days_and_backs_up(tmp_path, monkeypatch):
    stats_file = tmp_path / "data" / "stats.json"
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now(SHANGHAI_TZ).date()
    old = (today - timedelta(days=120)).isoformat()
    recent = (today - timedelta(days=10)).isoformat()
    stats_file.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "models": {MODEL: {"requests": 2}},
                "daily": {
                    old: {MODEL: {"requests": 1}},
                    recent: {MODEL: {"requests": 1}},
                },
                "count_tokens": {"requests": 4, "success": 3, "errors": 1},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AISTUDIO_STATS_FILE", str(stats_file))
    monkeypatch.setenv("AISTUDIO_PERSIST_STATS", "1")

    state = RuntimeState()

    assert old not in state.daily_stats
    assert recent in state.daily_stats
    assert state.count_tokens.requests == 4
    assert stats_file.with_name("stats.json.bak").is_file()
    rewritten = json.loads(stats_file.read_text(encoding="utf-8"))
    assert old not in rewritten["daily"]


@pytest.mark.asyncio
async def test_stats_route_days_window(monkeypatch):
    from aistudio_api.api.state import runtime_state

    today = datetime.now(SHANGHAI_TZ).date()
    keys = daily_keys(7, today=today)
    monkeypatch.setattr(runtime_state, "ui_debug", False)
    monkeypatch.setattr(runtime_state, "ui_sandbox", None)
    monkeypatch.setattr(runtime_state, "rotator", None)
    monkeypatch.setattr(runtime_state, "account_service", None)
    monkeypatch.setattr(
        runtime_state,
        "model_stats",
        {MODEL: ModelStatsItem(requests=5, success=4)},
    )
    monkeypatch.setattr(
        runtime_state,
        "daily_stats",
        {keys[-1]: {MODEL: ModelStatsItem(requests=2, success=2)}},
    )
    monkeypatch.setattr(runtime_state, "count_tokens", CountTokensStats(requests=1))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        week = (await client.get("/stats?days=7")).json()
        all_time = (await client.get("/stats")).json()

    assert [entry["date"] for entry in week["daily"]] == keys
    assert week["attempt"]["attempts"] == 2
    assert week["client"]["requests"] == 0
    assert week["count_tokens"]["requests"] == 1
    assert all_time["attempt"]["attempts"] == 5
    assert len(all_time["daily"]) == 90
    assert "models" not in all_time["models"]


@pytest.mark.asyncio
async def test_count_tokens_records_isolated_ledger_without_generation_attempt():
    client = _Client([42])

    class _Pipeline(ScriptedPipeline):
        def __init__(self) -> None:
            super().__init__([make_attempt(client)], context=make_context())

    pipeline = _Pipeline()

    outcome = await token_counter.count_tokens(
        model_path=f"chat/{MODEL}", req=_request(), pipeline=pipeline
    )

    assert outcome.total_tokens == 42
    assert pipeline.successes == []
    assert pipeline.count_terminals == [(MODEL, "success", 42)]
    assert pipeline.context.record_attempts is False


@pytest.mark.asyncio
async def test_count_tokens_failure_records_count_error_only():
    client = _Client([RuntimeError("count exploded")])
    pipeline = ScriptedPipeline([make_attempt(client)], context=make_context())

    with pytest.raises(RuntimeError):
        await token_counter.count_tokens(
            model_path=MODEL, req=_request(), pipeline=pipeline
        )

    assert pipeline.successes == []
    assert pipeline.count_terminals == [(MODEL, "errors", 0)]


def test_ui_sandbox_seeds_daily_and_updates_today(monkeypatch):
    from aistudio_api.api.ui_sandbox import UISandbox

    sandbox = UISandbox()
    try:
        payload = sandbox.stats_payload()
        daily_payload = cast("list[dict[str, Any]]", payload["daily"])
        assert len(daily_payload) == 90
        keys = [entry["date"] for entry in daily_payload]
        assert keys == daily_keys(90)
        today_entry = daily_payload[-1]
        assert len(today_entry["by_model"]) >= 3
        models = today_entry["by_model"]
        some = next(iter(models.values()))
        assert set(some["tokens"]) == {
            "prompt_tokens",
            "cached_tokens",
            "completion_tokens",
            "reasoning_tokens",
            "total_tokens",
        }

        before = today_entry["client"]["requests"]
        sandbox.apply(action="simulate_requests")
        after = cast("list[dict[str, Any]]", sandbox.stats_payload()["daily"])[-1]
        assert after["client"]["requests"] == before + 10
        assert after["tokens"]["total_tokens"] > today_entry["tokens"]["total_tokens"]

        limited_before = after["attempt"]["rate_limited"]
        sandbox.apply(action="simulate_429")
        limited = cast(
            "list[dict[str, Any]]", sandbox.stats_payload()["daily"]
        )[-1]
        assert limited["attempt"]["rate_limited"] == limited_before + 1
        assert limited["client"]["rate_limited"] >= 1
    finally:
        sandbox.close()
