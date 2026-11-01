"""P3-F deterministic per-account browser fingerprint contracts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

import aistudio_api.config as config_module
import aistudio_api.infrastructure.gateway.pooled_session as pooled_module
import aistudio_api.infrastructure.gateway.session as session_module
from aistudio_api.config import resolve_fingerprint_spoof_enabled
from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage
from aistudio_api.infrastructure.browser.fingerprint import (
    FORBIDDEN_RENDERER_TOKENS,
    apply_fingerprint,
    build_fingerprint_payload,
    build_fingerprint_profile,
    build_fingerprint_script,
)
from aistudio_api.infrastructure.gateway.instance_pool import (
    InstancePool,
    InstanceSpec,
)
from aistudio_api.infrastructure.gateway.pooled_session import PooledSession
from aistudio_api.infrastructure.gateway.session import BrowserSession

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _FakeConnection:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.fail_methods: set[str] = set()

    async def send(
        self, method: str, params: dict[str, object] | None = None, **_: Any
    ) -> dict[str, object]:
        payload = dict(params or {})
        self.calls.append((method, payload))
        if method in self.fail_methods:
            raise RuntimeError(f"forced failure: {method}")
        if method == "Page.addScriptToEvaluateOnNewDocument":
            self.events.append("fingerprint.script")
        elif method == "Emulation.setDeviceMetricsOverride":
            self.events.append("fingerprint.metrics")
        return {}


class _FakePage:
    def __init__(self, events: list[str], target_id: str = "target-1") -> None:
        self.events = events
        self.cdp = _FakeConnection(events)
        self.target_id = target_id
        self.account_auth_file: str | None = None
        self._closed = False
        self.cookies: list[dict[str, object]] = []

    def is_closed(self) -> bool:
        return self._closed

    async def set_cookies(self, cookies: list[dict[str, object]]) -> None:
        self.events.append("cookies.set")
        self.cookies = list(cookies)

    async def get_cookies(self) -> list[dict[str, object]]:
        return list(self.cookies)

    async def close(self) -> None:
        self.events.append("page.close")
        self._closed = True


async def _fake_load_cookies(target: Any, auth_file: str | None) -> bool:
    del auth_file
    cast("_FakePage", target).events.append("cookies")
    return True


async def _fake_navigate(target: Any, **_: Any) -> None:
    cast("_FakePage", target).events.append("navigate")


def _patch_session_io(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    monkeypatch.setattr(module, "load_cookies_to_page", _fake_load_cookies)
    monkeypatch.setattr(module, "navigate_to_aistudio", _fake_navigate)
    monkeypatch.setattr(
        module,
        "bootstrap_botguard_and_template",
        AsyncMock(return_value=("snap_key", {"url": "https://example.com"})),
    )


async def _bootstrap_pooled(
    account_id: str,
) -> tuple[PooledSession, _FakePage]:
    events: list[str] = []
    page = _FakePage(events)
    cdp = MagicMock(spec=CDPClient)
    cdp.create_page_in_context = AsyncMock(return_value=page)
    session = await PooledSession.bootstrap(
        cdp,
        context_id="ctx-1",
        auth_file=f"/accounts/{account_id}/auth.json",
        auth_user="0",
        realm_id=f"realm-{account_id}",
        account_id=account_id,
    )
    return session, page


@pytest.mark.asyncio
async def test_pooled_session_injects_fingerprint_before_cookies_and_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pooled_module, "resolve_fingerprint_spoof_enabled", lambda: True
    )
    _patch_session_io(monkeypatch, pooled_module)

    session, page = await _bootstrap_pooled("account-A")

    try:
        assert page.events == ["fingerprint.script", "fingerprint.metrics", "cookies", "navigate"]
        methods = [method for method, _ in page.cdp.calls]
        assert methods[0] == "Page.addScriptToEvaluateOnNewDocument"
        assert methods[1] == "Emulation.setDeviceMetricsOverride"

        expected = build_fingerprint_script(
            build_fingerprint_profile(
                account_id="account-A", auth_file="/accounts/account-A/auth.json"
            )
        )
        script_params = page.cdp.calls[0][1]
        assert script_params["source"] == expected
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_same_account_is_stable_and_account_swap_changes_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pooled_module, "resolve_fingerprint_spoof_enabled", lambda: True
    )
    _patch_session_io(monkeypatch, pooled_module)

    first, first_page = await _bootstrap_pooled("account-A")
    second, second_page = await _bootstrap_pooled("account-A")
    other, other_page = await _bootstrap_pooled("account-B")
    try:
        first_source = cast(str, first_page.cdp.calls[0][1]["source"])
        second_source = cast(str, second_page.cdp.calls[0][1]["source"])
        other_source = cast(str, other_page.cdp.calls[0][1]["source"])
        assert first_source == second_source
        assert first_source != other_source
        assert build_fingerprint_profile(account_id="account-A").fingerprint_id in first_source
        assert build_fingerprint_profile(account_id="account-B").fingerprint_id in other_source
    finally:
        await first.close()
        await second.close()
        await other.close()


@pytest.mark.asyncio
async def test_disabled_config_skips_all_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pooled_module, "resolve_fingerprint_spoof_enabled", lambda: False
    )
    _patch_session_io(monkeypatch, pooled_module)

    session, page = await _bootstrap_pooled("account-A")
    try:
        assert page.events == ["cookies", "navigate"]
        assert page.cdp.calls == []
    finally:
        await session.close()


def test_fingerprint_config_defaults_true_and_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for env_name in (
        "AISTUDIO_FINGERPRINT_SPOOF",
        "AISTUDIO_FINGERPRINT_SPOOF_ENABLED",
    ):
        monkeypatch.delenv(env_name, raising=False)

    config_path = tmp_path / "config.yaml"
    config_path.write_text("api_keys: []\n", encoding="utf-8")
    monkeypatch.setenv("AISTUDIO_CONFIG_PATH", str(config_path))
    assert resolve_fingerprint_spoof_enabled() is True

    config_path.write_text(
        "browser:\n  fingerprint_spoof_enabled: false\n", encoding="utf-8"
    )
    assert resolve_fingerprint_spoof_enabled() is False

    config_path.write_text(
        "browser:\n  fingerprint_spoof_enabled: true\n", encoding="utf-8"
    )
    assert resolve_fingerprint_spoof_enabled() is True


def test_fingerprint_config_is_cached_by_path_mtime_and_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for env_name in (
        "AISTUDIO_FINGERPRINT_SPOOF",
        "AISTUDIO_FINGERPRINT_SPOOF_ENABLED",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setattr(config_module, "_FINGERPRINT_SPOOF_CACHE", {})

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "browser:\n  fingerprint_spoof_enabled: false\n", encoding="utf-8"
    )
    monkeypatch.setenv("AISTUDIO_CONFIG_PATH", str(config_path))

    calls = {"count": 0}
    real_safe_load = yaml.safe_load

    def counting_safe_load(stream: Any, *args: Any, **kwargs: Any) -> Any:
        calls["count"] += 1
        return real_safe_load(stream, *args, **kwargs)

    monkeypatch.setattr(yaml, "safe_load", counting_safe_load)

    assert resolve_fingerprint_spoof_enabled() is False
    assert resolve_fingerprint_spoof_enabled() is False
    assert calls["count"] == 1

    stat = config_path.stat()
    os.utime(
        config_path,
        ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000),
    )
    assert resolve_fingerprint_spoof_enabled() is False
    assert calls["count"] == 2

    config_path.write_text(
        "browser:\n  fingerprint_spoof_enabled: true\n", encoding="utf-8"
    )
    assert resolve_fingerprint_spoof_enabled() is True
    assert calls["count"] == 3

    monkeypatch.setenv("AISTUDIO_FINGERPRINT_SPOOF_ENABLED", "0")
    assert resolve_fingerprint_spoof_enabled() is False
    assert calls["count"] == 3
    monkeypatch.setenv("AISTUDIO_FINGERPRINT_SPOOF_ENABLED", "1")
    assert resolve_fingerprint_spoof_enabled() is True
    assert calls["count"] == 3


def test_defaults_and_schema_declare_fingerprint_switch() -> None:
    defaults = yaml.safe_load(
        (PROJECT_ROOT / "defaults" / "config.yaml").read_text(encoding="utf-8")
    )
    assert defaults["browser"]["fingerprint_spoof_enabled"] is True

    schema = json.loads(
        (PROJECT_ROOT / "config" / "config.schema.json").read_text(encoding="utf-8")
    )
    switch = schema["properties"]["browser"]["properties"][
        "fingerprint_spoof_enabled"
    ]
    assert switch["type"] == "boolean"
    assert switch["default"] is True


def test_profiles_are_stable_per_account_and_differ_across_accounts() -> None:
    first = build_fingerprint_profile(account_id="account-A")
    first_again = build_fingerprint_profile(account_id="  ACCOUNT-a ")
    other = build_fingerprint_profile(account_id="account-B")

    assert first == first_again
    assert first.fingerprint_id == first_again.fingerprint_id
    assert first != other
    assert first.fingerprint_id != other.fingerprint_id


def test_auth_file_identity_falls_back_to_parent_directory() -> None:
    first = build_fingerprint_profile(auth_file="/srv/accounts/account-A/auth.json")
    same = build_fingerprint_profile(auth_file="D:/mirror/accounts/account-A/auth.json")
    other = build_fingerprint_profile(auth_file="/srv/accounts/account-B/auth.json")

    assert first == same
    assert first.fingerprint_id != other.fingerprint_id


def test_profiles_never_expose_software_renderers() -> None:
    for index in range(512):
        profile = build_fingerprint_profile(account_id=f"account-{index}")
        payload = json.dumps(
            build_fingerprint_payload(profile), ensure_ascii=False
        ).casefold()
        for token in FORBIDDEN_RENDERER_TOKENS:
            assert token not in payload
        assert profile.gpu_family in profile.webgl_renderer.casefold()
        assert "direct3d11" in profile.webgl_renderer.casefold()
        assert profile.hardware_concurrency in {8, 12, 16, 24}
        assert profile.device_memory in {8, 16, 32}
        assert profile.platform == "Win32"
        assert profile.user_agent.startswith("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")


@pytest.mark.asyncio
async def test_apply_fingerprint_disabled_or_failed_registration() -> None:
    events: list[str] = []
    page = _FakePage(events)
    profile = build_fingerprint_profile(account_id="account-A")

    assert await apply_fingerprint(cast(CDPPage, page), profile, enabled=False) is False
    assert page.cdp.calls == []

    page.cdp.fail_methods.add("Page.addScriptToEvaluateOnNewDocument")
    with pytest.raises(RuntimeError, match="指纹脚本注册失败"):
        await apply_fingerprint(cast(CDPPage, page), profile, enabled=True)


@pytest.mark.asyncio
async def test_metrics_failure_is_logged_but_script_stays_registered() -> None:
    events: list[str] = []
    page = _FakePage(events)
    page.cdp.fail_methods.add("Emulation.setDeviceMetricsOverride")
    profile = build_fingerprint_profile(account_id="account-A")

    assert await apply_fingerprint(cast(CDPPage, page), profile, enabled=True) is True
    assert [method for method, _ in page.cdp.calls] == [
        "Page.addScriptToEvaluateOnNewDocument",
        "Emulation.setDeviceMetricsOverride",
    ]
    assert events == ["fingerprint.script"]


@pytest.mark.asyncio
async def test_browser_session_rebind_swaps_fingerprint_for_new_account(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        session_module, "resolve_fingerprint_spoof_enabled", lambda: True
    )
    _patch_session_io(monkeypatch, session_module)

    account_a = tmp_path / "account-A"
    account_a.mkdir()
    auth_a = account_a / "auth.json"
    auth_a.write_text("{}", encoding="utf-8")
    account_b = tmp_path / "account-B"
    account_b.mkdir()
    auth_b = account_b / "auth.json"
    auth_b.write_text("{}", encoding="utf-8")

    first_page = _FakePage([], target_id="target-a")
    second_page = _FakePage([], target_id="target-b")
    mock_cdp = MagicMock(spec=CDPClient)
    mock_cdp.connect_page = AsyncMock(return_value=first_page)
    mock_cdp.create_new_page = AsyncMock(return_value=second_page)
    mock_cdp.close_target = AsyncMock()

    session = BrowserSession(port=9222)
    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = True
    session._proc = mock_proc
    session._cdp_client = mock_cdp

    await session.transition_to_account(str(auth_a), force_refresh=True)
    await session.transition_to_account(str(auth_b), force_refresh=True)

    first_source = cast(str, first_page.cdp.calls[0][1]["source"])
    second_source = cast(str, second_page.cdp.calls[0][1]["source"])
    assert first_source == build_fingerprint_script(
        build_fingerprint_profile(auth_file=str(auth_a))
    )
    assert second_source == build_fingerprint_script(
        build_fingerprint_profile(auth_file=str(auth_b))
    )
    assert first_source != second_source
    assert first_page.events[:2] == ["fingerprint.script", "fingerprint.metrics"]
    assert second_page.events[:2] == ["fingerprint.script", "fingerprint.metrics"]


def test_generated_script_binds_native_prototypes_and_merges_chrome() -> None:
    runtime = shutil.which("bun") or shutil.which("node")
    if not runtime:
        pytest.skip("Neither bun nor node found in PATH; skipping JS behavior check.")

    script_path = (
        PROJECT_ROOT
        / "src"
        / "aistudio_api"
        / "infrastructure"
        / "browser"
        / "js"
        / "fingerprint_spoof.js"
    )
    payload = build_fingerprint_payload(
        build_fingerprint_profile(account_id="account-A")
    )

    harness = r"""
const fs = require('node:fs');
const assert = require('node:assert');

const source = fs.readFileSync(process.argv[2], 'utf8');
const payload = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));

function runScenario(options) {
    class Plugin {}
    class PluginArray {}
    class MimeType {}
    class MimeTypeArray {}
    const nativeLoadTimes = function () { return { marker: 'native' }; };
    const nativeChrome = options.withChrome
        ? { runtime: { id: 'native-runtime' }, loadTimes: nativeLoadTimes, custom: 'keep' }
        : undefined;
    const window = {
        screen: {
            width: 1, height: 1, availWidth: 1, availHeight: 1,
            colorDepth: 24, pixelDepth: 24,
            orientation: { type: 'x', angle: 0 }
        },
        devicePixelRatio: 1,
        innerWidth: 1, innerHeight: 1, outerWidth: 1, outerHeight: 1,
        chrome: nativeChrome
    };
    if (options.nativeConstructors) {
        window.Plugin = Plugin;
        window.PluginArray = PluginArray;
        window.MimeType = MimeType;
        window.MimeTypeArray = MimeTypeArray;
    }
    const navigator = {
        plugins: options.nativeConstructors ? new PluginArray() : [],
        mimeTypes: options.nativeConstructors ? new MimeTypeArray() : []
    };
    const factory = new Function(
        'window', 'navigator', 'document',
        'return (' + source + ');'
    );
    factory(window, navigator, {})(payload);
    return { window, navigator, nativeChrome, nativeLoadTimes, Plugin, PluginArray, MimeType, MimeTypeArray };
}

const native = runScenario({ nativeConstructors: true, withChrome: true });
assert.strictEqual(native.window.chrome, native.nativeChrome, 'chrome object must be preserved');
assert.strictEqual(native.window.chrome.custom, 'keep');
assert.strictEqual(native.window.chrome.runtime.id, 'native-runtime');
assert.strictEqual(native.window.chrome.loadTimes, native.nativeLoadTimes);
assert.ok(native.window.chrome.app && typeof native.window.chrome.csi === 'function');
assert.ok(native.navigator.plugins instanceof native.PluginArray);
assert.ok(native.navigator.mimeTypes instanceof native.MimeTypeArray);
assert.strictEqual(native.navigator.plugins.length, payload.plugins.length);
assert.strictEqual(native.navigator.plugins.item(0), native.navigator.plugins[0]);
assert.strictEqual(native.navigator.plugins.item(-1), null);
assert.strictEqual(
    native.navigator.plugins.namedItem(payload.plugins[0].name),
    native.navigator.plugins[0]
);
const firstPlugin = native.navigator.plugins[0];
assert.ok(firstPlugin instanceof native.Plugin);
assert.strictEqual(firstPlugin.length, payload.plugins[0].mimeTypes.length);
assert.strictEqual(firstPlugin.item(0), firstPlugin[0]);
assert.strictEqual(
    firstPlugin.namedItem(payload.plugins[0].mimeTypes[0]),
    firstPlugin[0]
);
const firstMime = native.navigator.mimeTypes[0];
assert.ok(firstMime instanceof native.MimeType);
assert.strictEqual(firstMime.type, payload.plugins[0].mimeTypes[0]);
assert.strictEqual(firstMime.enabledPlugin, firstPlugin);
assert.deepStrictEqual(
    Array.from(native.navigator.plugins).map(function (p) { return p.name; }),
    payload.plugins.map(function (p) { return p.name; })
);

const plain = runScenario({ nativeConstructors: false, withChrome: false });
assert.strictEqual(plain.navigator.plugins.length, payload.plugins.length);
assert.strictEqual(plain.navigator.plugins.item(0), plain.navigator.plugins[0]);
assert.strictEqual(plain.navigator.plugins.namedItem(payload.plugins[0].name), plain.navigator.plugins[0]);
assert.strictEqual(typeof plain.navigator.plugins.refresh, 'function');
assert.strictEqual(typeof plain.navigator.mimeTypes.item, 'function');
assert.strictEqual(plain.navigator.mimeTypes.namedItem('missing'), null);
assert.ok(plain.window.chrome && typeof plain.window.chrome === 'object');
assert.ok(plain.window.chrome.runtime && typeof plain.window.chrome.runtime === 'object');
assert.strictEqual(typeof plain.window.chrome.csi, 'function');
assert.strictEqual(typeof plain.window.chrome.loadTimes, 'function');
console.log('fingerprint-spoof-js ok');
"""

    with tempfile.TemporaryDirectory(prefix="aistudio-fingerprint-harness-") as tmp_dir:
        payload_path = Path(tmp_dir) / "payload.json"
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        harness_path = Path(tmp_dir) / "harness.js"
        harness_path.write_text(harness, encoding="utf-8")
        result = subprocess.run(
            [runtime, str(harness_path), str(script_path), str(payload_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    assert result.returncode == 0, result.stderr
    assert "fingerprint-spoof-js ok" in result.stdout


def test_generated_script_parses_with_available_js_runtime() -> None:
    runtime = shutil.which("bun") or shutil.which("node")
    if not runtime:
        pytest.skip("Neither bun nor node found in PATH; skipping syntax check.")

    script = build_fingerprint_script(build_fingerprint_profile(account_id="account-A"))
    probe = (
        "try { new Function("
        + json.dumps(script)
        + "); } catch (e) { if (e instanceof SyntaxError) { process.exit(1); } }"
    )
    with tempfile.TemporaryDirectory(prefix="aistudio-fingerprint-js-") as tmp_dir:
        probe_path = Path(tmp_dir) / "probe.js"
        probe_path.write_text(probe, encoding="utf-8")
        result = subprocess.run(
            [runtime, str(probe_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_import_cookies_applies_fingerprint_before_google_navigation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[str] = []
    page = _FakePage(events, target_id="target-import")
    cdp = MagicMock(spec=CDPClient)
    cdp.create_new_page = AsyncMock(return_value=page)
    cdp.close_target = AsyncMock()

    async def _fake_bootstrap(target: object) -> None:
        cast("_FakePage", target).events.append("bootstrap.google")

    async def _fake_save(
        target: object, *, auth_file: str | None, cookies: object = None
    ) -> bool:
        del target, auth_file, cookies
        events.append("cookies.save")
        return True

    monkeypatch.setattr(
        session_module, "resolve_fingerprint_spoof_enabled", lambda: True
    )
    monkeypatch.setattr(session_module, "bootstrap_google_session", _fake_bootstrap)
    monkeypatch.setattr(session_module, "save_browser_cookies", _fake_save)
    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.cookie_refresher.load_cookies_from_string",
        lambda _raw: [{"name": "SID", "value": "sid", "domain": ".google.com"}],
    )

    auth_file = str(tmp_path / "imported-account" / "auth.json")
    session = BrowserSession(port=9222)
    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = True
    session._proc = mock_proc
    session._cdp_client = cdp

    imported = await session.import_cookies("SID=sid", auth_file=auth_file)

    assert imported == 1
    assert events[:4] == [
        "fingerprint.script",
        "fingerprint.metrics",
        "cookies.set",
        "bootstrap.google",
    ]
    assert events[4] == "cookies.save"
    assert cast(str, page.cdp.calls[0][1]["source"]) == build_fingerprint_script(
        build_fingerprint_profile(auth_file=auth_file)
    )


@pytest.mark.asyncio
async def test_instance_pool_reassign_passes_new_account_id_to_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pooled_module, "resolve_fingerprint_spoof_enabled", lambda: True
    )
    _patch_session_io(monkeypatch, pooled_module)

    pages = [
        _FakePage([], target_id="target-a"),
        _FakePage([], target_id="target-b"),
    ]
    remaining = iter(pages)
    cdp = MagicMock(spec=CDPClient)
    cdp.create_browser_context = AsyncMock(side_effect=["ctx-a", "ctx-b"])
    cdp.dispose_browser_context = AsyncMock()

    async def _create_page(*_: Any, **__: Any) -> _FakePage:
        return next(remaining)

    cdp.create_page_in_context = _create_page

    seen: list[tuple[object, object]] = []

    async def factory(inner_cdp: Any, **kwargs: Any) -> PooledSession:
        seen.append((kwargs.get("account_id"), kwargs.get("auth_file")))
        return await PooledSession.bootstrap(inner_cdp, **kwargs)

    async def _cdp_provider() -> Any:
        return cdp

    pool = InstancePool(
        _cdp_provider,
        specs=[InstanceSpec(count=1, surface="chat", min_tier="Free")],
        max_contexts=1,
        session_factory=factory,
    )
    slot = await pool.add_slot(pool.specs[0], lane="free")
    try:
        await pool.activate(
            slot.slot_id,
            account_id="account-A",
            auth_file="/accounts/account-A/auth.json",
            tier="Free",
        )
        await pool.activate(
            slot.slot_id,
            account_id="account-B",
            auth_file="/accounts/account-B/auth.json",
            tier="Free",
        )

        assert seen == [
            ("account-A", "/accounts/account-A/auth.json"),
            ("account-B", "/accounts/account-B/auth.json"),
        ]
        sources = [cast(str, p.cdp.calls[0][1]["source"]) for p in pages]
        assert sources[0] == build_fingerprint_script(
            build_fingerprint_profile(
                account_id="account-A", auth_file="/accounts/account-A/auth.json"
            )
        )
        assert sources[1] == build_fingerprint_script(
            build_fingerprint_profile(
                account_id="account-B", auth_file="/accounts/account-B/auth.json"
            )
        )
        assert sources[0] != sources[1]
    finally:
        await pool.close()
