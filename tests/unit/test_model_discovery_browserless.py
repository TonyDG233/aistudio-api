"""Offline coverage for browserless model discovery candidate fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.config import settings
from aistudio_api.infrastructure.gateway import model_discovery as discovery_module
from aistudio_api.infrastructure.gateway.model_discovery import (
    _FALLBACK_MODELS,
    REGION_RESTRICTED,
    SERVICE_RESTRICTED,
    UNKNOWN_403,
    VERIFICATION_REQUIRED,
    ModelDiscoveryService,
    _classify_list_models_error,
)


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


def _browserless_session(
    auth_file: Path, *, tier: str = "Free", sender: object | None = None
) -> SimpleNamespace:
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
        send_hooked_request=sender,
    )


def _signer_session(
    auth_file: Path, sender: object, *, tier: str = "Free"
) -> SimpleNamespace:
    return _browserless_session(auth_file, tier=tier, sender=sender)


def _raw_models_payload(models: list[dict[str, object]]) -> bytes:
    items: list[list[object]] = []
    for model in models:
        item: list[object] = [None] * 84
        item[0] = f"models/{model['id']}"
        item[3] = str(model.get("displayName") or model["id"])
        item[4] = ""
        item[5] = 1
        item[6] = 1
        item[7] = ["generateContent"]
        items.append(item)
    return json.dumps([items]).encode("utf-8")


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
async def test_browserless_uses_signer_transport_and_never_touches_a_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _point_cache(monkeypatch, tmp_path)
    auth_file = _write_auth(tmp_path / "auth-a.json", sapisid="a", sid="a")
    expected = _models()
    seen: dict[str, object] = {}

    async def _sender(*, body: str, url: str) -> tuple[int, bytes]:
        seen["body"] = body
        seen["url"] = url
        return 200, _raw_models_payload(expected)

    session = _signer_session(auth_file, _sender)
    service = ModelDiscoveryService()

    async def _page_path(*args: object, **kwargs: object) -> list[dict[str, object]]:
        raise AssertionError("page/XHR path must not run for browserless sessions")

    monkeypatch.setattr(service, "_fetch_via_page", _page_path)
    assert not hasattr(ModelDiscoveryService, "_fetch_via_http")

    with caplog.at_level(logging.WARNING):
        models = await service.get_models(sessions=(session,), force_refresh=True)

    assert _ids(models) == _ids(expected)
    assert service.last_source == "live_signer"
    assert seen["body"] == "[]"
    assert str(seen["url"]).endswith("ListModels")
    assert "no page" not in caplog.text
    assert "browserless session has no page" not in caplog.text


@pytest.mark.asyncio
async def test_signer_401_without_other_channel_degrades_to_builtin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _point_cache(monkeypatch, tmp_path)
    bad_auth = _write_auth(tmp_path / "auth-bad.json", sapisid="bad", sid="bad")
    signer_calls: list[int] = []

    async def _sender(*, body: str, url: str) -> tuple[int, bytes]:
        del body, url
        signer_calls.append(1)
        return 401, b"unauthorized"

    session = _signer_session(bad_auth, _sender)
    service = ModelDiscoveryService()

    models = await service.get_models(sessions=(session,), force_refresh=True)

    assert signer_calls == [1]
    assert _ids(models) == _ids(_FALLBACK_MODELS)
    assert service.last_source == "fallback"
    assert "401" in (service.last_error or "")
    assert not hasattr(ModelDiscoveryService, "_fetch_via_http")


@pytest.mark.asyncio
async def test_live_signer_success_atomically_writes_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = _point_cache(monkeypatch, tmp_path)
    auth_file = _write_auth(tmp_path / "auth-write.json", sapisid="write", sid="write")
    service = ModelDiscoveryService()
    expected = _models(7)

    async def _sender(*, body: str, url: str) -> tuple[int, bytes]:
        del body, url
        return 200, _raw_models_payload(expected)

    session = _signer_session(auth_file, _sender)

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


def test_disk_http_fallback_surface_is_removed() -> None:
    import inspect

    source = inspect.getsource(discovery_module)
    assert not hasattr(ModelDiscoveryService, "_fetch_via_http")
    assert not hasattr(discovery_module, "_account_http_candidates")
    assert not hasattr(discovery_module, "_HttpCandidate")
    assert "live_http" not in discovery_module.LIVE_SOURCES
    assert "live_http" not in discovery_module.DISCOVERY_TRUTH_SOURCES
    assert "import httpx" not in source
    assert "httpx.AsyncClient" not in source


@pytest.mark.asyncio
async def test_all_candidates_fail_keeps_previous_valid_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = _point_cache(monkeypatch, tmp_path)
    previous = _models(8)
    _write_snapshot(cache_path, previous)
    auth_bad = _write_auth(tmp_path / "auth-x.json", sapisid="x", sid="x")
    signer_calls: list[int] = []

    async def _sender(*, body: str, url: str) -> tuple[int, bytes]:
        del body, url
        signer_calls.append(1)
        return 401, b"unauthorized"

    session = _signer_session(auth_bad, _sender)
    service = ModelDiscoveryService()
    assert service.last_source == "snapshot"
    cache_before = cache_path.read_text(encoding="utf-8")

    models = await service.get_models(sessions=(session,), force_refresh=True)

    assert signer_calls == [1]
    assert _ids(models) == _ids(previous)
    assert service.last_source == "snapshot"
    assert "401" in (service.last_error or "")
    assert service._failure_streak == 1
    assert service._next_retry_at is not None
    assert cache_path.read_text(encoding="utf-8") == cache_before


def test_failure_backoff_grows_exponentially_and_caps_at_one_day() -> None:
    service = ModelDiscoveryService()
    service._record_failure(now=100.0)
    assert service._failure_streak == 1
    assert service._next_retry_at == pytest.approx(100.0 + service.cache_ttl)

    service._record_failure(now=100.0)
    assert service._failure_streak == 2
    assert service._next_retry_at == pytest.approx(100.0 + service.cache_ttl * 2)

    for _ in range(28):
        service._record_failure(now=100.0)
    assert service._failure_streak == 30
    assert service._next_retry_at == pytest.approx(100.0 + 86400.0)


@pytest.mark.asyncio
async def test_backoff_gate_returns_cache_without_any_network_even_when_forced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cache_path = _point_cache(monkeypatch, tmp_path)
    cached = _models(6)
    _write_snapshot(cache_path, cached)

    assert not hasattr(discovery_module, "_account_http_candidates")
    auth_file = _write_auth(tmp_path / "auth-gate.json", sapisid="g", sid="g")

    async def _sender(*, body: str, url: str) -> tuple[int, bytes]:
        raise AssertionError("backoff gate must not call the signer")

    session = _signer_session(auth_file, _sender)
    service = ModelDiscoveryService()
    assert _ids(service._cached_models) == _ids(cached)
    service._failure_streak = 3
    service._next_retry_at = time.time() + 3600.0

    with caplog.at_level(logging.DEBUG):
        models = await service.get_models(sessions=(session,), force_refresh=True)

    assert _ids(models) == _ids(cached)
    assert "退避期" in caplog.text


@pytest.mark.asyncio
async def test_first_classified_403_stops_round_before_next_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _point_cache(monkeypatch, tmp_path)
    first_auth = _write_auth(tmp_path / "auth-1.json", sapisid="1", sid="1")
    second_auth = _write_auth(tmp_path / "auth-2.json", sapisid="2", sid="2")
    calls: list[str] = []

    async def _first_sender(*, body: str, url: str) -> tuple[int, bytes]:
        del body, url
        calls.append("first")
        return 403, b'[7,"Region not supported."]'

    async def _second_sender(*, body: str, url: str) -> tuple[int, bytes]:
        del body, url
        calls.append("second")
        return 200, _raw_models_payload(_models())

    service = ModelDiscoveryService()
    sessions = (
        _signer_session(first_auth, _first_sender),
        _signer_session(second_auth, _second_sender),
    )

    models = await service.get_models(sessions=sessions, force_refresh=True)

    assert calls == ["first"]
    assert service.last_source == "fallback"
    assert REGION_RESTRICTED in (service.last_error or "")
    assert service._failure_streak == 1
    assert len(models) >= 30


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('[7,"Region not supported."]', REGION_RESTRICTED),
        ('{"error":"see available-regions"}', REGION_RESTRICTED),
        ("https://accounts.google.com/signin/verify-account", VERIFICATION_REQUIRED),
        ("account must be verified", VERIFICATION_REQUIRED),
        ("servicerestricted", SERVICE_RESTRICTED),
        ("https://myaccount.google.com/restrictions", SERVICE_RESTRICTED),
        ("unexpected failure", UNKNOWN_403),
    ],
)
def test_classify_list_models_403_body(body: str, expected: str) -> None:
    assert _classify_list_models_error(body) == expected


@pytest.mark.asyncio
async def test_manual_refresh_endpoint_resets_backoff_and_forces_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aistudio_api.api import routes_system
    from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

    seen: dict[str, object] = {}

    async def _fake_get_models(**kwargs: object) -> list[dict[str, object]]:
        seen.update(kwargs)
        return [{"id": "model-a"}, {"id": "model-b"}]

    monkeypatch.setattr(model_discovery, "get_models", _fake_get_models)
    monkeypatch.setattr(model_discovery, "last_source", "fallback")
    monkeypatch.setattr(model_discovery, "last_error", None)

    result = await routes_system.refresh_models(
        runtime_state=cast(Any, SimpleNamespace(instance_manager=None))
    )

    assert seen["force_refresh"] is True
    assert seen["reset_backoff"] is True
    assert seen["sessions"] == ()
    assert result == {"source": "fallback", "model_count": 2}


@pytest.mark.asyncio
async def test_success_clears_backoff_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _point_cache(monkeypatch, tmp_path)
    auth_file = _write_auth(tmp_path / "auth-ok.json", sapisid="ok", sid="ok")
    expected = _models()

    async def _sender(*, body: str, url: str) -> tuple[int, bytes]:
        del body, url
        return 200, _raw_models_payload(expected)

    service = ModelDiscoveryService()
    service._failure_streak = 5
    service._next_retry_at = 0.0

    models = await service.get_models(
        sessions=(_signer_session(auth_file, _sender),), force_refresh=True
    )

    assert _ids(models) == _ids(expected)
    assert service._failure_streak == 0
    assert service._next_retry_at is None


@pytest.mark.asyncio
async def test_reset_backoff_bypasses_gate_and_recounts_failed_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = _point_cache(monkeypatch, tmp_path)
    cached = _models(6)
    _write_snapshot(cache_path, cached)
    service = ModelDiscoveryService()
    assert _ids(service._cached_models) == _ids(cached)
    service._failure_streak = 4
    service._next_retry_at = time.time() + 3600.0

    models = await service.get_models(force_refresh=True, reset_backoff=True)

    assert _ids(models) == _ids(cached)
    assert service.last_source == "snapshot"
    # The gate was bypassed, the round ran and failed (no candidates), so the
    # operator-forced reset lands on a fresh streak of 1 instead of staying 4.
    assert service._failure_streak == 1
    assert service._next_retry_at is not None


@pytest.mark.asyncio
async def test_no_candidate_round_records_failure_and_gates_next_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _point_cache(monkeypatch, tmp_path)
    service = ModelDiscoveryService()

    await service.get_models(force_refresh=True)

    assert service._failure_streak == 1
    assert service._next_retry_at is not None

    with caplog.at_level(logging.DEBUG):
        await service.get_models(force_refresh=True)

    assert service._failure_streak == 1
    assert "退避期" in caplog.text


@pytest.mark.asyncio
async def test_signer_timeout_falls_through_to_next_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from aistudio_api.infrastructure.gateway import model_discovery as discovery_module

    _point_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(discovery_module, "_SIGNER_FETCH_TIMEOUT_SECONDS", 0.01)
    first_auth = _write_auth(tmp_path / "auth-hang.json", sapisid="h", sid="h")
    second_auth = _write_auth(tmp_path / "auth-ok.json", sapisid="o", sid="o")
    expected = _models()

    async def _hang(*, body: str, url: str) -> tuple[int, bytes]:
        del body, url
        await asyncio.sleep(60)
        raise AssertionError("timed-out signer must be cancelled")

    async def _ok(*, body: str, url: str) -> tuple[int, bytes]:
        del body, url
        return 200, _raw_models_payload(expected)

    service = ModelDiscoveryService()
    sessions = (
        _signer_session(first_auth, _hang),
        _signer_session(second_auth, _ok),
    )

    with caplog.at_level(logging.WARNING):
        models = await service.get_models(sessions=sessions, force_refresh=True)

    assert _ids(models) == _ids(expected)
    assert service.last_source == "live_signer"
    assert service.last_error is None
    assert "signer 请求超时" in caplog.text


def test_accept_startup_models_commits_signer_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _point_cache(monkeypatch, tmp_path)
    service = ModelDiscoveryService()
    service._failure_streak = 4
    service._next_retry_at = time.time() + 3600.0
    raw = json.dumps(
        [
            [
                [
                    "models/gemini-startup-flash",
                    None,
                    "001",
                    "Gemini Startup Flash",
                    "startup capture",
                    0,
                    0,
                    ["generateContent"],
                ]
            ]
        ]
    )

    assert service.accept_startup_models(raw, source="live_signer") is True

    assert _ids(service._cached_models) == ["gemini-startup-flash"]
    assert service.last_source == "live_signer"
    assert service._failure_streak == 0
    assert service._next_retry_at is None


def test_accept_startup_models_rejects_invalid_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _point_cache(monkeypatch, tmp_path)
    service = ModelDiscoveryService()
    before = _ids(service._cached_models)

    assert service.accept_startup_models("not-json") is False
    assert service.accept_startup_models("[]") is False
    assert service.accept_startup_models("") is False
    assert _ids(service._cached_models) == before
