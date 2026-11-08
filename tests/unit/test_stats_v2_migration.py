"""Stats schema v2 loading, ghost-key cleanup and /stats filtering."""

from __future__ import annotations

import json

from aistudio_api.api.state import RuntimeState


def _use_stats_file(monkeypatch, tmp_path):
    stats_file = tmp_path / "data" / "stats.json"
    monkeypatch.setenv("AISTUDIO_STATS_FILE", str(stats_file))
    monkeypatch.setenv("AISTUDIO_PERSIST_STATS", "1")
    return stats_file


def test_v1_flat_stats_loads_and_migrates_to_v2(monkeypatch, tmp_path):
    stats_file = _use_stats_file(monkeypatch, tmp_path)
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    stats_file.write_text(
        json.dumps(
            {
                "gemini-3.8-flash": {
                    "requests": 7,
                    "success": 6,
                    "rate_limited": 1,
                    "total_tokens": 1234,
                },
                "totals": {"requests": 7},
                "version": 1,
            }
        ),
        encoding="utf-8",
    )

    state = RuntimeState()

    assert state.model_stats["gemini-3.8-flash"].requests == 7
    assert "totals" not in state.model_stats
    assert "version" not in state.model_stats
    backup = stats_file.with_name("stats.json.bak")
    assert backup.is_file()
    migrated = json.loads(stats_file.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert "gemini-3.8-flash" in migrated["models"]
    assert "totals" not in migrated
    assert "version" not in migrated


def test_v2_envelope_loads_models_and_daily(monkeypatch, tmp_path):
    stats_file = _use_stats_file(monkeypatch, tmp_path)
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    stats_file.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "models": {
                    "models/gemini-4-argon": {
                        "requests": 3,
                        "success": 2,
                        "errors": 1,
                        "reasoning_tokens": 20,
                    }
                },
                "daily": {
                    "2026-11-07": {
                        "gemini-4-argon": {"requests": 2, "success": 2}
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    state = RuntimeState()

    assert state.model_stats["gemini-4-argon"].requests == 3
    assert state.model_stats["gemini-4-argon"].reasoning_tokens == 20
    assert state.daily_stats["2026-11-07"]["gemini-4-argon"].requests == 2
    # Clean v2 is left untouched.
    assert not stats_file.with_name("stats.json.bak").exists()


def test_v2_top_level_ghost_counters_never_become_models(monkeypatch, tmp_path):
    stats_file = _use_stats_file(monkeypatch, tmp_path)
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    original = {
        "schema_version": 2,
        "models": {"gemini-3.8-flash": {"requests": 4, "success": 4}},
        "daily": {},
        # Legacy aggregate counters that used to leak into the model table.
        "finish_reasons": {"STOP": 12, "MAX_TOKENS": 3},
        "count_tokens": {"requests": 8},
        "client": {"requests": 1},
        "attempt": {"requests": 2},
        "range": {"requests": 3},
        "events": {"requests": 4},
        "quota_scope": {"requests": 5},
        "updated_at": {"requests": 6},
        "account_breakdown": {"requests": 7},
    }
    stats_file.write_text(json.dumps(original), encoding="utf-8")

    state = RuntimeState()

    assert set(state.model_stats) == {"gemini-3.8-flash"}
    assert state.model_stats["gemini-3.8-flash"].requests == 4
    backup = stats_file.with_name("stats.json.bak")
    assert json.loads(backup.read_text(encoding="utf-8")) == original
    rewritten = json.loads(stats_file.read_text(encoding="utf-8"))
    assert set(rewritten["models"]) == {"gemini-3.8-flash"}
    for ghost in ("finish_reasons", "count_tokens", "client", "quota_scope"):
        assert ghost not in rewritten


def test_v2_polluted_models_entry_is_cleaned_but_real_models_kept(
    monkeypatch, tmp_path
):
    stats_file = _use_stats_file(monkeypatch, tmp_path)
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    stats_file.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "models": {
                    "finish_reasons": {"STOP": 999},
                    "models/count_tokens": {"requests": 1},
                    "gemini-3.7-flash": {"requests": 6, "success": 6},
                    "gemini-4-argon": {"requests": 2, "errors": 1},
                },
                "daily": {
                    "2026-11-07": {
                        "finish_reasons": {"STOP": 5},
                        "gemini-3.7-flash": {"requests": 6},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    state = RuntimeState()

    assert set(state.model_stats) == {"gemini-3.7-flash", "gemini-4-argon"}
    assert set(state.daily_stats["2026-11-07"]) == {"gemini-3.7-flash"}
    rewritten = json.loads(stats_file.read_text(encoding="utf-8"))
    assert set(rewritten["models"]) == {"gemini-3.7-flash", "gemini-4-argon"}
    assert "finish_reasons" not in rewritten["models"]


def test_v1_flat_ghost_counters_are_skipped(monkeypatch, tmp_path):
    stats_file = _use_stats_file(monkeypatch, tmp_path)
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    stats_file.write_text(
        json.dumps(
            {
                "gemini-3.8-flash": {"requests": 9, "success": 9},
                "finish_reasons": {"STOP": 9},
                "count_tokens": {"requests": 1},
                "account_breakdown": {"requests": 2},
            }
        ),
        encoding="utf-8",
    )

    state = RuntimeState()

    assert set(state.model_stats) == {"gemini-3.8-flash"}
    assert state.model_stats["gemini-3.8-flash"].requests == 9


def test_save_stats_never_writes_reserved_ghost_keys(monkeypatch, tmp_path):
    stats_file = _use_stats_file(monkeypatch, tmp_path)
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    state = RuntimeState()

    from aistudio_api.api.state import ModelStatsItem

    state.model_stats["gemini-3.8-flash"] = ModelStatsItem(requests=3, success=3)
    state.model_stats["finish_reasons"] = ModelStatsItem(requests=50)
    state.model_stats["count_tokens"] = ModelStatsItem(requests=10)
    state.save_stats()

    saved = json.loads(stats_file.read_text(encoding="utf-8"))
    assert set(saved["models"]) == {"gemini-3.8-flash"}
    assert saved["models"]["gemini-3.8-flash"]["requests"] == 3


def test_ghost_keys_are_skipped_and_clean_structure_written(monkeypatch, tmp_path):
    stats_file = _use_stats_file(monkeypatch, tmp_path)
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    original = {
        "schema_version": 2,
        "models": {
            "models": {"requests": 999},
            "daily": {"requests": 888},
            "gemini-3.7-flash": {"requests": 5, "success": 5},
        },
        "daily": {
            "schema_version": {"requests": 1},
            "2026-11-07": {
                "totals": {"requests": 50},
                "gemini-3.7-flash": {"requests": 5},
            },
        },
        "totals": {"requests": 12345},
    }
    stats_file.write_text(json.dumps(original), encoding="utf-8")

    state = RuntimeState()

    assert set(state.model_stats) == {"gemini-3.7-flash"}
    assert "2026-11-07" in state.daily_stats
    assert set(state.daily_stats["2026-11-07"]) == {"gemini-3.7-flash"}
    backup = stats_file.with_name("stats.json.bak")
    assert json.loads(backup.read_text(encoding="utf-8")) == original
    rewritten = json.loads(stats_file.read_text(encoding="utf-8"))
    assert rewritten["schema_version"] == 2
    assert set(rewritten["models"]) == {"gemini-3.7-flash"}
    assert "totals" not in rewritten


def test_migration_write_failure_keeps_loaded_history(
    monkeypatch, tmp_path
):
    stats_file = _use_stats_file(monkeypatch, tmp_path)
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    original = {"gemini-3.8-flash": {"requests": 9, "success": 8}}
    stats_file.write_text(json.dumps(original), encoding="utf-8")

    import aistudio_api.api.state as state_module

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(state_module, "atomic_write_json", _boom)

    state = RuntimeState()

    assert state.model_stats["gemini-3.8-flash"].requests == 9
    assert json.loads(stats_file.read_text(encoding="utf-8")) == original
    assert stats_file.with_name("stats.json.bak").is_file()


def test_stats_response_filters_reserved_keys(monkeypatch):
    from aistudio_api.api.routes_system import stats_response
    from aistudio_api.api.state import ModelStatsItem, runtime_state

    monkeypatch.setattr(runtime_state, "ui_debug", False)
    monkeypatch.setattr(runtime_state, "ui_sandbox", None)
    monkeypatch.setattr(runtime_state, "rotator", None)
    monkeypatch.setattr(runtime_state, "account_service", None)
    monkeypatch.setattr(
        runtime_state,
        "model_stats",
        {
            "models": ModelStatsItem(requests=40),
            "daily": ModelStatsItem(requests=30),
            "schema_version": ModelStatsItem(requests=20),
            "totals": ModelStatsItem(requests=10),
            "version": ModelStatsItem(requests=1),
            "finish_reasons": ModelStatsItem(requests=9),
            "count_tokens": ModelStatsItem(requests=8),
            "account_breakdown": ModelStatsItem(requests=7),
            "models/finish_reasons": ModelStatsItem(requests=6),
            "gemini-3.8-flash": ModelStatsItem(requests=5, success=4),
        },
    )

    result = stats_response()

    assert set(result.models) == {"gemini-3.8-flash"}
    assert result.totals.requests == 5
    assert result.models["gemini-3.8-flash"].success == 4
