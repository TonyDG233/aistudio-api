"""Account rotation, per-model cooldown and D-01 quota_scope accounting."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.application.account_rotator import (
    AccountRotator,
    AccountStats,
    get_seconds_until_pacific_midnight,
    split_quota_key,
)
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore
from aistudio_api.infrastructure.gateway.capture import (
    CapturedRequest,
    RequestCaptureService,
)
from aistudio_api.infrastructure.gateway.session import BrowserSession


@pytest.mark.asyncio
async def test_capture_service_concurrency_and_caching():
    """Concurrent template capture runs once; every request gets a fresh snapshot."""
    mock_session = MagicMock(spec=BrowserSession)
    mock_session.generate_snapshot = AsyncMock(return_value="mock_snap")

    call_count = 0

    async def fake_capture_template(model: str):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.001)
        return {
            "url": "https://example.com/generate",
            "headers": {"content-type": "application/json"},
            "body": '["models/gemini-2.5-flash",[[[[null,"old"]],"user"]],null,[null,null,null,128,0.5,0.8,16],"orig_snap"]',
        }

    mock_session.capture_template = AsyncMock(side_effect=fake_capture_template)
    service = RequestCaptureService(session=mock_session)

    tasks = [
        service.capture(prompt=f"hi {i}", model="gemini-2.5-flash") for i in range(5)
    ]
    results = await asyncio.gather(*tasks)

    assert call_count == 1
    assert len(results) == 5
    assert mock_session.generate_snapshot.call_count == 5
    for res in results:
        assert isinstance(res, CapturedRequest)
        assert res.url == "https://example.com/generate"


def test_account_stats_per_model_cooldown():
    stats = AccountStats(account_id="acc_1")
    assert stats.is_available("gemini-2.5-pro")
    stats.record_rate_limited(model="gemini-2.5-pro")
    assert not stats.is_available("gemini-2.5-pro")
    assert stats.is_available("gemini-2.5-flash")
    assert stats.get_cooldown_remaining("gemini-2.5-pro") > 0
    assert stats.get_cooldown_remaining("gemini-2.5-flash") == 0
    stats.record_success(model="gemini-2.5-pro")
    assert stats.is_available("gemini-2.5-pro")


def test_account_stats_pacific_midnight_reset():
    stats = AccountStats(account_id="acc_1")
    m_stats = stats.get_model_daily("gemini-2.5-pro")
    m_stats.cooldown_until = time.time() + 3600
    stats.daily_date_la = "2020-01-01"
    assert stats.is_available("gemini-2.5-pro")
    assert stats.daily_date_la != "2020-01-01"


def test_rotator_quota_scope_reads_config(tmp_path, monkeypatch):
    from aistudio_api.infrastructure.gateway.model_defaults import (
        invalidate_config_cache,
    )

    config = tmp_path / "config.yaml"
    config.write_text("rotator:\n  quota_scope: account_surface\n", encoding="utf-8")
    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(config))
    invalidate_config_cache()
    store = MagicMock(spec=AccountStore)
    store.list_accounts.return_value = []
    rotator = AccountRotator(store)
    assert rotator.quota_scope == "account_surface"

    config.write_text("rotator:\n  quota_scope: bogus\n", encoding="utf-8")
    invalidate_config_cache()
    rotator = AccountRotator(store)
    assert rotator.quota_scope == "account"


def test_clear_cooldown_resets_counter():
    stats = AccountStats(account_id="acc_1")
    stats.record_rate_limited(model="gemini-2.5-pro")
    stats.record_rate_limited(model="gemini-2.5-pro")
    stats.record_rate_limited(model="gemini-2.5-pro")
    assert not stats.is_available("gemini-2.5-pro")
    stats.clear_cooldown(model="gemini-2.5-pro")
    assert stats.is_available("gemini-2.5-pro")
    assert stats.get_model_daily("gemini-2.5-pro").rate_limited == 0
    stats.record_rate_limited(model="gemini-2.5-pro")
    assert 0 < stats.get_cooldown_remaining("gemini-2.5-pro") <= 60.0


def test_rate_limit_cooldown_escalates_short_drip_extended(monkeypatch):
    """429 首次命中即切号后：第 1 次 60s，第 2 次 300s，第 3 次起 600s。"""
    from aistudio_api.application import account_rotator as rotator_module

    monkeypatch.setattr(
        rotator_module, "get_seconds_until_pacific_midnight", lambda: 86400.0
    )
    stats = AccountStats(account_id="acc_1")
    model = "gemini-2.5-pro"

    stats.record_rate_limited(model=model)
    assert stats.get_model_daily(model).drip_mode is False
    assert stats.get_cooldown_remaining(model) == pytest.approx(60.0, abs=1.0)

    stats.record_rate_limited(model=model)
    assert stats.get_model_daily(model).drip_mode is True
    assert stats.get_cooldown_remaining(model) == pytest.approx(300.0, abs=1.0)

    stats.record_rate_limited(model=model)
    assert stats.get_cooldown_remaining(model) == pytest.approx(600.0, abs=1.0)

    stats.record_rate_limited(model=model)
    assert stats.get_cooldown_remaining(model) == pytest.approx(600.0, abs=1.0)


def test_quota_scope_account_shares_chat_and_build_keys():
    stats = AccountStats(account_id="acc_1", quota_scope="account")
    stats.record_success(model="gemini-3.7-flash", surface="chat")
    stats.record_success(model="gemini-3.7-flash", surface="build")
    assert list(stats.daily_models) == ["gemini-3.7-flash"]
    assert stats.daily_models["gemini-3.7-flash"].requests == 2


def test_quota_scope_account_surface_isolates_keys_and_cooldowns():
    stats = AccountStats(account_id="acc_1", quota_scope="account_surface")
    stats.record_rate_limited(model="gemini-3.7-flash", surface="chat")
    stats.record_success(model="gemini-3.7-flash", surface="build")

    assert set(stats.daily_models) == {
        "chat::gemini-3.7-flash",
        "build::gemini-3.7-flash",
    }
    assert not stats.is_available("gemini-3.7-flash", surface="chat")
    assert stats.is_available("gemini-3.7-flash", surface="build")
    assert split_quota_key("chat::gemini-3.7-flash") == (
        "chat",
        "gemini-3.7-flash",
    )


def test_quota_scope_account_surface_reads_legacy_plain_key() -> None:
    """D-01: switching scope keeps historical plain ``model`` keys readable."""
    stats = AccountStats(account_id="acc_1", quota_scope="account_surface")
    legacy = stats.get_model_daily("gemini-3.7-flash")
    legacy.cooldown_until = time.time() + 60
    assert not stats.is_available("gemini-3.7-flash", surface="chat")
    stats.clear_cooldown("gemini-3.7-flash", surface="chat")
    assert stats.is_available("gemini-3.7-flash", surface="chat")


def test_quota_scope_account_surface_midnight_reset():
    stats = AccountStats(account_id="acc_1", quota_scope="account_surface")
    chat = stats.get_model_daily("gemini-3.7-flash", surface="chat")
    chat.cooldown_until = time.time() + 3600
    stats.daily_date_la = "2020-01-01"
    assert stats.is_available("gemini-3.7-flash", surface="chat")
    assert stats.daily_models == {}


@pytest.mark.asyncio
async def test_rotator_sticky_and_least_used_without_groups():
    store = MagicMock(spec=AccountStore)
    acc1 = AccountMeta(
        id="acc_1", name="Account 1", email="acc1@example.com", created_at="2026-01-01"
    )
    acc2 = AccountMeta(
        id="acc_2", name="Account 2", email="acc2@example.com", created_at="2026-01-01"
    )
    store.list_accounts.return_value = [acc1, acc2]

    rotator = AccountRotator(account_store=store)
    for _ in range(10):
        rotator.record_success("acc_1", model="gemini-3.7-flash")
    for _ in range(2):
        rotator.record_success("acc_2", model="gemini-3.7-flash")

    sticky = await rotator.get_next_account(
        model="gemini-3.7-flash", current_account_id="acc_1"
    )
    assert sticky is not None and sticky.id == "acc_1"

    failover = await rotator.get_next_account(
        model="gemini-3.7-flash",
        current_account_id="acc_1",
        failed_account_id="acc_1",
    )
    assert failover is not None and failover.id == "acc_2"


@pytest.mark.asyncio
async def test_rotator_does_not_use_cooled_account_as_fallback():
    store = MagicMock(spec=AccountStore)
    acc1 = AccountMeta(
        id="acc_1", name="Account 1", email="acc1@example.com", created_at="2026-01-01"
    )
    store.list_accounts.return_value = [acc1]

    rotator = AccountRotator(account_store=store)
    rotator.record_rate_limited("acc_1", model="gemini-3.7-flash")
    assert await rotator.get_next_account(model="gemini-3.7-flash") is None


def test_get_seconds_until_pacific_midnight():
    remaining = get_seconds_until_pacific_midnight()
    assert 0 <= remaining <= 86400
