"""Unit tests for Tampermonkey script serving, bundle import formats, and cookie rotation/sibling sync."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.account.cookie_parser import (
    load_cookies_from_auth_file,
    parse_storage_state,
    rotate_google_cookies,
)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_tampermonkey_script_route():
    """验证 GET /api/tools/... 与 /tools/... 提供油猴脚本且无 attachment 头。"""
    async with _client() as client:
        for route in (
            "/api/tools/aistudio-cookie-grabber.user.js",
            "/tools/aistudio-cookie-grabber.user.js",
        ):
            resp = await client.get(route)
            assert resp.status_code == 200
            assert "text/javascript" in resp.headers.get("content-type", "")
            # 必须无 attachment，以允许浏览器 Tampermonkey 自动识别安装
            disposition = resp.headers.get("content-disposition", "")
            assert "attachment" not in disposition.lower()
            assert "no-store" in resp.headers.get("cache-control", "")
            assert "// ==UserScript==" in resp.text
            assert "cookieObjects" in resp.text
            assert "下载凭据包" in resp.text


@pytest.mark.asyncio
async def test_import_bundle_all_four_formats(tmp_path: Path):
    """验证 import-bundle 接口对 4 种 JSON 格式的解析，并验证跨域 domain 保真。"""
    from aistudio_api.api.ui_sandbox import UISandbox

    store = AccountStore(tmp_path)
    service = AccountService(store)
    orig_service = runtime_state.account_service
    orig_debug = runtime_state.ui_debug
    orig_sandbox = runtime_state.ui_sandbox

    runtime_state.account_service = service
    runtime_state.ui_debug = True
    runtime_state.ui_sandbox = UISandbox()

    try:
        async with _client() as client:
            # 1. 单账号 Playwright storageState（如 auth-49.json）
            auth49_data = {
                "accountName": "worker49@gmail.com",
                "cookies": [
                    {
                        "name": "SID",
                        "value": "sid_val_49",
                        "domain": ".google.com",
                        "path": "/",
                        "secure": True,
                        "httpOnly": True,
                    },
                    {
                        "name": "SAPISID",
                        "value": "sapisid_val_49",
                        "domain": ".google.com",
                        "path": "/",
                        "secure": True,
                        "httpOnly": False,
                    },
                    {
                        "name": "LSID",
                        "value": "lsid_val_49",
                        "domain": "accounts.google.com",
                        "path": "/",
                        "secure": True,
                        "httpOnly": True,
                    },
                    {
                        "name": "__Host-1PLSID",
                        "value": "host_lsid_49",
                        "domain": "accounts.google.com",
                        "path": "/",
                        "secure": True,
                        "httpOnly": True,
                    },
                ],
                "origins": [
                    {
                        "origin": "https://aistudio.google.com",
                        "localStorage": [],
                    }
                ],
            }

            resp = await client.post(
                "/api/accounts/import-bundle",
                json={"content": json.dumps(auth49_data)},
            )
            assert resp.status_code == 200, resp.text
            res_data = resp.json()
            assert res_data["imported_count"] == 1
            acc_id = res_data["accounts"][0]["id"]
            acc = store.get_account(acc_id)
            assert acc is not None
            assert acc.email == "worker49@gmail.com"
            assert acc.name == "worker49@gmail.com"

            # 校验 auth.json 中 accounts.google.com 的 domain 100% 保真
            auth_file = store.get_auth_path(acc_id)
            assert auth_file is not None
            saved_state = parse_storage_state(auth_file.read_text(encoding="utf-8"))
            assert saved_state is not None
            saved_cookies: list[dict[str, Any]] = saved_state.get("cookies", [])  # type: ignore[assignment]
            lsid_item = next(
                (c for c in saved_cookies if c.get("name") == "LSID"), None
            )
            assert lsid_item is not None
            assert lsid_item["domain"] == "accounts.google.com"
            host_lsid_item = next(
                (c for c in saved_cookies if c.get("name") == "__Host-1PLSID"), None
            )
            assert host_lsid_item is not None
            assert host_lsid_item["domain"] == "accounts.google.com"

            # 2. 油猴 Bundle 字典（含 accounts 数组、authUser、g1Tier、cookieObjects）
            tamper_data = {
                "bundle": True,
                "accounts": [
                    {
                        "name": "Pro User 1",
                        "email": "prouser1@gmail.com",
                        "authUser": 1,
                        "g1Tier": "TIER1",
                        "cookies": "SID=pro_sid_1; SAPISID=pro_sapisid_1",
                        "cookieObjects": [
                            {
                                "name": "SID",
                                "value": "pro_sid_1",
                                "domain": ".google.com",
                                "path": "/",
                            },
                            {
                                "name": "SAPISID",
                                "value": "pro_sapisid_1",
                                "domain": ".google.com",
                                "path": "/",
                            },
                            {
                                "name": "LSID",
                                "value": "pro_lsid_1",
                                "domain": "accounts.google.com",
                                "path": "/",
                            },
                        ],
                    }
                ],
            }
            resp = await client.post(
                "/api/accounts/import-bundle",
                json={"content": json.dumps(tamper_data)},
            )
            assert resp.status_code == 200, resp.text
            res_data = resp.json()
            assert res_data["imported_count"] == 1
            tamper_acc = res_data["accounts"][0]
            assert tamper_acc["email"] == "prouser1@gmail.com"
            assert tamper_acc["auth_user"] == "1"
            assert tamper_acc["tier"] == "Pro"

            # 3. Cookie-Editor 导出的纯 Cookie 数组
            cookie_editor_data = [
                {
                    "name": "SID",
                    "value": "ce_sid",
                    "domain": ".google.com",
                    "path": "/",
                },
                {
                    "name": "SAPISID",
                    "value": "ce_sapisid",
                    "domain": ".google.com",
                    "path": "/",
                },
                {
                    "name": "HSID",
                    "value": "ce_hsid",
                    "domain": ".google.com",
                    "path": "/",
                },
            ]
            resp = await client.post(
                "/api/accounts/import-bundle",
                json={"content": json.dumps(cookie_editor_data)},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["imported_count"] == 1

            # 4. 多账号对象数组
            multi_acc_data = [
                {
                    "name": "Multi Acc 1",
                    "email": "multi1@gmail.com",
                    "cookies": "SID=m1; SAPISID=msap1",
                },
                {
                    "name": "Multi Acc 2",
                    "email": "multi2@gmail.com",
                    "cookies": "SID=m2; SAPISID=msap2",
                },
            ]
            resp = await client.post(
                "/api/accounts/import-bundle",
                json={"content": json.dumps(multi_acc_data)},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["imported_count"] == 2

            # 5. file_path 本地文件导入验证
            local_file = tmp_path / "imported_auth.json"
            local_file.write_text(json.dumps(auth49_data), encoding="utf-8")
            resp = await client.post(
                "/api/accounts/import-bundle",
                json={"file_path": str(local_file)},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["imported_count"] == 1
    finally:
        runtime_state.account_service = orig_service
        runtime_state.ui_debug = orig_debug
        runtime_state.ui_sandbox = orig_sandbox


@pytest.mark.asyncio
async def test_cookie_rotation_and_sibling_sync_and_deletion_safety(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """测试 Google /RotateCookies 轮换、同源兄弟同步以及关键安全约束：删除任意子账号不影响剩余兄弟账号保活。"""
    store = AccountStore(tmp_path)
    service = AccountService(store)
    orig_service = runtime_state.account_service
    orig_debug = runtime_state.ui_debug
    orig_sandbox = runtime_state.ui_sandbox
    runtime_state.account_service = service

    try:
        # 1. 模拟 Google /RotateCookies 响应
        def mock_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/RotateCookies":
                return httpx.Response(
                    200,
                    headers=[
                        (
                            "set-cookie",
                            "__Secure-1PSIDTS=ts_rotated_999; Domain=.google.com; Path=/; Secure; HttpOnly",
                        ),
                        (
                            "set-cookie",
                            "SIDCC=sidcc_rotated_888; Domain=.google.com; Path=/; Secure; HttpOnly",
                        ),
                    ],
                    json={},
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as mock_client:
            # 2. 验证 rotate_google_cookies 单元逻辑
            cookie_jar = {
                "SID": "initial_sid",
                "SAPISID": "shared_sapisid_xyz",
                "LSID": "lsid_seed_val",
                "__Secure-1PSIDTS": "ts_initial_111",
            }
            rot_res = await rotate_google_cookies(cookie_jar, client=mock_client)
            assert rot_res["ok"] is True
            updated_names = rot_res.get("updated")
            assert (
                isinstance(updated_names, list) and "__Secure-1PSIDTS" in updated_names
            )
            assert cookie_jar["__Secure-1PSIDTS"] == "ts_rotated_999"
            assert cookie_jar["SIDCC"] == "sidcc_rotated_888"

            # 3. 创建两个同源兄弟账号 (u/0 和 u/1)
            # u/0: 拥有完整凭据 (含 LSID, SAPISID, __Secure-1PSIDTS)
            storage_u0: dict[str, object] = {
                "cookies": [
                    {
                        "name": "SID",
                        "value": "sid_u0",
                        "domain": ".google.com",
                        "path": "/",
                    },
                    {
                        "name": "SAPISID",
                        "value": "shared_sapisid_xyz",
                        "domain": ".google.com",
                        "path": "/",
                    },
                    {
                        "name": "LSID",
                        "value": "lsid_u0",
                        "domain": "accounts.google.com",
                        "path": "/",
                    },
                    {
                        "name": "__Secure-1PSIDTS",
                        "value": "ts_u0_old",
                        "domain": ".google.com",
                        "path": "/",
                    },
                ]
            }
            acc_u0 = store.save_account(
                name="Google User 0",
                email="user0@gmail.com",
                storage_state=storage_u0,
                account_id="acc_u0",
                auth_user="0",
                cookie_id="cid_session_shared",
            )

            # u/1: 同一浏览器会话多登账号，初始持有旧凭据
            storage_u1: dict[str, object] = {
                "cookies": [
                    {
                        "name": "SID",
                        "value": "sid_u1",
                        "domain": ".google.com",
                        "path": "/",
                    },
                    {
                        "name": "SAPISID",
                        "value": "shared_sapisid_xyz",
                        "domain": ".google.com",
                        "path": "/",
                    },
                    {
                        "name": "LSID",
                        "value": "lsid_u1",
                        "domain": "accounts.google.com",
                        "path": "/",
                    },
                    {
                        "name": "__Secure-1PSIDTS",
                        "value": "ts_u1_old",
                        "domain": ".google.com",
                        "path": "/",
                    },
                ]
            }
            acc_u1 = store.save_account(
                name="Google User 1",
                email="user1@gmail.com",
                storage_state=storage_u1,
                account_id="acc_u1",
                auth_user="1",
                cookie_id="cid_session_shared",
            )

            # 4. 对 u/0 执行 rotate_account_cookies
            rotate_res = await service.rotate_account_cookies(
                "acc_u0", client=mock_client
            )
            assert rotate_res["ok"] is True

            # 验证 Sibling Sync：u/0 与同源兄弟 u/1 的 auth.json 均已同步为最新凭据
            u0_cookies = load_cookies_from_auth_file(store.get_auth_path(acc_u0.id))
            u1_cookies = load_cookies_from_auth_file(store.get_auth_path(acc_u1.id))
            assert u0_cookies["__Secure-1PSIDTS"] == "ts_rotated_999"
            assert u1_cookies["__Secure-1PSIDTS"] == "ts_rotated_999"

            # 验证 last_rotated_at 均已记录
            meta_u0 = store.get_account(acc_u0.id)
            meta_u1 = store.get_account(acc_u1.id)
            assert meta_u0 is not None and meta_u0.last_rotated_at is not None
            assert meta_u1 is not None and meta_u1.last_rotated_at is not None

            # 5. 关键安全约束：用户手动删除 u/0
            assert service.delete_account(acc_u0.id) is True
            assert store.get_account(acc_u0.id) is None

            # 验证剩余兄弟账号 u/1 仍完好无损，且持有完整凭据与域名
            u1_path = store.get_auth_path(acc_u1.id)
            assert u1_path is not None and u1_path.is_file()
            u1_state = parse_storage_state(u1_path.read_text(encoding="utf-8"))
            assert u1_state is not None
            u1_cookies_list = u1_state.get("cookies", [])
            assert any(
                c.get("name") == "LSID" and c.get("domain") == "accounts.google.com"
                for c in u1_cookies_list  # type: ignore[union-attr]
            )

            # 模拟第二次轮换 (更新的 ts 值)
            def mock_handler_v2(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    headers=[
                        (
                            "set-cookie",
                            "__Secure-1PSIDTS=ts_rotated_v2; Domain=.google.com; Path=/; Secure; HttpOnly",
                        ),
                    ],
                    json={},
                )

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(mock_handler_v2)
            ) as mock_client_v2:
                # 剩余账号 u/1 能独立成功继续执行轮换保活！
                rot_res_v2 = await service.rotate_account_cookies(
                    acc_u1.id, client=mock_client_v2
                )
                assert rot_res_v2["ok"] is True
                u1_latest = load_cookies_from_auth_file(store.get_auth_path(acc_u1.id))
                assert u1_latest["__Secure-1PSIDTS"] == "ts_rotated_v2"

            # 6. 测试 POST /accounts/{account_id}/rotate-cookies API 路由
            # 6.1 沙盒模式下返回模拟成功响应
            runtime_state.ui_debug = True
            from aistudio_api.api.ui_sandbox import UISandbox

            runtime_state.ui_sandbox = UISandbox()
            async with _client() as api_client:
                sandbox_resp = await api_client.post(
                    f"/accounts/{acc_u1.id}/rotate-cookies"
                )
                assert sandbox_resp.status_code == 200
                s_data = sandbox_resp.json()
                assert s_data["ok"] is True
                assert "__Secure-1PSIDTS" in s_data["updated"]
                assert s_data["account"]["id"] == acc_u1.id

            runtime_state.ui_debug = False
            runtime_state.ui_sandbox = None

            # 6.2 非沙盒模式下调用真实 service 路由 (通过 monkeypatch 模拟网络响应)
            async def _fake_rotate(
                cookies: dict[str, str], **kwargs: Any
            ) -> dict[str, object]:
                cookies["__Secure-1PSIDTS"] = "ts_api_rotated"
                return {"ok": True, "status": 200, "updated": ["__Secure-1PSIDTS"]}

            monkeypatch.setattr(
                "aistudio_api.infrastructure.account.cookie_parser.rotate_google_cookies",
                _fake_rotate,
            )
            async with _client() as api_client:
                api_resp = await api_client.post(
                    f"/accounts/{acc_u1.id}/rotate-cookies"
                )
                assert api_resp.status_code == 200
                api_data = api_resp.json()
                assert api_data["ok"] is True
                assert "account" in api_data
                assert api_data["account"]["id"] == acc_u1.id
                u1_latest = load_cookies_from_auth_file(store.get_auth_path(acc_u1.id))
                assert u1_latest["__Secure-1PSIDTS"] == "ts_api_rotated"
    finally:
        runtime_state.account_service = orig_service
        runtime_state.ui_debug = orig_debug
        runtime_state.ui_sandbox = orig_sandbox
