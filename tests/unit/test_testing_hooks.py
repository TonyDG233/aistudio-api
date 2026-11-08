"""D05-043: forced-429 hook is inert unless AISTUDIO_TEST_HOOKS=1."""

from __future__ import annotations

import pytest

from aistudio_api.application.testing_hooks import forced_429, reset_test_hooks


@pytest.fixture(autouse=True)
def _clean_hooks(monkeypatch):
    for name in (
        "AISTUDIO_TEST_HOOKS",
        "AISTUDIO_TEST_FORCE_429_ACCOUNT",
        "AISTUDIO_TEST_FORCE_429_MODEL",
        "AISTUDIO_TEST_FORCE_429_SURFACE",
        "AISTUDIO_TEST_FORCE_429_COUNT",
    ):
        monkeypatch.delenv(name, raising=False)
    reset_test_hooks()
    yield
    reset_test_hooks()


def test_hook_disabled_by_default() -> None:
    assert not forced_429(account_id="acc", model="m", surface="chat")


def test_hook_count_and_reset(monkeypatch) -> None:
    monkeypatch.setenv("AISTUDIO_TEST_HOOKS", "1")
    monkeypatch.setenv("AISTUDIO_TEST_FORCE_429_COUNT", "2")
    assert forced_429(account_id="acc", model="m", surface="chat")
    assert forced_429(account_id="acc", model="m", surface="chat")
    assert not forced_429(account_id="acc", model="m", surface="chat")
    reset_test_hooks()
    assert forced_429(account_id="acc", model="m", surface="chat")


def test_hook_filters_account_model_surface(monkeypatch) -> None:
    monkeypatch.setenv("AISTUDIO_TEST_HOOKS", "1")
    monkeypatch.setenv("AISTUDIO_TEST_FORCE_429_ACCOUNT", "acc-2")
    monkeypatch.setenv("AISTUDIO_TEST_FORCE_429_MODEL", "models/gemini-3.7-flash")
    monkeypatch.setenv("AISTUDIO_TEST_FORCE_429_SURFACE", "chat")
    assert not forced_429(account_id="acc-1", model="gemini-3.7-flash", surface="chat")
    assert not forced_429(account_id="acc-2", model="gemini-3.7-flash", surface="build")
    assert forced_429(account_id="acc-2", model="models/gemini-3.7-flash", surface="chat")
