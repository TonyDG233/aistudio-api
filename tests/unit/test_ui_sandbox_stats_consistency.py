"""N-1: UI debug sandbox daily/client/token ledgers must stay self-consistent."""

from __future__ import annotations

from typing import Any, cast

import pytest

from aistudio_api.api.state import ModelStatsItem, daily_key
from aistudio_api.api.ui_sandbox import UISandbox


def _assert_bucket(bucket: dict[str, Any]) -> None:
    client = bucket["client"]
    attempt = bucket["attempt"]
    tokens = bucket["tokens"]

    assert attempt["attempts"] == (
        attempt["success"] + attempt["rate_limited"] + attempt["errors"]
    )
    assert client["requests"] == (
        client["success"]
        + client["rate_limited"]
        + client["errors"]
        + client["aborted"]
    )
    assert 0 <= client["rescued_requests"] <= client["success"]
    assert 0 <= tokens["cached_tokens"] <= tokens["prompt_tokens"]
    assert 0 <= tokens["reasoning_tokens"] <= tokens["completion_tokens"]
    assert tokens["total_tokens"] == (
        tokens["prompt_tokens"] + tokens["completion_tokens"]
    )


def _assert_payload(payload: dict[str, Any]) -> None:
    for point in payload["daily"]:
        _assert_bucket(point)
        for bucket in point["by_model"].values():
            _assert_bucket(bucket)
    _assert_bucket(
        {
            "client": payload["client"],
            "attempt": payload["attempt"],
            "tokens": {
                "prompt_tokens": payload["totals"]["prompt_tokens"],
                "cached_tokens": payload["totals"]["cached_tokens"],
                "completion_tokens": payload["totals"]["completion_tokens"],
                "reasoning_tokens": payload["totals"]["reasoning_tokens"],
                "total_tokens": payload["totals"]["total_tokens"],
            },
        }
    )


def _assert_item(item: ModelStatsItem) -> None:
    assert item.requests == item.success + item.rate_limited + item.errors
    assert 0 <= item.cached_tokens <= item.prompt_tokens
    assert 0 <= item.reasoning_tokens <= item.completion_tokens
    assert item.total_tokens == item.prompt_tokens + item.completion_tokens
    assert item.client.requests == (
        item.client.success
        + item.client.rate_limited
        + item.client.errors
        + item.client.aborted
    )


@pytest.mark.parametrize(
    "scenario",
    ["all_states", "mass_accounts", "narrow_edge", "all_429", "empty"],
)
def test_seeded_daily_ledgers_are_arithmetically_consistent(scenario: str) -> None:
    sandbox = UISandbox()
    try:
        sandbox.reset(scenario)
        payload = sandbox.stats_payload()
        _assert_payload(payload)
        for item in sandbox.model_stats.values():
            _assert_item(item)
    finally:
        sandbox.close()


def test_simulate_actions_keep_today_and_totals_consistent() -> None:
    sandbox = UISandbox()
    try:
        sandbox.reset("all_states")
        sandbox.apply(action="simulate_requests")
        sandbox.apply(action="simulate_429")

        today = daily_key()
        payload = cast(dict[str, Any], sandbox.stats_payload(7))
        _assert_payload(payload)
        for item in sandbox.model_stats.values():
            _assert_item(item)

        point = next(day for day in payload["daily"] if day["date"] == today)
        _assert_bucket(point)
        tokens = point["tokens"]
        assert tokens["total_tokens"] == (
            tokens["cached_tokens"]
            + (tokens["prompt_tokens"] - tokens["cached_tokens"])
            + tokens["completion_tokens"]
        )

        scoped = cast(dict[str, Any], sandbox.stats_payload(7))
        _assert_bucket(
            {
                "client": scoped["client"],
                "attempt": scoped["attempt"],
                "tokens": {
                    "prompt_tokens": scoped["totals"]["prompt_tokens"],
                    "cached_tokens": scoped["totals"]["cached_tokens"],
                    "completion_tokens": scoped["totals"]["completion_tokens"],
                    "reasoning_tokens": scoped["totals"]["reasoning_tokens"],
                    "total_tokens": scoped["totals"]["total_tokens"],
                },
            }
        )
    finally:
        sandbox.close()
