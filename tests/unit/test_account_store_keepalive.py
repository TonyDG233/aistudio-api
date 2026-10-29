"""Storage-level keepalive behavior tests."""

from __future__ import annotations

import json

from aistudio_api.infrastructure.account.account_store import (
    AccountStore,
    write_storage_state,
)


def test_mark_auth_saved_and_refresh_attempt_persist_to_meta(tmp_path):
    store = AccountStore(tmp_path)
    account = store.save_account(
        "A",
        "a@example.com",
        {"cookies": [{"name": "SID", "value": "old"}]},
        account_id="acc_a",
    )
    store.update_account_keepalive(account.id, auth_saved_at=None)
    assert store.get_account(account.id).auth_saved_at is None  # type: ignore[union-attr]

    assert store.mark_auth_saved(account.id, at="2026-10-30T00:00:00+00:00") is True
    assert (
        store.mark_refresh_attempt(
            account.id,
            error="HTTP 503",
            at="2026-10-30T00:01:00+00:00",
        )
        is True
    )

    updated = store.get_account(account.id)
    assert updated is not None
    assert updated.auth_saved_at == "2026-10-30T00:00:00+00:00"
    assert updated.last_refresh_attempt_at == "2026-10-30T00:01:00+00:00"
    assert updated.last_refresh_error == "HTTP 503"

    meta = json.loads((tmp_path / account.id / "meta.json").read_text(encoding="utf-8"))
    assert meta["auth_saved_at"] == "2026-10-30T00:00:00+00:00"
    assert meta["last_refresh_attempt_at"] == "2026-10-30T00:01:00+00:00"
    assert meta["last_refresh_error"] == "HTTP 503"


def test_keepalive_update_for_unknown_account_is_noop(tmp_path):
    store = AccountStore(tmp_path)

    assert store.mark_auth_saved("acc_missing") is False
    assert store.mark_refresh_attempt("acc_missing", error="x") is False


def test_write_storage_state_preserves_non_cookie_fields(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "cookies": [{"name": "old", "value": "old"}],
                "origins": [{"origin": "https://aistudio.google.com"}],
                "accountName": "keep-me",
                "custom": {"flag": True},
            }
        ),
        encoding="utf-8",
    )

    write_storage_state(
        auth_path,
        cookies=[{"name": "SID", "value": "new"}],
    )

    data = json.loads(auth_path.read_text(encoding="utf-8"))
    assert data["cookies"] == [{"name": "SID", "value": "new"}]
    assert data["origins"] == [{"origin": "https://aistudio.google.com"}]
    assert data["accountName"] == "keep-me"
    assert data["custom"] == {"flag": True}
