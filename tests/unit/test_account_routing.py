"""WP5b 账号级路由覆盖与 AccountStore 持久化测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aistudio_api.application.account_routing import (
    AccountRouting,
    apply_account_routing,
    normalize_account_routing,
)
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore

ROUTING: dict[str, object] = {
    "surfaces": ["chat"],
    "weight": 2.0,
    "model_surfaces": {"gemini-3.8-flash": "build"},
}


def _storage_state() -> dict[str, object]:
    return {"cookies": [], "origins": []}


def _make_store(tmp_path: Path) -> tuple[AccountStore, AccountMeta]:
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    account = store.save_account(
        name="Acc", email="routing@example.com", storage_state=_storage_state()
    )
    return store, account


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        "chat",
        42,
        {"surfaces": "chat"},
        {"surfaces": ("chat", "build")},
        {"model_surfaces": ["chat"]},
        {"weight": "2"},
        {"weight": True},
    ],
)
def test_normalize_invalid_inputs_fall_back_to_defaults(raw: object) -> None:
    assert normalize_account_routing(raw) == AccountRouting()


def test_normalize_surfaces_dedupes_and_preserves_first_seen_order() -> None:
    routing = normalize_account_routing(
        {"surfaces": [" BUILD ", "chat", "build", "pro", None, 3, "CHAT"]}
    )
    assert routing.surfaces == ("build", "chat")


@pytest.mark.parametrize(
    ("raw_weight", "expected"),
    [
        (0, 0.1),
        (-5, 0.1),
        (0.05, 0.1),
        (12.5, 12.5),
        (1000, 100.0),
        (None, 1.0),
        (True, 1.0),
        ("3", 1.0),
        (float("nan"), 1.0),
        (float("inf"), 100.0),
    ],
)
def test_normalize_weight_clamps_to_bounds(raw_weight: object, expected: float) -> None:
    assert normalize_account_routing({"weight": raw_weight}).weight == expected


def test_normalize_model_surfaces_strips_prefix_and_drops_invalid_entries() -> None:
    routing = normalize_account_routing(
        {
            "model_surfaces": {
                " models/gemini-3.8-flash ": "build",
                "models/gemini-3.8-pro": " CHAT ",
                "models/": "chat",
                "": "build",
                "gemini-3.8-ultra": "official",
                3: "chat",
            }
        }
    )
    assert routing.model_surfaces == {
        "gemini-3.8-flash": "build",
        "gemini-3.8-pro": "chat",
    }
    assert type(routing.model_surfaces) is dict


def test_from_dict_accepts_to_dict_roundtrip() -> None:
    routing = AccountRouting(
        surfaces=("build", "chat"),
        weight=3.5,
        model_surfaces={"gemini-3.8-flash": "build"},
    )
    assert AccountRouting.from_dict(routing.to_dict()) == routing
    assert normalize_account_routing(routing) == routing


# --------------------------------------------------------------------------- #
# 能力收窄
# --------------------------------------------------------------------------- #
def test_apply_none_routing_passthrough() -> None:
    assert apply_account_routing(["chat", "build"], None, "any") == ("chat", "build")


def test_apply_intersects_without_adding_surfaces() -> None:
    routing = AccountRouting(surfaces=("chat",))
    assert apply_account_routing(["chat"], routing, "any") == ("chat",)
    assert apply_account_routing(["chat", "build"], routing, "any") == ("chat",)


def test_apply_preserves_capable_order() -> None:
    routing = AccountRouting(surfaces=("chat", "build"))
    assert apply_account_routing(("build", "chat"), routing, "any") == (
        "build",
        "chat",
    )


def test_apply_model_pin_intersects_with_selected_surfaces() -> None:
    routing = AccountRouting(
        model_surfaces={"gemini-3.8-flash": "build"},
    )
    assert apply_account_routing(["chat", "build"], routing, "gemini-3.8-flash") == (
        "build",
    )

    narrowed = AccountRouting(
        surfaces=("chat",),
        model_surfaces={"gemini-3.8-flash": "build"},
    )
    assert apply_account_routing(["chat", "build"], narrowed, "gemini-3.8-flash") == ()


def test_apply_model_pin_not_capable_is_empty() -> None:
    routing = AccountRouting(model_surfaces={"gemini-3.8-flash": "build"})
    assert apply_account_routing(["chat"], routing, "gemini-3.8-flash") == ()


def test_apply_normalizes_model_lookup() -> None:
    routing = AccountRouting(model_surfaces={"gemini-3.8-flash": "build"})
    assert apply_account_routing(
        ["chat", "build"], routing, " models/gemini-3.8-flash "
    ) == ("build",)


# --------------------------------------------------------------------------- #
# AccountMeta 持久化字段
# --------------------------------------------------------------------------- #
def test_account_meta_roundtrip_includes_routing() -> None:
    meta = AccountMeta(
        id="a",
        name="n",
        email="e@x.com",
        created_at="t",
        routing=dict(ROUTING),
    )
    restored = AccountMeta.from_dict(json.loads(json.dumps(meta.to_dict())))
    assert restored == meta
    assert restored.routing == ROUTING


def test_account_meta_invalid_routing_falls_back_to_empty() -> None:
    meta = AccountMeta.from_dict(
        {"id": "a", "name": "n", "email": None, "created_at": "t", "routing": []}
    )
    assert meta.routing == {}


# --------------------------------------------------------------------------- #
# AccountStore CRUD 与重写存活
# --------------------------------------------------------------------------- #
def test_store_get_update_clear_routing(tmp_path: Path) -> None:
    store, account = _make_store(tmp_path)

    assert store.get_account_routing(account.id) == {}
    assert store.update_account_routing(account.id, ROUTING) is True

    fetched = store.get_account_routing(account.id)
    assert fetched == ROUTING
    fetched["surfaces"] = ["build"]
    assert store.get_account_routing(account.id) == ROUTING

    meta_path = tmp_path / "accounts" / account.id / "meta.json"
    assert json.loads(meta_path.read_text(encoding="utf-8"))["routing"] == ROUTING

    assert store.clear_account_routing(account.id) is True
    assert store.get_account_routing(account.id) == {}
    assert json.loads(meta_path.read_text(encoding="utf-8"))["routing"] == {}


def test_store_unknown_account_routing(tmp_path: Path) -> None:
    store, _ = _make_store(tmp_path)

    assert store.get_account_routing("missing") == {}
    assert store.update_account_routing("missing", ROUTING) is False
    assert store.clear_account_routing("missing") is False


def test_routing_survives_refresh_and_keepalive_rewrites(tmp_path: Path) -> None:
    store, account = _make_store(tmp_path)
    assert store.update_account_routing(account.id, ROUTING) is True

    assert store.update_account_refresh(account.id, tier="Pro") is True
    refreshed = store.get_account(account.id)
    assert refreshed is not None
    assert refreshed.routing == ROUTING

    assert store.update_account_keepalive(account.id, last_refresh_error="boom") is True
    kept = store.get_account(account.id)
    assert kept is not None
    assert kept.routing == ROUTING

    meta_path = tmp_path / "accounts" / account.id / "meta.json"
    assert json.loads(meta_path.read_text(encoding="utf-8"))["routing"] == ROUTING
    registry_path = tmp_path / "accounts" / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry["accounts"][account.id]["routing"] == ROUTING
