"""账号信息刷新（tier/过期/邮箱 + 批量并行 + 调度排除）单元测试。

所有 HTTP 交互经注入的 FakeTransport 离线完成，不发任何真实网络请求。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import HTTPException

from aistudio_api.api.app import app
from aistudio_api.api.routes_accounts import (
    RefreshAccountsRequest,
    get_account_info_refresher,
    refresh_accounts,
    to_account_response,
)
from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.infrastructure.account.account_info_refresher import (
    AccountInfoRefresher,
    RefreshInProgressError,
    RefreshResult,
    TransportResponse,
)
from aistudio_api.infrastructure.account.account_store import (
    AccountMeta,
    AccountStore,
)

NEW_KEY = "AIzaSyDdP8" + "A" * 30


@dataclass
class RecordedRequest:
    method: str
    url: str
    headers: dict[str, str]
    content: str


class FakeTransport:
    """脚本化替身传输：按顺序返回预置响应，并记录最大并发数。"""

    def __init__(self, responses: list[TransportResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[RecordedRequest] = []
        self.max_active = 0
        self._active = 0

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        content: str = "",
    ) -> TransportResponse:
        self.requests.append(RecordedRequest(method, url, dict(headers), content))
        self._active += 1
        self.max_active = max(self.max_active, self._active)
        try:
            await asyncio.sleep(0.01)
            if not self.responses:
                raise AssertionError("FakeTransport: no scripted response left")
            return self.responses.pop(0)
        finally:
            self._active -= 1


def _storage_state() -> dict[str, object]:
    return {
        "cookies": [
            {"name": "SAPISID", "value": "sap", "domain": ".google.com", "path": "/"},
            {
                "name": "__Secure-1PAPISID",
                "value": "p1",
                "domain": ".google.com",
                "path": "/",
            },
            {
                "name": "__Secure-3PAPISID",
                "value": "p3",
                "domain": ".google.com",
                "path": "/",
            },
            {"name": "SID", "value": "sid", "domain": ".google.com", "path": "/"},
        ],
        "origins": [],
    }


def _make_store(tmp_path: Path) -> tuple[AccountStore, AccountMeta]:
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    account = store.save_account(
        name="Acc", email="existing@gmail.com", storage_state=_storage_state()
    )
    return store, account


# --------------------------------------------------------------------------- #
# tier 映射
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("[]", "Free"),
        ("[0]", "Free"),
        ("[null]", "Free"),
        ("[1]", "Pro"),
        ("[2]", "Ultra"),
        ("[3]", "Plus"),
    ],
)
def test_tier_mapping(tmp_path: Path, body: str, expected: str) -> None:
    store, account = _make_store(tmp_path)
    transport = FakeTransport([TransportResponse(status=200, body=body)])
    refresher = AccountInfoRefresher(store, transport=transport)

    result = asyncio.run(refresher.refresh_account(account, refresh_email=False))

    assert result.ok is True
    assert result.expired is False
    assert result.tier == expected
    stored = store.get_account(account.id)
    assert stored is not None
    assert stored.tier == expected
    assert stored.expired is False


@pytest.mark.parametrize("existing_tier", ["Pro", "Ultra"])
@pytest.mark.parametrize(
    "body",
    [
        "[9]",
        "not-json",
        "null",
        '[null, "error message"]',
    ],
)
def test_malformed_200_preserves_existing_tier_and_skips_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    existing_tier: str,
) -> None:
    store, account = _make_store(tmp_path)
    store.update_account_refresh(
        account.id,
        tier=existing_tier,
        tier_checked_at="2026-01-01T00:00:00Z",
        expired=False,
    )
    current = store.get_account(account.id)
    assert current is not None
    update_mock = MagicMock(wraps=store.update_account_refresh)
    monkeypatch.setattr(store, "update_account_refresh", update_mock)
    transport = FakeTransport([TransportResponse(status=200, body=body)])
    refresher = AccountInfoRefresher(store, transport=transport)

    result = asyncio.run(refresher.refresh_account(current, refresh_email=False))

    assert result.ok is False
    assert result.tier is None
    assert result.expired is None
    update_mock.assert_not_called()
    stored = store.get_account(account.id)
    assert stored is not None
    assert stored.tier == existing_tier
    assert stored.tier_checked_at == "2026-01-01T00:00:00Z"
    assert stored.expired is False


def test_batch_continues_after_malformed_200(tmp_path: Path) -> None:
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    pro = store.save_account(
        name="Pro", email="pro@example.com", storage_state=_storage_state(), tier="Pro"
    )
    ultra = store.save_account(
        name="Ultra",
        email="ultra@example.com",
        storage_state=_storage_state(),
        tier="Ultra",
    )
    accounts = [pro, ultra]
    transport = FakeTransport(
        [
            TransportResponse(status=200, body="not-json"),
            TransportResponse(status=200, body="[9]"),
        ]
    )
    refresher = AccountInfoRefresher(store, transport=transport, concurrency=2)

    async def _run() -> list[RefreshResult]:
        return await refresher.refresh_accounts(accounts, refresh_email=False)

    results = asyncio.run(_run())

    assert len(results) == 2
    assert all(result.ok is False for result in results)
    assert all(result.tier is None for result in results)
    assert store.get_account(pro.id).tier == "Pro"  # type: ignore[union-attr]
    assert store.get_account(ultra.id).tier == "Ultra"  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# 多身份账号：x-goog-authuser 必须跟随 account.auth_user
# --------------------------------------------------------------------------- #
def test_refresh_uses_account_auth_user(tmp_path: Path) -> None:
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    account = store.save_account(
        name="Acc u1",
        email="second@gmail.com",
        storage_state=_storage_state(),
        auth_user="1",
    )
    transport = FakeTransport([TransportResponse(status=200, body="[1]")])
    refresher = AccountInfoRefresher(store, transport=transport)

    result = asyncio.run(refresher.refresh_account(account, refresh_email=False))

    assert result.ok is True
    assert transport.requests[0].headers["x-goog-authuser"] == "1"
    assert transport.requests[0].headers["x-goog-ext-519733851-bin"]
    assert transport.requests[0].headers["x-ai-studio-visit-id"].startswith("v1_")
    assert "authorization" in transport.requests[0].headers


def test_email_fetch_uses_account_auth_user(tmp_path: Path) -> None:
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    account = store.save_account(
        name="Acc u1",
        email="second@gmail.com",
        storage_state=_storage_state(),
        auth_user="1",
    )
    html = '<script>{"oPEP7c":"second@gmail.com"}</script>'
    transport = FakeTransport(
        [
            TransportResponse(status=200, body="[1]"),
            TransportResponse(status=200, body=html),
        ]
    )
    refresher = AccountInfoRefresher(store, transport=transport)

    result = asyncio.run(refresher.refresh_account(account, refresh_email=True))

    assert result.ok is True
    page_request = transport.requests[1]
    assert page_request.headers["x-goog-authuser"] == "1"
    assert "authuser=1" in page_request.url


# --------------------------------------------------------------------------- #
# 401 -> 过期；5xx -> 不改状态
# --------------------------------------------------------------------------- #
def test_401_marks_expired(tmp_path: Path) -> None:
    store, account = _make_store(tmp_path)
    transport = FakeTransport(
        [TransportResponse(status=401, body='[16,"Login required"]')]
    )
    refresher = AccountInfoRefresher(store, transport=transport)

    result = asyncio.run(refresher.refresh_account(account))

    assert result.ok is False
    assert result.expired is True
    assert result.tier is None
    assert "401" in (result.error or "")
    assert "Login required" in (result.error or "")
    stored = store.get_account(account.id)
    assert stored is not None
    assert stored.expired is True
    resp = to_account_response(stored)
    assert resp.expired is True


def test_401_cleared_after_recovery(tmp_path: Path) -> None:
    """先标记过期，再刷新成功应清除 expired。"""
    store, account = _make_store(tmp_path)
    store.update_account_refresh(account.id, expired=True)
    transport = FakeTransport([TransportResponse(status=200, body="[1]")])
    refresher = AccountInfoRefresher(store, transport=transport)

    result = asyncio.run(refresher.refresh_account(account, refresh_email=False))

    assert result.ok is True
    stored = store.get_account(account.id)
    assert stored is not None
    assert stored.expired is False
    assert stored.tier == "Pro"


def test_server_error_does_not_change_state(tmp_path: Path) -> None:
    store, account = _make_store(tmp_path)
    store.update_account_refresh(
        account.id, tier="Pro", tier_checked_at="2026-01-01T00:00:00Z"
    )
    transport = FakeTransport([TransportResponse(status=500, body="boom")])
    refresher = AccountInfoRefresher(store, transport=transport)

    result = asyncio.run(refresher.refresh_account(account))

    assert result.ok is False
    assert result.expired is None
    assert "500" in (result.error or "")
    stored = store.get_account(account.id)
    assert stored is not None
    assert stored.expired is False
    assert stored.tier == "Pro"
    assert stored.tier_checked_at == "2026-01-01T00:00:00Z"


# --------------------------------------------------------------------------- #
# 403 疑 key 被拒 -> 回收 apps 页 key 重试一次
# --------------------------------------------------------------------------- #
def test_403_key_blocked_recovers_and_retries_once(tmp_path: Path) -> None:
    store, account = _make_store(tmp_path)
    html = f'<script>WIZ_global_data = {{"k":"{NEW_KEY}"}};</script>'
    transport = FakeTransport(
        [
            TransportResponse(status=403, body="API_KEY_SERVICE_BLOCKED"),
            TransportResponse(status=200, body=html),
            TransportResponse(status=200, body="[2]"),
        ]
    )
    refresher = AccountInfoRefresher(store, transport=transport, app_id="app-x")

    result = asyncio.run(refresher.refresh_account(account, refresh_email=False))

    assert result.ok is True
    assert result.tier == "Ultra"

    rpc_calls = [r for r in transport.requests if r.method == "POST"]
    assert len(rpc_calls) == 2
    assert rpc_calls[0].headers.get("x-goog-api-key") != NEW_KEY
    assert rpc_calls[1].headers.get("x-goog-api-key") == NEW_KEY
    page_calls = [r for r in transport.requests if r.method == "GET"]
    assert len(page_calls) == 1


def test_403_non_key_does_not_retry(tmp_path: Path) -> None:
    store, account = _make_store(tmp_path)
    transport = FakeTransport([TransportResponse(status=403, body="PERMISSION_DENIED")])
    refresher = AccountInfoRefresher(store, transport=transport)

    result = asyncio.run(refresher.refresh_account(account))

    assert result.ok is False
    assert result.expired is None
    assert len([r for r in transport.requests if r.method == "POST"]) == 1


# --------------------------------------------------------------------------- #
# 邮箱回填（真实 HTML 特征 fixture）
# --------------------------------------------------------------------------- #
ARK_APP_HTML = (
    "<!doctype html><html><head><script>"
    'window.WIZ_global_data = {"oPEP7c":"tonygemini38@gmail.com","bLBL6b":"x"};'
    "</script></head><body>"
    '<a href="mailto:helper@schema.org">helper</a>'
    '<a href="mailto:noreply@example.com">n</a>'
    '<a href="mailto:support@google.com">s</a>'
    "</body></html>"
)

FALLBACK_APP_HTML = (
    "<html><body>"
    '<a href="mailto:helper@schema.org">h</a>'
    '<a href="mailto:noise@example.com">n</a>'
    '<a href="mailto:support@google.com">s</a>'
    '<a href="mailto:real.user@gmail.com">r</a>'
    "</body></html>"
)


def test_email_backfill_from_wiz_global_data(tmp_path: Path) -> None:
    store, account = _make_store(tmp_path)
    transport = FakeTransport(
        [
            TransportResponse(status=200, body="[]"),
            TransportResponse(status=200, body=ARK_APP_HTML),
        ]
    )
    refresher = AccountInfoRefresher(store, transport=transport)

    result = asyncio.run(refresher.refresh_account(account))

    assert result.email == "tonygemini38@gmail.com"
    stored = store.get_account(account.id)
    assert stored is not None
    assert stored.email == "tonygemini38@gmail.com"


def test_email_backfill_fallback_filters_noise_domains(tmp_path: Path) -> None:
    store, account = _make_store(tmp_path)
    transport = FakeTransport(
        [
            TransportResponse(status=200, body="[]"),
            TransportResponse(status=200, body=FALLBACK_APP_HTML),
        ]
    )
    refresher = AccountInfoRefresher(store, transport=transport)

    result = asyncio.run(refresher.refresh_account(account))

    assert result.email == "real.user@gmail.com"


def test_email_backfill_prefers_existing_name(tmp_path: Path) -> None:
    store, account = _make_store(tmp_path)
    store.update_account_refresh(account.id, email="alice@corp.com")
    html = "<html><body>alice@corp.com bob@corp.com</body></html>"
    transport = FakeTransport(
        [
            TransportResponse(status=200, body="[1]"),
            TransportResponse(status=200, body=html),
        ]
    )
    refresher = AccountInfoRefresher(store, transport=transport)
    account = store.get_account(account.id)
    assert account is not None

    result = asyncio.run(refresher.refresh_account(account))

    assert result.email == "alice@corp.com"


# --------------------------------------------------------------------------- #
# 批量并发 + 409 互斥
# --------------------------------------------------------------------------- #
def test_batch_concurrency_bounded(tmp_path: Path) -> None:
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    accounts = [
        store.save_account(
            name=f"A{i}", email=f"a{i}@x.com", storage_state=_storage_state()
        )
        for i in range(5)
    ]
    transport = FakeTransport(
        [TransportResponse(status=200, body="[]") for _ in accounts]
    )
    refresher = AccountInfoRefresher(store, transport=transport, concurrency=3)

    async def _run() -> list[RefreshResult]:
        return await refresher.refresh_accounts(accounts, refresh_email=False)

    results = asyncio.run(_run())

    assert len(results) == 5
    assert transport.max_active <= 3
    assert transport.max_active >= 2
    assert refresher.is_refreshing() is False


def test_batch_mutual_exclusion_raises(tmp_path: Path) -> None:
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    accounts = [
        store.save_account(
            name=f"A{i}", email=f"a{i}@x.com", storage_state=_storage_state()
        )
        for i in range(3)
    ]
    transport = FakeTransport(
        [TransportResponse(status=200, body="[]") for _ in accounts]
    )
    refresher = AccountInfoRefresher(store, transport=transport, concurrency=1)

    async def _run() -> None:
        task = asyncio.create_task(
            refresher.refresh_accounts(accounts, refresh_email=False)
        )
        await asyncio.sleep(0)
        with pytest.raises(RefreshInProgressError):
            await refresher.refresh_accounts(accounts, refresh_email=False)
        await task

    asyncio.run(_run())


# --------------------------------------------------------------------------- #
# AccountMeta 向后兼容
# --------------------------------------------------------------------------- #
def test_account_meta_backward_compatible_defaults() -> None:
    meta = AccountMeta.from_dict(
        {"id": "a", "name": "n", "email": None, "created_at": "t"}
    )
    assert meta.tier == "Free"
    assert meta.tier_checked_at is None
    assert meta.expired is False
    assert meta.duplicate_of is None
    data = meta.to_dict()
    assert data["tier"] == "Free"
    assert data["tier_checked_at"] is None
    assert data["expired"] is False


def test_account_meta_roundtrip_with_refresh_fields() -> None:
    meta = AccountMeta(
        id="a",
        name="n",
        email="e@x.com",
        created_at="t",
        tier="Pro",
        tier_checked_at="2026-10-18T00:00:00Z",
        expired=True,
        duplicate_of="keeper",
    )
    restored = AccountMeta.from_dict(json.loads(json.dumps(meta.to_dict())))
    assert restored == meta


def test_update_account_refresh_persists(tmp_path: Path) -> None:
    store, account = _make_store(tmp_path)
    assert (
        store.update_account_refresh(
            account.id, tier="Plus", tier_checked_at="ts", expired=True
        )
        is True
    )
    assert (
        store.update_account_refresh(
            account.id, tier="Plus", tier_checked_at="ts", expired=True
        )
        is False
    )
    meta_path = tmp_path / "accounts" / account.id / "meta.json"
    on_disk = json.loads(meta_path.read_text(encoding="utf-8"))
    assert on_disk["tier"] == "Plus"
    assert on_disk["expired"] is True
    assert store.update_account_refresh("missing", tier="Free") is False


# --------------------------------------------------------------------------- #
# 调度排除 expired
# --------------------------------------------------------------------------- #
def _rotator_with(accounts: list[AccountMeta]) -> AccountRotator:
    store = MagicMock(spec=AccountStore)
    store.list_accounts.return_value = accounts
    store.get_account.side_effect = lambda aid: next(
        (a for a in accounts if a.id == aid), None
    )
    return AccountRotator(account_store=store)


def test_dispatch_excludes_expired_account() -> None:
    expired = AccountMeta(
        id="acc_e", name="E", email="e@x.com", created_at="t", expired=True
    )
    healthy = AccountMeta(id="acc_h", name="H", email="h@x.com", created_at="t")
    rotator = _rotator_with([expired, healthy])

    async def _run() -> None:
        nxt = await rotator.get_next_account(current_account_id="acc_e")
        assert nxt is not None and nxt.id == "acc_h"
        assert rotator.is_account_available("acc_e") is False
        assert rotator.is_account_available("acc_h") is True

    asyncio.run(_run())


def test_dispatch_all_expired_returns_none() -> None:
    only = AccountMeta(
        id="acc_e", name="E", email="e@x.com", created_at="t", expired=True
    )
    rotator = _rotator_with([only])

    async def _run() -> None:
        assert await rotator.get_next_account() is None

    asyncio.run(_run())


def test_is_account_available_false_for_expired() -> None:
    account = AccountMeta(
        id="acc_e", name="E", email=None, created_at="t", expired=True
    )
    rotator = _rotator_with([account])
    assert rotator.is_account_available("acc_e", model="gemini-2.5-pro") is False


# --------------------------------------------------------------------------- #
# 路由：POST /accounts/refresh
# --------------------------------------------------------------------------- #
class _FakeRefresher(AccountInfoRefresher):
    """惰性替身：绕过真实传输，验证路由编排/互斥翻译。"""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.calls: list[tuple[list[str], bool]] = []

    async def refresh_accounts(
        self,
        accounts: list[AccountMeta],
        *,
        refresh_email: bool = True,
        skip_fresh_free_tier: bool = False,
    ) -> list[RefreshResult]:
        if self._raises is not None:
            raise self._raises
        self.calls.append(([a.id for a in accounts], refresh_email))
        return [
            RefreshResult(
                account_id=a.id, ok=True, tier="Pro", expired=False, email=a.email
            )
            for a in accounts
        ]


@pytest.fixture()
def fake_service():
    svc = MagicMock()
    accs = {
        "acc_1": AccountMeta(id="acc_1", name="A1", email="a1@x.com", created_at="t"),
        "acc_2": AccountMeta(
            id="acc_2", name="A2", email=None, created_at="t", expired=True
        ),
    }
    svc.list_accounts.return_value = list(accs.values())
    svc.get_account.side_effect = lambda aid: accs.get(aid)
    orig = runtime_state.account_service
    runtime_state.account_service = svc
    try:
        yield svc
    finally:
        runtime_state.account_service = orig


def test_refresh_route_requires_ids_or_all(fake_service) -> None:
    refresher = _FakeRefresher()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            refresh_accounts(
                RefreshAccountsRequest(),
                account_service=fake_service,
                refresher=refresher,
            )
        )
    assert exc.value.status_code == 400


def test_refresh_route_all_returns_results(fake_service) -> None:
    refresher = _FakeRefresher()
    resp = asyncio.run(
        refresh_accounts(
            RefreshAccountsRequest(all=True),
            account_service=fake_service,
            refresher=refresher,
        )
    )
    assert resp.refreshed_count == 2
    assert {r.id for r in resp.results} == {"acc_1", "acc_2"}
    assert all(r.ok for r in resp.results)


def test_refresh_route_unknown_id_reports_error(fake_service) -> None:
    refresher = _FakeRefresher()
    resp = asyncio.run(
        refresh_accounts(
            RefreshAccountsRequest(account_ids=["acc_1", "ghost"]),
            account_service=fake_service,
            refresher=refresher,
        )
    )
    by_id = {r.id: r for r in resp.results}
    assert by_id["ghost"].ok is False
    assert by_id["ghost"].error == "账号不存在"
    assert refresher.calls[0][0] == ["acc_1"]


def test_refresh_route_409_when_in_progress(fake_service) -> None:
    refresher = _FakeRefresher(raises=RefreshInProgressError("already running"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            refresh_accounts(
                RefreshAccountsRequest(all=True),
                account_service=fake_service,
                refresher=refresher,
            )
        )
    assert exc.value.status_code == 409


def test_refresh_route_via_asgi_409(fake_service) -> None:
    """通过真实 ASGI 栈验证依赖覆盖 + 409（互斥语义）。"""
    fake = _FakeRefresher(raises=RefreshInProgressError("running"))
    app.dependency_overrides[get_account_info_refresher] = lambda: fake
    try:

        async def _run() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                return await client.post("/api/accounts/refresh", json={"all": True})

        resp = asyncio.run(_run())
        assert resp.status_code == 409
    finally:
        app.dependency_overrides.pop(get_account_info_refresher, None)


def test_account_response_includes_tier_and_expired() -> None:
    meta = AccountMeta(
        id="a",
        name="n",
        email="e@x.com",
        created_at="t",
        tier="Ultra",
        expired=True,
    )
    resp = to_account_response(meta)
    assert resp.tier == "Ultra"
    assert resp.expired is True
