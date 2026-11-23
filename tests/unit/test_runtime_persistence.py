"""Unit tests for runtime statistics and account rotator state persistence."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from aistudio_api.api.state import RuntimeState
from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore


def test_model_stats_persistence(tmp_path, monkeypatch):
    stats_file = tmp_path / "data" / "stats.json"
    monkeypatch.setenv("AISTUDIO_STATS_FILE", str(stats_file))
    monkeypatch.setenv("AISTUDIO_PERSIST_STATS", "1")

    state1 = RuntimeState()
    state1.record(
        "gemini-3.7-flash",
        "success",
        {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    )
    state1.record("gemini-3.7-flash", "rate_limited")
    state1.record("gemini-3.1-flash-image-preview", "errors")

    assert stats_file.is_file()
    saved = json.loads(stats_file.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2
    assert "models" in saved and "daily" in saved
    assert saved["models"]["gemini-3.7-flash"]["requests"] == 2
    assert saved["models"]["gemini-3.7-flash"]["success"] == 1
    assert saved["models"]["gemini-3.7-flash"]["rate_limited"] == 1
    assert saved["models"]["gemini-3.7-flash"]["prompt_tokens"] == 10
    assert saved["models"]["gemini-3.7-flash"]["total_tokens"] == 30
    assert saved["models"]["gemini-3.1-flash-image-preview"]["errors"] == 1
    assert "totals" not in saved
    daily = saved.get("daily")
    assert isinstance(daily, dict) and daily

    # Simulate restart by creating a new RuntimeState instance
    state2 = RuntimeState()
    assert state2.model_stats["gemini-3.7-flash"].requests == 2
    assert state2.model_stats["gemini-3.7-flash"].success == 1
    assert state2.model_stats["gemini-3.7-flash"].rate_limited == 1
    assert state2.model_stats["gemini-3.7-flash"].total_tokens == 30
    assert state2.model_stats["gemini-3.1-flash-image-preview"].errors == 1


def test_account_rotator_state_persistence(tmp_path, monkeypatch):
    rotator_file = tmp_path / "data" / "rotator_state.json"
    monkeypatch.setenv("AISTUDIO_ROTATOR_STATE_FILE", str(rotator_file))
    monkeypatch.setenv("AISTUDIO_PERSIST_ROTATOR", "1")

    mock_store = MagicMock(spec=AccountStore)
    mock_store.list_accounts.return_value = [
        AccountMeta(
            id="acc_test1",
            name="Account 1",
            email="acc1@gmail.com",
            created_at="2026-09-21 00:00:00",
        ),
        AccountMeta(
            id="acc_test2",
            name="Account 2",
            email="acc2@gmail.com",
            created_at="2026-09-21 00:00:00",
        ),
    ]

    rotator1 = AccountRotator(mock_store)
    rotator1.record_success("acc_test1", "gemini-3.7-flash")
    rotator1.record_rate_limited("acc_test1", "gemini-3.7-flash")
    rotator1.record_rate_limited("acc_test1", "gemini-3.7-flash")
    rotator1.record_rate_limited(
        "acc_test1", "gemini-3.7-flash"
    )  # triggers 3rd 429 -> locks to midnight
    rotator1.record_auth_error("acc_test2", "gemini-3.7-flash")

    assert rotator_file.is_file()
    saved = json.loads(rotator_file.read_text(encoding="utf-8"))
    assert "acc_test1" in saved
    assert saved["acc_test1"]["total_requests"] == 4
    assert saved["acc_test1"]["total_rate_limited"] == 3
    assert "gemini-3.7-flash" in saved["acc_test1"]["daily_models"]
    assert saved["acc_test1"]["daily_models"]["gemini-3.7-flash"]["requests"] == 4
    assert saved["acc_test2"]["auth_errors"] == 1

    # Simulate restart by creating a new AccountRotator instance
    rotator2 = AccountRotator(mock_store)
    all_stats = rotator2.get_all_stats()
    assert all_stats["acc_test1"]["requests"] == 4
    assert all_stats["acc_test1"]["rate_limited"] == 3
    assert not rotator2._stats["acc_test1"].is_available("gemini-3.7-flash")
    assert rotator2._stats["acc_test2"].is_available("gemini-3.7-flash")


def test_account_meta_verify_required_round_trip_and_legacy_default():
    meta = AccountMeta(
        id="acc_verify",
        name="Verify",
        email="verify@example.com",
        created_at="2026-11-22 00:00:00",
        verify_required=True,
    )
    restored = AccountMeta.from_dict(meta.to_dict())
    assert restored.verify_required is True
    assert restored.id == meta.id

    legacy = AccountMeta.from_dict(
        {
            "id": "acc_legacy",
            "name": "Legacy",
            "email": None,
            "created_at": "2025-01-01 00:00:00",
        }
    )
    assert legacy.verify_required is False


def test_account_stats_verify_required_is_separate_from_session_expired():
    from aistudio_api.application.account_rotator import AccountStats

    stats = AccountStats(account_id="acc_verify")
    stats.record_verify_required("gemini-3.7-flash", surface="chat")

    assert stats.verify_required is True
    assert stats.verify_required_count == 1
    assert stats.last_verify_required_at > 0
    assert stats.session_expired is False
    assert stats.is_available("gemini-3.7-flash", surface="chat") is False
    assert stats.get_cooldown_remaining("gemini-3.7-flash", surface="chat") == 0.0

    data = stats.to_dict()
    restored = AccountStats.from_dict(data)
    assert restored.verify_required is True
    assert restored.verify_required_count == 1
    assert restored.last_verify_required_at == stats.last_verify_required_at
    assert restored.session_expired is False


def test_rotator_is_auth_locked_covers_verify_required(tmp_path):
    mock_store = MagicMock(spec=AccountStore)
    verify_meta = AccountMeta(
        id="acc_verify",
        name="Verify",
        email="v@example.com",
        created_at="2026-11-22 00:00:00",
        verify_required=True,
    )
    ok_meta = AccountMeta(
        id="acc_ok",
        name="OK",
        email="ok@example.com",
        created_at="2026-11-22 00:00:00",
    )
    mock_store.list_accounts.return_value = [verify_meta, ok_meta]
    mock_store.get_account.side_effect = lambda account_id: (
        verify_meta if account_id == "acc_verify" else ok_meta
    )

    rotator = AccountRotator(mock_store)

    assert rotator.is_auth_locked("acc_verify") is True
    assert rotator.is_auth_locked("acc_ok") is False
    verify_stats = rotator.get_account_stats("acc_verify")
    assert verify_stats is not None and verify_stats.verify_required is True


def test_reimport_same_account_clears_verify_lock_explicitly(tmp_path):
    store = AccountStore(tmp_path / "accounts")
    state = {
        "cookies": [
            {"name": "SID", "value": "a", "domain": ".google.com", "path": "/"}
        ],
        "origins": [],
    }
    first = store.save_account(
        name="A",
        email="a@example.com",
        storage_state=state,
        account_id="acc_reimport",
        tier="Free",
    )
    assert store.update_account_refresh(account_id="acc_reimport", verify_required=True)
    assert store.get_account(first.id).verify_required is True  # type: ignore[union-attr]

    again = store.save_account(
        name="A",
        email="a@example.com",
        storage_state={
            "cookies": [
                {"name": "SID", "value": "b", "domain": ".google.com", "path": "/"}
            ],
            "origins": [],
        },
        account_id="acc_reimport",
        tier="Free",
    )
    assert again.id == "acc_reimport"
    assert again.verify_required is False


def test_resolve_config_file_initializes_from_template_when_missing(
    tmp_path, monkeypatch
):
    from aistudio_api.config import resolve_config_file

    target_config = tmp_path / "data" / "config.yaml"
    assert not target_config.exists()

    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(target_config))
    resolved = resolve_config_file()

    assert resolved == target_config
    assert target_config.is_file()
    content = target_config.read_text(encoding="utf-8")
    assert "model_defaults" in content or "api_keys" in content


def test_resolve_config_file_never_overwrites_existing(tmp_path, monkeypatch):
    from aistudio_api.config import resolve_config_file

    target_config = tmp_path / "data" / "config.yaml"
    target_config.parent.mkdir(parents=True, exist_ok=True)
    target_config.write_text("custom_key: preserved_value\n", encoding="utf-8")

    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(target_config))
    resolved = resolve_config_file()

    assert resolved == target_config
    assert target_config.read_text(encoding="utf-8") == "custom_key: preserved_value\n"
