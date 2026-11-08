"""账号分组、启停与预设持久化（D-05 / D-08 / D-13）。"""

from __future__ import annotations

import json
import threading

import pytest

from aistudio_api.infrastructure.account import account_store as store_module
from aistudio_api.infrastructure.account.account_store import (
    AccountPreset,
    AccountStore,
)


def _save(
    store: AccountStore,
    name: str,
    email: str,
    *,
    tier: str = "Free",
    group: str = "default",
    enabled: bool = True,
):
    return store.save_account(
        name=name,
        email=email,
        storage_state={"cookies": []},
        tier=tier,
        enabled=enabled,
        group=group,
    )


def test_account_enabled_and_group_persistence(tmp_path):
    store = AccountStore(accounts_dir=tmp_path / "accounts_group_test")

    acc = _save(store, "Test Account", "test@gmail.com", group="vip")
    assert acc.enabled is True
    assert acc.group == "vip"

    toggled = store.toggle_account_enabled(acc.id, enabled=False)
    assert toggled is not None
    assert toggled.enabled is False

    reloaded = store.get_account(acc.id)
    assert reloaded is not None
    assert reloaded.enabled is False

    grouped = store.update_account_group(acc.id, "production")
    assert grouped is not None
    assert grouped.group == "production"
    assert "production" in store.list_groups()


def test_account_preset_crud_and_apply_returns_rebind_without_rewriting_active(
    tmp_path,
):
    store = AccountStore(accounts_dir=tmp_path / "accounts_preset_test")
    acc1 = _save(store, "Acc 1", "acc1@gmail.com", tier="Free")
    acc2 = _save(store, "Acc 2", "acc2@gmail.com", tier="Pro", group="production")
    acc3 = _save(store, "Acc 3", "acc3@gmail.com", tier="Ultra", group="production")
    store.set_active_account(acc1.id)

    snapshot = store.create_snapshot_preset("My Custom Preset", "all enabled")
    assert snapshot.id.startswith("preset_")
    assert set(snapshot.account_ids) == {acc1.id, acc2.id, acc3.id}

    custom_paid = AccountPreset(
        id="preset_custom_paid",
        name="Paid Only Preset",
        action="filter_tier",
        target_tiers=["Pro", "Ultra"],
    )
    store.save_preset(custom_paid)
    assert store.get_preset("preset_custom_paid") is not None

    result = store.apply_preset("preset_custom_paid")
    assert set(result.enabled_account_ids) == {acc2.id, acc3.id}
    assert set(result.disabled_account_ids) == {acc1.id}
    assert result.rebind_required is True
    assert result.previous_active_account_id == acc1.id
    assert result.active_account_id == acc2.id
    assert result.target_account is not None
    assert result.target_account.id == acc2.id

    # D-05：不得在 rebind 前用 enabled 兜底改写 active。
    assert store.get_account(acc1.id).enabled is False  # type: ignore[union-attr]
    assert store.get_account(acc2.id).enabled is True  # type: ignore[union-attr]
    active_now = store.get_active_account()
    assert active_now is not None and active_now.id == acc1.id
    with (tmp_path / "accounts_preset_test" / "registry.json").open(
        encoding="utf-8"
    ) as fh:
        registry = json.load(fh)
    assert registry["active_account_id"] == acc1.id


def test_apply_preset_keeps_active_when_not_disabled(tmp_path):
    store = AccountStore(accounts_dir=tmp_path / "accounts_preset_keep")
    acc1 = _save(store, "Acc 1", "keep1@gmail.com", tier="Pro")
    _save(store, "Acc 2", "keep2@gmail.com", tier="Free")
    store.set_active_account(acc1.id)

    preset = AccountPreset(
        id="preset_paid_keep",
        name="Paid keep",
        action="filter_tier",
        target_tiers=["Pro"],
    )
    store.save_preset(preset)
    result = store.apply_preset(preset.id)

    assert result.rebind_required is False
    assert result.previous_active_account_id == acc1.id
    assert store.get_active_account().id == acc1.id  # type: ignore[union-attr]


def test_delete_preset_rejects_system_and_removes_custom(tmp_path):
    store = AccountStore(accounts_dir=tmp_path / "accounts_preset_delete")
    assert store.get_preset("preset_all") is not None
    with pytest.raises(ValueError):
        store.delete_preset("preset_all")

    store.save_preset(AccountPreset(id="preset_tmp", name="tmp"))
    assert store.delete_preset("preset_tmp") is True
    assert store.get_preset("preset_tmp") is None


def test_reimport_cookies_preserves_custom_metadata(tmp_path):
    """重复导入凭据时保留分组、停用状态与更高 tier。"""
    store = AccountStore(accounts_dir=tmp_path / "accounts_meta_preserve")

    acc = _save(
        store,
        "Original",
        "dev@gmail.com",
        tier="Pro",
        group="production",
        enabled=False,
    )
    updated = store.save_account(
        name="Original",
        email="dev@gmail.com",
        storage_state={"cookies": [{"name": "new_cookie"}]},
        tier="Free",
    )
    assert updated.id == acc.id
    assert updated.group == "production"
    assert updated.enabled is False
    assert updated.tier == "Pro"


def test_load_presets_reenters_lock_without_deadlock(tmp_path):
    """D-08：_load_presets 在缺文件时调用加锁的 _save_presets 不得死锁。"""
    store = AccountStore(accounts_dir=tmp_path / "accounts_lock")
    store._load_presets()
    store._presets_path.unlink()
    store._presets = None

    result: list[dict[str, AccountPreset]] = []
    thread = threading.Thread(target=lambda: result.append(store._load_presets()))
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "嵌套锁导致死锁"
    assert "preset_all" in result[0]


def test_save_presets_write_failure_raises_and_keeps_old_file(tmp_path, monkeypatch):
    """D-08：写失败必须显式抛出，且不得留下半写/清空缓存。"""
    store = AccountStore(accounts_dir=tmp_path / "accounts_write_fail")
    store._load_presets()
    before = store._presets_path.read_text(encoding="utf-8")

    def _boom(*_args, **_kwargs):
        raise PermissionError("read-only directory")

    monkeypatch.setattr(store_module, "_atomic_write_json", _boom)
    with pytest.raises(PermissionError):
        store.save_preset(AccountPreset(id="preset_new", name="new"))
    assert store._presets_path.read_text(encoding="utf-8") == before


def test_save_presets_readonly_dir_does_not_deadlock(tmp_path, monkeypatch):
    """只读目录模拟：写失败在线程内显式抛出，不死锁。"""
    store = AccountStore(accounts_dir=tmp_path / "accounts_readonly")
    store._load_presets()
    errors: list[BaseException] = []

    def _boom(*_args, **_kwargs):
        raise PermissionError("read-only directory")

    monkeypatch.setattr(store_module, "_atomic_write_json", _boom)

    def _run() -> None:
        try:
            store.save_preset(AccountPreset(id="preset_ro", name="ro"))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "只读目录写失败导致死锁"
    assert isinstance(errors[0], PermissionError)
