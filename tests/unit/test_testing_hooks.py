"""Deterministic 429 switch-hook contracts."""

from __future__ import annotations

from aistudio_api.application.testing_hooks import forced_429, reset_test_hooks


def test_hook_is_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("AISTUDIO_TEST_HOOKS", raising=False)
    reset_test_hooks()
    assert forced_429(account_id="a", model="models/flash", surface="chat") is False


def test_hook_matches_one_account_and_count(monkeypatch) -> None:
    monkeypatch.setenv("AISTUDIO_TEST_HOOKS", "1")
    monkeypatch.setenv("AISTUDIO_TEST_FORCE_429_ACCOUNT", "a")
    monkeypatch.setenv("AISTUDIO_TEST_FORCE_429_MODEL", "flash")
    monkeypatch.setenv("AISTUDIO_TEST_FORCE_429_SURFACE", "chat")
    monkeypatch.setenv("AISTUDIO_TEST_FORCE_429_COUNT", "1")
    reset_test_hooks()

    assert forced_429(account_id="b", model="models/flash", surface="chat") is False
    assert forced_429(account_id="a", model="models/flash", surface="build") is False
    assert forced_429(account_id="a", model="models/flash", surface="chat") is True
    assert forced_429(account_id="a", model="models/flash", surface="chat") is False
