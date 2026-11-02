"""Model discovery warmup cadence and log consolidation (VPS bug 3)."""

from __future__ import annotations

import logging

import pytest

from aistudio_api.api import app as app_module
from aistudio_api.api.state import RuntimeState
from aistudio_api.infrastructure.gateway import (
    model_defaults as model_defaults_module,
    model_discovery as discovery_module,
)
from aistudio_api.infrastructure.gateway.model_defaults import (
    get_configured_model_discovery_settings,
)
from aistudio_api.infrastructure.gateway.model_discovery import ModelDiscoveryService


def test_live_page_source_uses_daily_interval() -> None:
    assert (
        app_module._model_warmup_interval_seconds(
            "live_page",
            {"live_refresh_interval_sec": 86400, "fallback_refresh_interval_sec": 600},
        )
        == 86400
    )


@pytest.mark.parametrize("source", ["fallback", "snapshot", "live_http"])
def test_non_live_sources_use_short_retry_interval(source: str) -> None:
    assert (
        app_module._model_warmup_interval_seconds(
            source,
            {"live_refresh_interval_sec": 86400, "fallback_refresh_interval_sec": 600},
        )
        == 600
    )


def test_model_discovery_settings_read_config_override(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "features:\n"
        "  model_discovery:\n"
        "    live_refresh_interval_sec: 1234\n"
        "    fallback_refresh_interval_sec: 42\n",
        encoding="utf-8",
    )

    settings = get_configured_model_discovery_settings(config_path)

    assert settings == {
        "live_refresh_interval_sec": 1234,
        "fallback_refresh_interval_sec": 42,
    }


def test_model_discovery_settings_reject_invalid_values(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "features:\n"
        "  model_discovery:\n"
        "    live_refresh_interval_sec: 0\n"
        "    fallback_refresh_interval_sec: '600'\n",
        encoding="utf-8",
    )

    settings = get_configured_model_discovery_settings(config_path)

    assert settings == {
        "live_refresh_interval_sec": 86400,
        "fallback_refresh_interval_sec": 600,
    }


@pytest.mark.asyncio
async def test_warmup_cycle_sleeps_24h_after_live_success(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def fake_refresh(_state: RuntimeState) -> int:
        discovery_module.model_discovery.last_source = "live_page"
        return 1

    monkeypatch.setattr(app_module, "_refresh_model_catalog_once", fake_refresh)

    interval = await app_module._model_warmup_cycle(RuntimeState(), sleep=fake_sleep)

    assert interval == 86400
    assert sleeps == [86400]


@pytest.mark.asyncio
async def test_warmup_cycle_sleeps_600_after_fallback(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def fake_refresh(_state: RuntimeState) -> int:
        discovery_module.model_discovery.last_source = "fallback"
        return 1

    monkeypatch.setattr(app_module, "_refresh_model_catalog_once", fake_refresh)

    interval = await app_module._model_warmup_cycle(RuntimeState(), sleep=fake_sleep)

    assert interval == 600
    assert sleeps == [600]


@pytest.mark.asyncio
async def test_warmup_cycle_retries_sooner_when_live_source_has_error(
    monkeypatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def fake_refresh(_state: RuntimeState) -> int:
        discovery_module.model_discovery.last_source = "live_page"
        discovery_module.model_discovery.last_error = "live_page: boom"
        return 1

    monkeypatch.setattr(app_module, "_refresh_model_catalog_once", fake_refresh)

    await app_module._model_warmup_cycle(RuntimeState(), sleep=fake_sleep)

    assert sleeps == [600]


@pytest.mark.asyncio
async def test_warmup_cycle_retries_sooner_when_refresh_raises(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def fake_refresh(_state: RuntimeState) -> int:
        discovery_module.model_discovery.last_source = "live_page"
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "_refresh_model_catalog_once", fake_refresh)

    await app_module._model_warmup_cycle(RuntimeState(), sleep=fake_sleep)

    assert sleeps == [600]


@pytest.mark.asyncio
async def test_warmup_cycles_never_use_ten_minute_live_loop(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def fake_refresh(_state: RuntimeState) -> int:
        discovery_module.model_discovery.last_source = "live_page"
        return 1

    monkeypatch.setattr(app_module, "_refresh_model_catalog_once", fake_refresh)
    monkeypatch.setattr(
        model_defaults_module,
        "get_configured_model_discovery_settings",
        lambda: {
            "live_refresh_interval_sec": 1234,
            "fallback_refresh_interval_sec": 77,
        },
    )

    state = RuntimeState()
    await app_module._model_warmup_cycle(state, sleep=fake_sleep)
    await app_module._model_warmup_cycle(state, sleep=fake_sleep)

    assert sleeps == [1234, 1234]
    assert 600 not in sleeps


def test_identical_consecutive_refresh_logs_only_one_info(caplog, monkeypatch) -> None:
    service = ModelDiscoveryService()
    models = service._parse_raw_models(
        [[["models/gemini-9-image-demo", None, "001", "Gemini 9 Image"]]]
    )
    assert models
    service._cached_models = [dict(item) for item in models]
    monkeypatch.setattr(service, "_save_snapshot", lambda _models: None)

    with caplog.at_level(logging.INFO, logger="aistudio.model_discovery"):
        service._accept_live(models, source="live_page")
        service._accept_live(models, source="live_page")

    summaries = [
        record.getMessage()
        for record in caplog.records
        if record.name == "aistudio.model_discovery"
        and record.levelno == logging.INFO
        and "模型发现更新" in record.getMessage()
    ]

    assert summaries == ["模型发现更新: source=live_page count=1 changed=false"]
    assert service.last_source == "live_page"
