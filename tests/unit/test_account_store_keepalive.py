"""Storage-level keepalive behavior tests."""

from __future__ import annotations

import json

from aistudio_api.infrastructure.account.account_store import write_storage_state


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
