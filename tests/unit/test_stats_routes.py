"""Tests for the v2 stats response and timeseries helpers."""

from __future__ import annotations

import pytest

from aistudio_api.api import routes_system
from aistudio_api.api.state import runtime_state


@pytest.fixture(autouse=True)
def _clean_stats():
    runtime_state.model_stats.clear()
    runtime_state.daily_stats.clear()
    yield
    runtime_state.model_stats.clear()
    runtime_state.daily_stats.clear()


@pytest.mark.asyncio
async def test_stats_response_range_and_nulls(monkeypatch) -> None:
    async def _no_paid(_session=None):
        return {}

    monkeypatch.setattr(routes_system, "_paid_map", _no_paid)
    runtime_state.record(
        "models/gemini-3.8-flash",
        "success",
        {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "cached_tokens": 30,
            "completion_tokens_details": {"reasoning_tokens": 20},
        },
        api_format="openai",
        surface="chat",
    )
    runtime_state.record_client(
        "gemini-3.8-flash", "success", api_format="openai", surface="chat"
    )
    runtime_state.record_token_count(
        "gemini-3.8-flash",
        api_format="claude",
        surface="build",
        outcome="success",
        total_tokens=42,
    )

    response = await routes_system.stats_response(range_key="today")
    model = response.models["gemini-3.8-flash"]
    assert response.range.key == "today"
    assert model.prompt_tokens == 100
    assert model.reasoning_tokens == 20
    assert model.cache_read_tokens == 30
    assert model.cache_write_tokens is None
    assert model.cache_hit_rate == 0.3
    assert model.client_requests == 1
    assert model.client_success == 1
    assert model.count_requests == 1
    assert model.count_input_tokens == 42
    assert model.formats["openai"].total_tokens == 150
    assert model.surfaces["build"].count_requests == 1
    assert response.totals.total_tokens == 150
    assert response.totals.count_input_tokens == 42


@pytest.mark.asyncio
async def test_timeseries_returns_missing_days_as_null() -> None:
    runtime_state.record(
        "gemini-3.8-flash",
        "success",
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        api_format="gemini",
        surface="chat",
    )
    response = await routes_system.stats_timeseries(
        range_key="7d",
        bucket="day",
        runtime_state=runtime_state,
    )
    assert response.bucket == "day"
    assert len(response.points) == 7
    assert any(point.totals is None for point in response.points)
    assert any(
        point.totals is not None and point.totals.total_tokens == 15
        for point in response.points
    )
