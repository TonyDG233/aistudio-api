"""Offline coverage for browserless model discovery candidate fallback."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from aistudio_api.config import settings
from aistudio_api.infrastructure.account import account_store as account_store_module
from aistudio_api.infrastructure.gateway.model_discovery import (
    _FALLBACK_MODELS,
    ModelDiscoveryService,
)


class _FakeAccountStore:
    def __init__(
        self,
        accounts: list[SimpleNamespace] | None = None,
        auth_paths: dict[str, Path] | None = None,
    ) -> None:
        self._accounts = accounts or []
        self._auth_paths = auth_paths or {}

    def list_accounts(self) -> list[SimpleNamespace]:
        return self._accounts

    def get_auth_path_optional(
        self, account_id: str, *, require_exists: bool = False
    ) -> Path | None:
        del require_exists
        return self._auth_paths.get(account_id)


def _write_auth(path: Path, *, sapisid: str, sid: str) -> Path:
    path.write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "SAPISID", "value": sapisid},
                    {"name": "SID", "value": sid},
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def _browserless_session(auth_file: Path, *, tier: str = "Free") -> SimpleNamespace:
    return SimpleNamespace(
        engine="browserless",
        page=None,
        _auth_file=str(auth_file),
        _auth_user="0",
        _tier=tier,
        _cookies=[
            {"name": "SAPISID", "value": f"sapisid-{auth_file.stem}"},
            {"name": "SID", "value": f"sid-{auth_file.stem}"},
        ],
        get_current_auth_user=lambda: "0",
        get_current_tier=lambda: tier,
        is_alive=lambda: True,
    )


def _account(
    account_id: str, *, tier: str = "Free", enabled: bool = True, expired: bool = False
) -> SimpleNamespace:
    return SimpleNamespace(
        id=account_id,
        tier=tier,
        enabled=enabled,
        expired=expired,
        auth_user="0",
    )


def _models(count: int = 6) -> list[dict[str, object]]:
    return [
        {
            "id": f"gemini-real-{index}",
            "displayName": f"Gemini Real {index}",
            "supportedGenerationMethods": ["generateContent"],
        }
        for index in range(count)
    ]


def _ids(models: list[dict[str, object]]) -> list[str]:
    return [str(model.get("id")) for model in models]


def _point_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cache_path = tmp_path / "models_cache.json"
    monkeypatch.setattr(settings, "model_cache_file", str(cache_path))
    return cache_path


def _install_store(monkeypatch: pytest.MonkeyPatch, store: _FakeAccountStore) -> None:
    monkeypatch.setattr(
        account_store_module, "AccountStore", lambda *args, **kwargs: store
    )


def _write_snapshot(path: Path, models: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "fetched_at": time.time(),
                "models": models,
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_browserless_skips_page_xhr_and_never_logs_no_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _point_cache(monkeypatch, tmp_path)
    _install_store(monkeypatch, _FakeAccountStore())
    auth_file = _write_auth(tmp_path / "auth-a.json", sapisid="a", sid="a")
    session = _browserless_session(auth_file)
    service = ModelDiscoveryService()
    expected = _models()

    async def _page_path(*args: object, **kwargs: object) -> list[dict[str, object]]:
        raise AssertionError("page/XHR path must not run for browserless sessions")

    async def _http_path(
        cookies: dict[str, str], auth_user: str, *, tier: str = "Free"
    ) -> list[dict[str, object]]:
        assert cookies["SAPISID"] == "sapisid-auth-a"
        assert auth_user == "0"
        assert tier == "Free"
        return expected

    monkeypatch.setattr(service, "_fetch_via_page", _page_path)
    monkeypatch.setattr(service, "_fetch_via_http", _http_path)

    with caplog.at_level(logging.WARNING):
        models = await service.get_models(sessions=(session,), force_refresh=True)

    assert _ids(models) == _ids(expected)
    assert service.last_source == "live_http"
    assert "no page" not in caplog.text
    assert "browserless session has no page" not in caplog.text


@pytest.mark.asyncio
async def test_http_candidates_skip_401_and_use_next_healthy_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _point_cache(monkeypatch, tmp_path)
    bad_auth = _write_auth(tmp_path / "auth-bad.json", sapisid="bad", sid="bad")
    good_auth = _write_auth(tmp_path / "auth-good.json", sapisid="good", sid="good")
    session = _browserless_session(bad_auth)
    account = _account("acc_good", tier="Pro")
    _install_store(
        monkeypatch,
        _FakeAccountStore(
            accounts=[account],
            auth_paths={account.id: good_auth},
        ),
    )
    service = ModelDiscoveryService()
    expected = _models()
    calls: list[str] = []

    async def _http_path(
        cookies: dict[str, str], auth_user: str, *, tier: str = "Free"
    ) -> list[dict[str, object]]:
        calls.append(cookies["SAPISID"])
        if cookies["SAPISID"] == "sapisid-auth-bad":
            raise RuntimeError("ListModels HTTP status 401")
        assert cookies["SAPISID"] == "good"
        assert auth_user == "0"
        assert tier == "Pro"
        return expected

    monkeypatch.setattr(service, "_fetch_via_http", _http_path)

    models = await service.get_models(sessions=(session,), force_refresh=True)

    assert calls == ["sapisid-auth-bad", "good"]
    assert _ids(models) == _ids(expected)
    assert service.last_source == "live_http"
    assert service.last_error is None


@pytest.mark.asyncio
async def test_live_http_success_atomically_writes_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = _point_cache(monkeypatch, tmp_path)
    _install_store(monkeypatch, _FakeAccountStore())
    auth_file = _write_auth(tmp_path / "auth-write.json", sapisid="write", sid="write")
    session = _browserless_session(auth_file)
    service = ModelDiscoveryService()
    expected = _models(7)

    async def _http_path(
        cookies: dict[str, str], auth_user: str, *, tier: str = "Free"
    ) -> list[dict[str, object]]:
        return expected

    monkeypatch.setattr(service, "_fetch_via_http", _http_path)

    await service.get_models(sessions=(session,), force_refresh=True)

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert isinstance(payload["fetched_at"], (int, float))
    assert _ids(payload["models"]) == _ids(expected)
    leftovers = [path.name for path in tmp_path.iterdir() if ".tmp." in path.name]
    assert leftovers == []


@pytest.mark.parametrize(
    "snapshot_models",
    [
        [
            {
                "id": "dynamic-gemini-test",
                "displayName": "Dynamic Gemini Test",
                "supportedGenerationMethods": ["generateContent"],
            }
        ],
        _models(4),
    ],
)
def test_dirty_snapshot_falls_back_to_full_builtin_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_models: list[dict[str, object]],
) -> None:
    cache_path = _point_cache(monkeypatch, tmp_path)
    _write_snapshot(cache_path, snapshot_models)

    service = ModelDiscoveryService()

    cached_ids = _ids(service._cached_models)
    assert len(cached_ids) >= 30
    assert "dynamic-gemini-test" not in cached_ids
    assert cached_ids == _ids(_FALLBACK_MODELS)
    assert service.last_source == "fallback"
    assert {entry.get("_discovery_source") for entry in service._cached_models} == {
        "fallback"
    }


def test_account_http_candidates_caps_to_two_paid_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aistudio_api.infrastructure.gateway.model_discovery import (
        _account_http_candidates,
    )

    _point_cache(monkeypatch, tmp_path)
    accounts: list[SimpleNamespace] = []
    auth_paths: dict[str, Path] = {}
    for index, tier in enumerate(("Free", "Pro", "Ultra", "Plus", "Free")):
        account = _account(f"acc_{index}", tier=tier)
        accounts.append(account)
        auth_paths[account.id] = _write_auth(
            tmp_path / f"auth-{index}.json",
            sapisid=f"sapisid-{index}",
            sid=f"sid-{index}",
        )
    _install_store(
        monkeypatch,
        _FakeAccountStore(accounts=accounts, auth_paths=auth_paths),
    )

    candidates = _account_http_candidates()

    assert len(candidates) == 2
    assert [candidate.tier for candidate in candidates] == ["Ultra", "Pro"]


def test_account_http_candidates_skips_auth_locked_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aistudio_api.application import account_rotator as rotator_module
    from aistudio_api.infrastructure.gateway.model_discovery import (
        _account_http_candidates,
    )

    _point_cache(monkeypatch, tmp_path)
    accounts: list[SimpleNamespace] = []
    auth_paths: dict[str, Path] = {}
    for index, tier in enumerate(("Ultra", "Pro", "Plus")):
        account = _account(f"acc_{index}", tier=tier)
        accounts.append(account)
        auth_paths[account.id] = _write_auth(
            tmp_path / f"auth-{index}.json",
            sapisid=f"sapisid-{index}",
            sid=f"sid-{index}",
        )
    _install_store(
        monkeypatch,
        _FakeAccountStore(accounts=accounts, auth_paths=auth_paths),
    )

    class _LockedRotator:
        def is_auth_locked(self, account_id: str) -> bool:
            return account_id == "acc_0"

    monkeypatch.setattr(rotator_module, "_rotator", _LockedRotator(), raising=False)

    candidates = _account_http_candidates()

    assert [candidate.tier for candidate in candidates] == ["Pro", "Plus"]


@pytest.mark.asyncio
async def test_fetch_via_http_sends_browser_like_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    captured: dict[str, str] = {}

    class _Response:
        status_code = 200

        def json(self) -> list[object]:
            return [
                [
                    [
                        "models/gemini-x",
                        None,
                        "001",
                        "Gemini X",
                        "",
                        1,
                        1,
                        ["generateContent"],
                    ]
                ]
            ]

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> bool:
            del args
            return False

        async def post(
            self, url: str, *, headers: dict[str, str], content: str
        ) -> _Response:
            del url, content
            captured.update(headers)
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    service = ModelDiscoveryService()

    models = await service._fetch_via_http({"SAPISID": "sap"}, "0")

    assert [model["id"] for model in models] == ["gemini-x"]
    assert captured["user-agent"].endswith("Firefox/152.0")
    assert captured["accept-language"].startswith("en-US")
    assert captured["sec-fetch-dest"] == "empty"
    assert captured["sec-fetch-mode"] == "cors"
    assert captured["sec-fetch-site"] == "cross-site"


@pytest.mark.asyncio
async def test_all_candidates_fail_keeps_previous_valid_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = _point_cache(monkeypatch, tmp_path)
    previous = _models(8)
    _write_snapshot(cache_path, previous)
    auth_bad = _write_auth(tmp_path / "auth-x.json", sapisid="x", sid="x")
    auth_other = _write_auth(tmp_path / "auth-y.json", sapisid="y", sid="y")
    session = _browserless_session(auth_bad)
    account = _account("acc_other", tier="Ultra")
    _install_store(
        monkeypatch,
        _FakeAccountStore(
            accounts=[account],
            auth_paths={account.id: auth_other},
        ),
    )
    service = ModelDiscoveryService()
    assert service.last_source == "snapshot"
    cache_before = cache_path.read_text(encoding="utf-8")
    attempts: list[str] = []

    async def _http_path(
        cookies: dict[str, str], auth_user: str, *, tier: str = "Free"
    ) -> list[dict[str, object]]:
        attempts.append(cookies["SAPISID"])
        raise RuntimeError("ListModels HTTP status 401")

    monkeypatch.setattr(service, "_fetch_via_http", _http_path)

    models = await service.get_models(sessions=(session,), force_refresh=True)

    assert len(attempts) == 2
    assert _ids(models) == _ids(previous)
    assert service.last_source == "snapshot"
    assert "401" in (service.last_error or "")
    assert cache_path.read_text(encoding="utf-8") == cache_before
