"""WP5 unit tests: surface-scoped rotator keys, migration, quota hook, client check."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest

from aistudio_api.application.account_rotator import (
    AccountRotator,
    AccountStats,
    canonicalize_surface_key,
    split_surface_key,
    surface_model_key,
)
from aistudio_api.config import settings
from aistudio_api.domain.errors import RequestError
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore
from aistudio_api.infrastructure.gateway.client import AIStudioClient


@pytest.fixture
def no_persist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid touching the real rotator state file during rotator tests."""
    monkeypatch.setattr(settings, "persist_rotator", False)


def _acc(account_id: str, name: str = "Acc", email: str | None = None) -> AccountMeta:
    return AccountMeta(
        id=account_id,
        name=name,
        email=email,
        created_at="2026-01-01",
    )


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def test_surface_model_key_format_and_default() -> None:
    assert surface_model_key("chat", "models/Gemini-2.5-Pro") == "chat::gemini-2.5-pro"
    assert surface_model_key("build", "gemini-2.5-pro") == "build::gemini-2.5-pro"
    assert surface_model_key(model="gemini-2.5-pro") == "chat::gemini-2.5-pro"
    assert surface_model_key("chat", None) is None


def test_split_and_canonicalize_roundtrip() -> None:
    assert split_surface_key("gemini-2.5-pro") == ("chat", "gemini-2.5-pro")
    assert split_surface_key("build::gemini-2.5-pro") == ("build", "gemini-2.5-pro")
    # Legacy bare keys collapse to the chat storage form; chat:: also collapses.
    assert canonicalize_surface_key("gemini-2.5-pro") == "gemini-2.5-pro"
    assert canonicalize_surface_key("chat::gemini-2.5-pro") == "gemini-2.5-pro"
    assert canonicalize_surface_key("build::gemini-2.5-pro") == "build::gemini-2.5-pro"


# ---------------------------------------------------------------------------
# Migration from legacy persisted state
# ---------------------------------------------------------------------------


def test_from_dict_migrates_legacy_bare_and_chat_prefixed_keys() -> None:
    stats = AccountStats.from_dict(
        {
            "account_id": "acc_1",
            "model_cooldowns": {
                "gemini-x": 111.0,  # legacy bare -> chat
                "chat::gemini-y": 5.0,  # chat-prefixed -> bare chat
                "build::gemini-z": 9.0,  # non-chat preserved
            },
            "model_rate_limited_dates": {"gemini-x": "2026-01-01"},
            "model_requests": {"build::gemini-z": 3},
        }
    )
    assert set(stats.model_cooldowns) == {"gemini-x", "gemini-y", "build::gemini-z"}
    assert stats.model_cooldowns["gemini-x"] == 111.0
    assert stats.model_rate_limited_dates == {"gemini-x": "2026-01-01"}
    assert stats.model_requests == {"build::gemini-z": 3}


# ---------------------------------------------------------------------------
# Surface isolation
# ---------------------------------------------------------------------------


def test_surface_isolation_chat_vs_build() -> None:
    stats = AccountStats(account_id="acc_1")
    stats.record_rate_limited("gemini-2.5-pro", surface="chat")

    assert not stats.is_available("gemini-2.5-pro", surface="chat")
    # build surface is untouched by the chat 429
    assert stats.is_available("gemini-2.5-pro", surface="build")
    assert stats.get_cooldown_remaining("gemini-2.5-pro", surface="chat") > 0
    assert stats.get_cooldown_remaining("gemini-2.5-pro", surface="build") == 0

    # reverse direction: build 429 must not cool down chat
    stats.record_rate_limited("gemini-2.5-pro", surface="build")
    assert not stats.is_available("gemini-2.5-pro", surface="build")
    assert "build::gemini-2.5-pro" in stats.model_cooldowns
    # chat still only affected by its own cooldown (already set above)
    assert stats.is_available("gemini-2.5-pro", surface="chat") is False


def test_is_available_and_remaining_default_to_chat() -> None:
    stats = AccountStats(account_id="acc_1")
    stats.record_rate_limited("gemini-2.5-pro")  # default surface=chat
    assert not stats.is_available("gemini-2.5-pro")
    assert stats.get_cooldown_remaining("gemini-2.5-pro") > 0
    # default-surface success clears the chat cooldown only
    stats.record_success("gemini-2.5-pro")
    assert stats.is_available("gemini-2.5-pro")


def test_clear_cooldown_is_surface_scoped() -> None:
    stats = AccountStats(account_id="acc_1")
    stats.record_rate_limited("gemini-2.5-pro", surface="chat")
    stats.record_rate_limited("gemini-2.5-pro", surface="build")

    stats.clear_cooldown("gemini-2.5-pro", surface="build")
    assert stats.is_available("gemini-2.5-pro", surface="build")
    assert not stats.is_available("gemini-2.5-pro", surface="chat")


# ---------------------------------------------------------------------------
# get_all_stats surface output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_all_stats_surface_fields(no_persist: None) -> None:
    store = MagicMock(spec=AccountStore)
    store.list_accounts.return_value = [_acc("acc_1", "A", "a@example.com")]
    rotator = AccountRotator(store)

    rotator.record_rate_limited("acc_1", "gemini-2.5-pro", surface="chat")
    rotator.record_rate_limited("acc_1", "gemini-2.5-pro", surface="build")

    entry = rotator.get_all_stats()["acc_1"]
    model_cooldowns = cast("dict[str, int]", entry["model_cooldowns"])
    assert set(model_cooldowns) == {
        "chat::gemini-2.5-pro",
        "build::gemini-2.5-pro",
    }
    assert all(isinstance(v, int) and v > 0 for v in model_cooldowns.values())
    surface_cooldowns = cast("dict[str, int]", entry["surface_cooldowns"])
    assert set(surface_cooldowns) == {
        "chat::gemini-2.5-pro",
        "build::gemini-2.5-pro",
    }
    # legacy fields retained
    for field in (
        "requests",
        "rate_limited",
        "model_rate_limited_dates",
        "model_requests",
        "model_rate_limited",
        "model_drip_mode",
    ):
        assert field in entry


# ---------------------------------------------------------------------------
# Scheduling exclusion hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_surface_eligibility_hook_excludes_accounts(no_persist: None) -> None:
    store = MagicMock(spec=AccountStore)
    store.list_accounts.return_value = [_acc("acc_1"), _acc("acc_2")]
    rotator = AccountRotator(store)

    # default: both eligible
    avail_default = rotator._get_available_accounts("gemini-2.5-pro")
    assert {a.id for a, _ in avail_default} == {"acc_1", "acc_2"}

    rotator.set_surface_eligibility(lambda acc: acc.id != "acc_1")
    avail_filtered = rotator._get_available_accounts("gemini-2.5-pro")
    assert {a.id for a, _ in avail_filtered} == {"acc_2"}

    chosen = await rotator.get_next_account(model="gemini-2.5-pro")
    assert chosen is not None
    assert chosen.id == "acc_2"

    # clearing the hook restores default behaviour
    rotator.set_surface_eligibility(None)
    assert len(rotator._get_available_accounts("gemini-2.5-pro")) == 2


# ---------------------------------------------------------------------------
# Client build-surface quota check
# ---------------------------------------------------------------------------


def _install_fake_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple],
    *,
    group: dict | None,
    default_quota: int = 2,
) -> None:
    """Patch the real surface_orchestrator functions with deterministic fakes."""
    from aistudio_api.application import surface_orchestrator as so

    def match_group(tier, email, groups):
        return dict(group) if group is not None else None

    def check_instance_quota(
        group_name, max_instances, live_instances, surface, need_new
    ):
        calls.append((group_name, max_instances, live_instances, surface, need_new))
        quota = default_quota if max_instances is None else int(max_instances)
        if need_new and live_instances >= quota:
            raise so.SurfacePolicyError(
                "surface_quota_exceeded",
                message=f"分组 '{group_name}' 的页面实例配额已满",
                advice="请关闭闲置实例或调高 max_page_instances",
            )

    monkeypatch.setattr(so, "match_group", match_group)
    monkeypatch.setattr(so, "check_instance_quota", check_instance_quota)


def test_client_quota_check_passes_when_within_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []
    _install_fake_orchestrator(
        monkeypatch, calls, group={"name": "free", "max_page_instances": 3}
    )

    client = AIStudioClient()
    monkeypatch.setattr(client, "_active_account_identity", lambda: ("free", "a@b.c"))
    monkeypatch.setattr(client, "_features_config", lambda: {"groups": []})
    monkeypatch.setattr(client, "_count_live_page_instances", lambda: 1)
    monkeypatch.setattr(client, "_build_surface_page_alive", lambda: False)

    client._enforce_build_instance_quota()  # no raise
    assert calls == [("free", 3, 1, "build", True)]


def test_client_quota_check_raises_request_error_when_over_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []
    _install_fake_orchestrator(
        monkeypatch, calls, group={"name": "free", "max_page_instances": 3}
    )

    client = AIStudioClient()
    monkeypatch.setattr(client, "_active_account_identity", lambda: ("free", "a@b.c"))
    monkeypatch.setattr(client, "_features_config", lambda: {"groups": []})
    monkeypatch.setattr(client, "_count_live_page_instances", lambda: 3)
    monkeypatch.setattr(client, "_build_surface_page_alive", lambda: False)

    with pytest.raises(RequestError) as excinfo:
        client._enforce_build_instance_quota()
    err = excinfo.value
    assert err.status == 429
    text = str(err)
    assert "surface_quota_exceeded" in text
    assert "处置建议" in text
    assert calls and calls[0][3] == "build"


def test_client_quota_check_uses_default_group_when_unmatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []
    _install_fake_orchestrator(monkeypatch, calls, group=None, default_quota=2)

    client = AIStudioClient()
    monkeypatch.setattr(client, "_active_account_identity", lambda: ("free", "a@b.c"))
    monkeypatch.setattr(client, "_features_config", lambda: {"groups": []})
    monkeypatch.setattr(client, "_count_live_page_instances", lambda: 0)
    monkeypatch.setattr(client, "_build_surface_page_alive", lambda: False)

    client._enforce_build_instance_quota()
    assert calls == [("default:free", None, 0, "build", True)]


@pytest.mark.asyncio
async def test_build_stream_branch_enforces_quota_before_surface_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The quota check fires on the build stream path before BuildSurface.ensure_ready."""
    calls: list[tuple] = []
    _install_fake_orchestrator(
        monkeypatch, calls, group={"name": "free", "max_page_instances": 3}
    )

    client = AIStudioClient()
    monkeypatch.setattr(client, "_active_account_identity", lambda: ("free", "a@b.c"))
    monkeypatch.setattr(client, "_features_config", lambda: {"groups": []})
    monkeypatch.setattr(client, "_count_live_page_instances", lambda: 3)
    monkeypatch.setattr(client, "_build_surface_page_alive", lambda: False)

    # ensure_ready must never be reached: the quota error is raised first.
    async def boom(*_args, **_kwargs):
        raise AssertionError("ensure_ready should not run when quota is exceeded")

    monkeypatch.setattr(client, "_ensure_build_surface", lambda: boom())

    with pytest.raises(RequestError) as excinfo:
        agen = client.stream_generate_content(
            model="gemini-2.5-pro",
            capture_prompt="",
            surface="build",
            rest_body="{}",
        )
        async for _ in agen:
            pass
    assert excinfo.value.status == 429
    assert calls == [("free", 3, 3, "build", True)]
