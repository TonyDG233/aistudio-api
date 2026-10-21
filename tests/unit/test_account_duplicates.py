"""重复账号标记（同一邮箱多条）的存储层测试。"""

from __future__ import annotations

from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore


def _save(store: AccountStore, name: str, email: str, auth_user: str = "0") -> AccountMeta:
    return store.save_account(
        name=name,
        email=email,
        storage_state={"cookies": [], "origins": []},
        auth_user=auth_user,
    )


def _get(store: AccountStore, account_id: str) -> AccountMeta:
    account = store.get_account(account_id)
    assert account is not None
    return account


def test_refresh_duplicate_flags_marks_later_account(tmp_path):
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    first = _save(store, "first", "User@Example.com")
    second = _save(store, "second", "user@example.com", auth_user="1")

    assert store.refresh_duplicate_flags() == 1
    assert _get(store, first.id).duplicate_of is None
    assert _get(store, second.id).duplicate_of == first.id


def test_duplicate_flags_cleared_after_deleting_canonical(tmp_path):
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    first = _save(store, "first", "dup@example.com")
    second = _save(store, "second", "dup@example.com", auth_user="1")
    assert store.refresh_duplicate_flags() == 1

    assert store.delete_account(first.id) is True
    assert _get(store, second.id).duplicate_of is None
    assert store.refresh_duplicate_flags() == 0


def test_unique_email_has_no_duplicate_flag(tmp_path):
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    account = _save(store, "only", "solo@example.com")

    assert store.refresh_duplicate_flags() == 0
    assert _get(store, account.id).duplicate_of is None


def test_duplicate_flag_persists_across_reload(tmp_path):
    accounts_dir = tmp_path / "accounts"
    store = AccountStore(accounts_dir=accounts_dir)
    first = _save(store, "first", "persist@example.com")
    second = _save(store, "second", "persist@example.com", auth_user="1")
    store.refresh_duplicate_flags()

    reloaded = AccountStore(accounts_dir=accounts_dir)
    assert _get(reloaded, first.id).duplicate_of is None
    assert _get(reloaded, second.id).duplicate_of == first.id
