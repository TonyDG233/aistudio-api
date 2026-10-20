"""Tests for schema v2 model statistics and two-layer counters."""

from __future__ import annotations

import json

from aistudio_api.api.state import RuntimeState


def _usage() -> dict[str, object]:
    return {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "cached_tokens": 30,
        "completion_tokens_details": {"reasoning_tokens": 20},
    }


def _fresh_state(monkeypatch) -> RuntimeState:
    monkeypatch.setattr(
        "aistudio_api.api.state.settings.persist_stats", False, raising=False
    )
    state = RuntimeState()
    state.model_stats.clear()
    state.daily_stats.clear()
    state._schedule_debounced_save = lambda delay=2.0: None  # type: ignore[method-assign]
    return state


def test_record_attempt_and_client_layers(monkeypatch) -> None:
    state = _fresh_state(monkeypatch)
    state.record(
        "models/gemini-3.8-flash",
        "success",
        _usage(),
        api_format="openai",
        surface="chat",
    )
    state.record(
        "models/gemini-3.8-flash",
        "rate_limited",
        api_format="openai",
        surface="chat",
    )
    state.record_client(
        "models/gemini-3.8-flash",
        "success",
        api_format="openai",
        surface="chat",
    )
    state.record_client(
        "models/gemini-3.8-flash",
        "rate_limited",
        api_format="openai",
        surface="chat",
    )

    item = state.model_stats["gemini-3.8-flash"]
    assert item.requests == 2
    assert item.attempts == 2
    assert item.success == 1
    assert item.rate_limited == 1
    assert item.client_requests == 2
    assert item.client_success == 1
    assert item.client_errors == 1
    assert item.client_rate_limited == 1
    assert item.client_aborted is None
    assert item.prompt_tokens == 100
    assert item.completion_tokens == 50
    assert item.reasoning_tokens == 20
    assert item.cache_read_tokens == 30
    assert item.cache_write_tokens is None
    assert item.total_tokens == 150
    assert item.cache_hit_rate == 0.3
    assert item.formats["openai"].requests == 2
    assert item.formats["openai"].prompt_tokens == 100
    assert item.formats["openai"].client_requests == 2
    assert item.surfaces["chat"].requests == 2
    assert item.surfaces["chat"].client_requests == 2


def test_count_tokens_never_touches_generation_counters(monkeypatch) -> None:
    state = _fresh_state(monkeypatch)
    state.record_token_count(
        "models/gemini-3.8-flash",
        api_format="claude",
        surface="build",
        outcome="success",
        total_tokens=42,
    )
    state.record_token_count(
        "models/gemini-3.8-flash",
        api_format="claude",
        surface="build",
        outcome="error",
    )

    item = state.model_stats["gemini-3.8-flash"]
    assert item.requests == 0
    assert item.success == 0
    assert item.errors == 0
    assert item.client_requests is None
    assert item.prompt_tokens == 0
    assert item.completion_tokens == 0
    assert item.total_tokens == 0
    assert item.count_requests == 2
    assert item.count_success == 1
    assert item.count_errors == 1
    assert item.count_input_tokens == 42
    assert item.formats["claude"].count_requests == 2
    assert item.surfaces["build"].count_requests == 2


def test_usage_missing_is_explicit(monkeypatch) -> None:
    state = _fresh_state(monkeypatch)
    state.record("gemini-3.8-flash", "success", None, api_format="gemini")
    item = state.model_stats["gemini-3.8-flash"]
    assert item.success == 1
    assert item.usage_missing == 1
    assert item.prompt_tokens == 0
    assert item.reasoning_tokens is None
    assert item.cache_read_tokens is None


def test_legacy_duplicate_keys_are_merged(tmp_path, monkeypatch) -> None:
    stats_file = tmp_path / "stats.json"
    stats_file.write_text(
        json.dumps(
            {
                "models/gemini-3.8-flash": {
                    "requests": 2,
                    "success": 1,
                    "rate_limited": 1,
                    "errors": 0,
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                },
                "gemini-3.8-flash": {
                    "requests": 1,
                    "success": 1,
                    "rate_limited": 0,
                    "errors": 0,
                    "prompt_tokens": 5,
                    "completion_tokens": 6,
                    "total_tokens": 11,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AISTUDIO_STATS_FILE", str(stats_file))
    monkeypatch.setattr(
        "aistudio_api.api.state.settings.persist_stats", True, raising=False
    )

    state = RuntimeState()
    assert list(state.model_stats) == ["gemini-3.8-flash"]
    item = state.model_stats["gemini-3.8-flash"]
    assert item.requests == 3
    assert item.success == 2
    assert item.total_tokens == 41
    assert item.reasoning_tokens is None
    assert item.client_requests is None
    assert item.count_requests is None


def test_stats_for_range_uses_daily_buckets(monkeypatch) -> None:
    state = _fresh_state(monkeypatch)
    state.record(
        "gemini-3.8-flash",
        "success",
        _usage(),
        api_format="gemini",
        surface="chat",
    )
    state.record_client("gemini-3.8-flash", "success", surface="chat")
    models, info = state.stats_for_range("today")
    assert info["key"] == "today"
    assert "gemini-3.8-flash" in models
    assert models["gemini-3.8-flash"].total_tokens == 150

    empty_models, empty_info = state.stats_for_range(
        "custom", start="2020-01-01", end="2020-01-02"
    )
    assert empty_models == {}
    assert empty_info["partial"] is True
