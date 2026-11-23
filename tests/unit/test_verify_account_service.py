"""Unit tests for the verify-account session service (no real browser/network)."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import platform
import tempfile
import time
from dataclasses import fields as dataclass_fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from aistudio_api.api.routes_accounts import ImportCookiesRequest, import_cookies
from aistudio_api.api.state import RuntimeState
from aistudio_api.application import verify_account_service as verify_module
from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.application.account_service import AccountService
from aistudio_api.application.verify_account_service import (
    CdpVerifyBrowserDriver,
    PageProbe,
    QrChallenge,
    VerifyAccountNotFound,
    VerifyAccountService,
    VerifyBrowserUnavailable,
    VerifyChallengeTimeout,
    VerifyRegionBlocked,
    VerifySessionConflict,
    VerifySessionInvalid,
    VerifySessionNotFound,
    VerifySessionState,
)
from aistudio_api.config import settings
from aistudio_api.infrastructure.account.account_store import AccountStore

_QR = QrChallenge(
    qr_url="https://recaptcha.net/qr/ABCDEF123456",
    short_code="ABCDEF123456",
)
_VERIFY_URL = "https://aistudio.google.com/verify-account"


def _add_account(
    store: AccountStore,
    account_id: str,
    *,
    enabled: bool = True,
    verify_required: bool = False,
) -> None:
    store.save_account(
        name=account_id,
        email=f"{account_id}@example.com",
        storage_state={
            "cookies": [
                {
                    "name": "SID",
                    "value": "sid-value",
                    "domain": ".google.com",
                    "path": "/",
                }
            ],
            "origins": [],
        },
        account_id=account_id,
        auth_user="0",
        tier="Free",
        enabled=enabled,
    )
    if verify_required:
        store.update_account_refresh(account_id, verify_required=True)


class FakeDriver:
    def __init__(
        self,
        *,
        start_error: Exception | None = None,
        poll_results: list[PageProbe] | None = None,
        confirm_status: int | None = 2,
    ) -> None:
        self.start_error = start_error
        self.poll_results = list(poll_results or [PageProbe(_VERIFY_URL, False)])
        self.confirm_status = confirm_status
        self.closed = False
        self.started = False
        self.confirm_calls: list[str] = []

    async def start(self) -> QrChallenge:
        self.started = True
        if self.start_error is not None:
            raise self.start_error
        return _QR

    async def poll(self) -> PageProbe:
        if len(self.poll_results) > 1:
            return self.poll_results.pop(0)
        return self.poll_results[0]

    async def confirm_verified(self, auth_user: str) -> int | None:
        self.confirm_calls.append(auth_user)
        return self.confirm_status

    async def close(self) -> None:
        self.closed = True


class FakeRotator:
    def __init__(self) -> None:
        self.stats: dict[str, Any] = {}

    def get_account_stats(self, account_id: str) -> Any:
        return self.stats.setdefault(
            account_id,
            SimpleNamespace(verify_required=True, last_verify_required_at=123.0),
        )


def _service(
    store: AccountStore,
    drivers: list[FakeDriver],
    *,
    rotator: FakeRotator | None = None,
    **kwargs: Any,
) -> VerifyAccountService:
    def factory(_session: Any, _auth_file: Path) -> FakeDriver:
        return drivers.pop(0)

    return VerifyAccountService(
        store,
        cast(Any, rotator),
        driver_factory=factory,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_same_account_start_is_idempotent_and_other_account_conflicts(
    tmp_path: Path,
) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    _add_account(store, "acc_2", enabled=False)
    service = _service(store, [FakeDriver()])
    try:
        first = await service.start("acc_1")
        second = await service.start("acc_1")
        assert first["session_id"] == second["session_id"]
        assert first["state"] == VerifySessionState.PENDING.value
        assert first["qr_url"] == _QR.qr_url
        assert first["short_code"] == _QR.short_code
        assert isinstance(first["expires_at"], str)
        with pytest.raises(VerifySessionConflict):
            await service.start("acc_2")
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cancel_is_idempotent_and_closes_driver(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    drivers: list[FakeDriver] = []
    service = VerifyAccountService(
        store,
        None,
        driver_factory=lambda _session, _auth: (
            drivers.append(FakeDriver()) or drivers[-1]
        ),
    )
    try:
        starter = await service.start("acc_1")
        assert drivers[0].started is True
        cancelled = await service.cancel("acc_1")
        assert cancelled == {"state": VerifySessionState.CANCELLED.value}
        await service.wait_for_cleanup()
        assert drivers[0].closed is True
        again = await service.cancel("acc_1")
        assert again == {"state": VerifySessionState.CANCELLED.value}
        assert starter["state"] == VerifySessionState.PENDING.value
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_expiry_marks_expired_and_closes_driver(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    driver = FakeDriver()
    service = VerifyAccountService(
        store, None, driver_factory=lambda _s, _a: driver, ttl_seconds=1.0
    )
    try:
        started = await service.start("acc_1")
        session_id = str(started["session_id"])
        session = service.session
        assert session is not None
        session.expires_at = time.time() - 1.0
        status = await service.status("acc_1", session_id)
        assert status["state"] == VerifySessionState.EXPIRED.value
        await service.wait_for_cleanup()
        assert driver.closed is True
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_login_redirect_sets_session_invalid(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    driver = FakeDriver(start_error=VerifySessionInvalid("login_redirect"))
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    try:
        started = await service.start("acc_1")
        assert started["state"] == VerifySessionState.SESSION_INVALID.value
        assert started["message"] == "login_redirect"
        await service.wait_for_cleanup()
        assert driver.closed is True
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_region_redirect_sets_region_blocked(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    driver = FakeDriver(start_error=VerifyRegionBlocked("available_regions"))
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    try:
        started = await service.start("acc_1")
        assert started["state"] == VerifySessionState.REGION_BLOCKED.value
        await service.wait_for_cleanup()
        assert driver.closed is True
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_code_fetch_timeout_sets_error(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    driver = FakeDriver(start_error=VerifyChallengeTimeout("qr_timeout"))
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    try:
        started = await service.start("acc_1")
        assert started["state"] == VerifySessionState.ERROR.value
        assert started["message"] == "qr_timeout"
        await service.wait_for_cleanup()
        assert driver.closed is True
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_browser_unavailable_propagates_and_clears_session(
    tmp_path: Path,
) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")

    def factory(_session: Any, _auth_file: Path) -> FakeDriver:
        raise VerifyBrowserUnavailable("no browser")

    service = VerifyAccountService(store, None, driver_factory=factory)
    try:
        with pytest.raises(VerifyBrowserUnavailable):
            await service.start("acc_1")
        assert service.session is None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_passed_clears_verify_lock_keeps_enabled_and_closes_driver(
    tmp_path: Path,
) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1", enabled=True, verify_required=True)
    rotator = FakeRotator()
    driver = FakeDriver(
        poll_results=[PageProbe(_VERIFY_URL, True)],
        confirm_status=2,
    )
    service = VerifyAccountService(
        store,
        cast(Any, rotator),
        driver_factory=lambda _s, _a: driver,
        passed_retention_seconds=0.05,
    )
    try:
        started = await service.start("acc_1")
        session_id = str(started["session_id"])
        status = await service.status("acc_1", session_id)
        assert status["state"] == VerifySessionState.PASSED.value
        account = store.get_account("acc_1")
        assert account is not None
        assert account.verify_required is False
        assert account.enabled is True
        assert rotator.stats["acc_1"].verify_required is False
        assert driver.confirm_calls == ["0"]
        await service.wait_for_cleanup()
        assert driver.closed is True
        await asyncio.sleep(0.08)
        with pytest.raises(VerifySessionNotFound):
            await service.status("acc_1", session_id)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_page_success_without_status_confirmation_stays_pending(
    tmp_path: Path,
) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1", verify_required=True)
    driver = FakeDriver(
        poll_results=[PageProbe(_VERIFY_URL, True)],
        confirm_status=1,
    )
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    try:
        started = await service.start("acc_1")
        status = await service.status("acc_1", str(started["session_id"]))
        assert status["state"] == VerifySessionState.PENDING.value
        account = store.get_account("acc_1")
        assert account is not None
        assert account.verify_required is True
        assert driver.closed is False
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_close_cleans_pending_session_and_driver(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    driver = FakeDriver()
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    await service.start("acc_1")
    await service.close()
    assert driver.closed is True
    assert service.session is None


class _FakeProcess:
    def __init__(self) -> None:
        self.pid = 2_000_000_000
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.returncode = 0
        return 0


@pytest.mark.asyncio
async def test_driver_close_terminates_process_and_removes_temp_dir(
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    driver = CdpVerifyBrowserDriver(auth_file=auth_file)
    user_data_dir = tempfile.mkdtemp(prefix="verify-close-test-")
    process = _FakeProcess()
    driver._user_data_dir = user_data_dir
    driver._process = cast(Any, process)

    await driver.close()

    if platform.system() == "Windows":
        # Windows reaps the whole tree via taskkill /T before the root exits.
        assert process.returncode == 0
        assert process.killed is False
    else:
        assert process.terminated is True
        assert process.killed is False
    assert not Path(user_data_dir).exists()
    await driver.close()
    if platform.system() != "Windows":
        assert process.terminated is True


def test_verify_chrome_env_override_wins(tmp_path: Path) -> None:
    fake = tmp_path / "chrome"
    fake.write_text("", encoding="utf-8")
    driver = CdpVerifyBrowserDriver(
        auth_file=tmp_path / "auth.json", chrome_path=str(fake)
    )
    assert driver._resolve_binary() == fake


def test_verify_chrome_missing_hint_lists_all_three_layouts(tmp_path: Path) -> None:
    driver = CdpVerifyBrowserDriver(
        auth_file=tmp_path / "auth.json",
        chrome_path=str(tmp_path / "missing-chrome"),
    )
    with pytest.raises(VerifyBrowserUnavailable) as info:
        driver._resolve_binary()
    message = str(info.value)
    assert ".cloakbrowser" in message
    assert "run-chrome.sh" in message
    assert "AISTUDIO_VERIFY_CHROME_PATH" in message


class _SlowDriver:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = False

    async def start(self) -> QrChallenge:
        self.started.set()
        await asyncio.Event().wait()
        return _QR

    async def poll(self) -> PageProbe:
        return PageProbe(_VERIFY_URL, False)

    async def confirm_verified(self, auth_user: str) -> int | None:
        del auth_user
        return None

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_cancel_during_start_aborts_launch_and_cleans_up(
    tmp_path: Path,
) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    driver = _SlowDriver()
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    started = asyncio.create_task(service.start("acc_1"))
    try:
        await asyncio.wait_for(driver.started.wait(), timeout=2.0)
        assert service.session is not None
        assert service.session.state is VerifySessionState.STARTING

        began = time.monotonic()
        cancelled = await service.cancel("acc_1")
        elapsed = time.monotonic() - began
        result = await asyncio.wait_for(started, timeout=2.0)

        assert cancelled == {"state": VerifySessionState.CANCELLED.value}
        assert result["state"] == VerifySessionState.CANCELLED.value
        await service.wait_for_cleanup()
        assert driver.closed is True
        assert elapsed < 2.0
        assert service.session is not None
        assert service.session.state is VerifySessionState.CANCELLED
    finally:
        started.cancel()
        await service.close()


@pytest.mark.asyncio
async def test_concurrent_start_for_other_account_conflicts_while_starting(
    tmp_path: Path,
) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    _add_account(store, "acc_2")
    driver = _SlowDriver()
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    started = asyncio.create_task(service.start("acc_1"))
    try:
        await asyncio.wait_for(driver.started.wait(), timeout=2.0)
        with pytest.raises(VerifySessionConflict):
            await service.start("acc_2")
        await service.cancel("acc_1")
        await asyncio.wait_for(started, timeout=2.0)
    finally:
        started.cancel()
        await service.close()


@pytest.mark.asyncio
async def test_verify_clear_persists_across_rotator_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rotator_file = tmp_path / "data" / "rotator_state.json"
    monkeypatch.setenv("AISTUDIO_ROTATOR_STATE_FILE", str(rotator_file))
    monkeypatch.setenv("AISTUDIO_PERSIST_ROTATOR", "1")

    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1", verify_required=True)
    rotator = AccountRotator(store)
    rotator.record_verify_required("acc_1")
    assert rotator.get_account_stats("acc_1") is not None
    assert rotator.get_account_stats("acc_1").verify_required is True  # type: ignore[union-attr]

    driver = FakeDriver(
        poll_results=[PageProbe(_VERIFY_URL, True)],
        confirm_status=2,
    )
    service = VerifyAccountService(
        store,
        rotator,
        driver_factory=lambda _s, _a: driver,
        passed_retention_seconds=0.05,
    )
    try:
        started = await service.start("acc_1")
        status = await service.status("acc_1", str(started["session_id"]))
        assert status["state"] == VerifySessionState.PASSED.value
        saved = rotator_file.read_text(encoding="utf-8")
        assert '"verify_required": false' in saved
    finally:
        await service.close()

    stale = json.loads(rotator_file.read_text(encoding="utf-8"))
    stale["acc_1"]["verify_required"] = True
    rotator_file.write_text(json.dumps(stale), encoding="utf-8")

    reloaded = AccountRotator(store)
    assert reloaded.get_all_stats()["acc_1"]["verify_required"] is False
    assert reloaded.is_auth_locked("acc_1") is False


@pytest.mark.asyncio
async def test_page_failure_state_maps_to_failed_instead_of_waiting_ttl(
    tmp_path: Path,
) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1", verify_required=True)
    driver = FakeDriver(
        poll_results=[
            PageProbe(_VERIFY_URL, False, failed=True),
        ]
    )
    service = VerifyAccountService(
        store, None, driver_factory=lambda _s, _a: driver, ttl_seconds=180.0
    )
    try:
        started = await service.start("acc_1")
        status = await service.status("acc_1", str(started["session_id"]))
        assert status["state"] == VerifySessionState.FAILED.value
        assert status["message"] == "page_failed"
        await service.wait_for_cleanup()
        assert driver.closed is True
        account = store.get_account("acc_1")
        assert account is not None and account.verify_required is True
    finally:
        await service.close()


class _FakeCdp:
    def __init__(self, owner: _FakeQrPage) -> None:
        self._owner = owner
        self.send_calls: list[str] = []

    async def send(
        self, method: str, params: dict[str, object] | None = None
    ) -> dict[str, object]:
        self.send_calls.append(method)
        if method == "Page.getFrameTree":
            return {
                "frameTree": {
                    "frame": {"id": "main", "url": _VERIFY_URL},
                    "childFrames": [
                        {
                            "frame": {
                                "id": "bframe_1",
                                "url": "https://www.google.com/recaptcha/api2/bframe",
                            }
                        }
                    ],
                }
            }
        if method == "Page.createIsolatedWorld":
            self._owner.worlds += 1
            return {"executionContextId": 100 + self._owner.worlds}
        if method == "Runtime.evaluate":
            self._owner.evaluate_calls += 1
            if self._owner.fail_first_evaluate and self._owner.evaluate_calls == 1:
                raise RuntimeError("Execution context was destroyed")
            if self._owner.qr_after_click and not self._owner.clicked:
                return {"result": {"value": {"hasCanvas": False}}}
            return {
                "result": {
                    "value": {
                        "href": "https://recaptcha.net/qr/ABCDEF123456",
                        "title": "",
                        "fallback": "",
                    }
                }
            }
        return {}


class _FakeQrPage:
    def __init__(
        self,
        *,
        fail_first_evaluate: bool = False,
        qr_after_click: bool = False,
        disabled_attempts: int = 0,
    ) -> None:
        self.fail_first_evaluate = fail_first_evaluate
        self.qr_after_click = qr_after_click
        self.disabled_attempts = disabled_attempts
        self.worlds = 0
        self.evaluate_calls = 0
        self.click_attempts = 0
        self.clicked = False
        self.cdp = _FakeCdp(self)

    async def evaluate(self, expression: str, *args: Any, **kwargs: Any) -> object:
        del args, kwargs
        if "window.location.href" in expression:
            return _VERIFY_URL
        if "verify device" in expression:
            self.click_attempts += 1
            # The in-page predicate finds only a disabled node for the first
            # attempts, so the JS returns false without dispatching a click.
            if self.click_attempts <= self.disabled_attempts:
                return False
            self.clicked = True
            return True
        return None


def test_click_verify_device_js_filters_disabled_buttons() -> None:
    js = verify_module._CLICK_VERIFY_DEVICE_JS
    assert "!el.disabled" in js
    assert "aria-disabled" in js
    assert verify_module._VERIFY_BUTTON_USABLE_PREDICATE in js
    # Both candidate scans must require an enabled node, and the click is
    # unreachable when no usable match exists.
    assert js.count("usable(el)") >= 2
    assert "if (!match) return false;" in js
    assert js.index("if (!match) return false;") < js.index("match.click()")


def test_verify_button_usable_rejects_disabled_nodes() -> None:
    usable = verify_module._verify_button_usable
    assert usable(disabled=False, aria_disabled=None) is True
    assert usable(disabled=False, aria_disabled="false") is True
    assert usable(disabled=True, aria_disabled=None) is False
    assert usable(disabled=False, aria_disabled="true") is False


@pytest.mark.asyncio
async def test_wait_for_qr_retries_disabled_click_until_enabled() -> None:
    page = _FakeQrPage(qr_after_click=True, disabled_attempts=1)
    driver = CdpVerifyBrowserDriver(
        auth_file=Path("unused.json"), code_timeout_seconds=3.0
    )
    challenge = await driver._wait_for_qr(cast(Any, page))
    assert challenge.short_code == "ABCDEF123456"
    assert page.click_attempts >= 2
    assert page.clicked is True


@pytest.mark.asyncio
async def test_bframe_world_is_rebuilt_after_context_invalidation() -> None:
    page = _FakeQrPage(fail_first_evaluate=True)
    driver = CdpVerifyBrowserDriver(
        auth_file=Path("unused.json"), code_timeout_seconds=2.0
    )
    challenge = await driver._read_bframe_qr(cast(Any, page))
    assert challenge is not None
    assert challenge.short_code == "ABCDEF123456"
    assert page.worlds == 2


@pytest.mark.asyncio
async def test_windows_terminate_kills_tree_before_root_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    async def fake_taskkill(pid: int) -> None:
        calls.append(pid)

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(verify_module, "_taskkill_tree_windows", fake_taskkill)
    process = _FakeProcess()
    await verify_module._terminate_process(cast(Any, process), timeout_s=0.1)
    assert calls == [process.pid]
    assert process.returncode == 0


class _SlowConfirmDriver:
    """``confirm_verified`` blocks until cancelled; models a slow RPC."""

    def __init__(self) -> None:
        self.confirm_started = asyncio.Event()
        self.closed = False

    async def start(self) -> QrChallenge:
        return _QR

    async def poll(self) -> PageProbe:
        return PageProbe(_VERIFY_URL, True)

    async def confirm_verified(self, auth_user: str) -> int | None:
        del auth_user
        self.confirm_started.set()
        await asyncio.sleep(8.0)
        return 2

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_cancel_returns_within_budget_while_confirm_is_slow(
    tmp_path: Path,
) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    driver = _SlowConfirmDriver()
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    try:
        started = await service.start("acc_1")
        session_id = str(started["session_id"])
        status_task = asyncio.create_task(service.status("acc_1", session_id))
        await asyncio.wait_for(driver.confirm_started.wait(), timeout=2.0)

        began = time.monotonic()
        cancelled = await service.cancel("acc_1")
        elapsed = time.monotonic() - began
        result = await asyncio.wait_for(status_task, timeout=2.0)

        assert cancelled == {"state": VerifySessionState.CANCELLED.value}
        assert result["state"] == VerifySessionState.CANCELLED.value
        assert elapsed < 2.0
        await service.wait_for_cleanup()
        assert driver.closed is True
    finally:
        await service.close()


class _SlowCloseDriver:
    def __init__(self, close_delay: float = 0.4) -> None:
        self.close_delay = close_delay
        self.closed = False

    async def start(self) -> QrChallenge:
        return _QR

    async def poll(self) -> PageProbe:
        return PageProbe(_VERIFY_URL, False)

    async def confirm_verified(self, auth_user: str) -> int | None:
        del auth_user
        return None

    async def close(self) -> None:
        await asyncio.sleep(self.close_delay)
        self.closed = True


@pytest.mark.asyncio
async def test_slow_driver_close_does_not_block_cancel(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    driver = _SlowCloseDriver()
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    try:
        await service.start("acc_1")
        began = time.monotonic()
        cancelled = await service.cancel("acc_1")
        elapsed = time.monotonic() - began
        assert cancelled == {"state": VerifySessionState.CANCELLED.value}
        assert elapsed < 2.0
        # cancel returned while the driver teardown was still in flight:
        # teardown runs in a bounded background task, not on the cancel path.
        assert driver.closed is False
        await service.wait_for_cleanup()
        assert driver.closed is True
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cancelling_start_coroutine_marks_cancelled_and_frees_driver(
    tmp_path: Path,
) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    slow = _SlowDriver()
    fresh = FakeDriver()
    drivers = [slow, fresh]
    service = VerifyAccountService(
        store, None, driver_factory=lambda _s, _a: drivers.pop(0)
    )
    started = asyncio.create_task(service.start("acc_1"))
    try:
        await asyncio.wait_for(slow.started.wait(), timeout=2.0)
        started.cancel()
        with pytest.raises(asyncio.CancelledError):
            await started
        await service.wait_for_cleanup()

        session = service.session
        assert session is not None
        assert session.state is VerifySessionState.CANCELLED
        assert slow.closed is True

        restarted = await service.start("acc_1")
        assert restarted["state"] == VerifySessionState.PENDING.value
    finally:
        await service.close()


class _HangingPage:
    async def close(self) -> None:
        await asyncio.sleep(30.0)


@pytest.mark.asyncio
async def test_close_removes_temp_dir_even_when_teardown_times_out(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    driver = CdpVerifyBrowserDriver(auth_file=tmp_path / "auth.json")
    driver._user_data_dir = str(profile)
    driver._page = cast(Any, _HangingPage())
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(driver.close(), timeout=0.1)
    # The bounded outer wait_for cancels teardown; rmtree must still run.
    assert not profile.exists()


def test_verify_chrome_command_has_low_memory_and_fingerprint_args(
    tmp_path: Path,
) -> None:
    command = verify_module._build_verify_chrome_command(
        binary=tmp_path / "chrome",
        port=9222,
        user_data_dir=str(tmp_path / "profile"),
        proxy=None,
    )
    assert "--renderer-process-limit=1" in command
    assert "--in-process-gpu" in command
    assert "--js-flags=--max-old-space-size=128 --expose-gc --optimize-for-size" in (
        command
    )
    assert "--fingerprint-platform=windows" in command
    assert f"--fingerprint-timezone={settings.timezone}" in command
    # Aligned with browser_engine.build_chromium_args (P2-R5).
    assert f"--fingerprint-locale={settings.locale}" in command
    assert "--use-gl=angle" in command
    assert "--use-angle=swiftshader" in command
    assert "--no-zygote" in command
    assert "--disable-gpu-compositing" in command
    disable_features = next(
        arg for arg in command if arg.startswith("--disable-features=")
    )
    assert "Translate" in disable_features
    assert "SitePerProcess" in disable_features
    # Previously verified args must stay.
    assert "--headless=new" in command
    assert "--mute-audio" in command
    assert "--window-size=1280,900" in command
    assert sum(arg.startswith("--window-size=") for arg in command) == 1
    # Intentional difference from a pooled instance: the CDP debugging port is
    # required to read the reCAPTCHA bframe, so it is not stripped.
    assert "--remote-debugging-port=9222" in command
    # Default egress is direct: never inherit settings.proxy_url.
    assert not any(arg.startswith("--proxy-server=") for arg in command)


def test_verify_chrome_command_defaults_direct_despite_settings_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "proxy_url", "http://env-proxy.invalid:3128")
    command = verify_module._build_verify_chrome_command(
        binary=tmp_path / "chrome",
        port=9222,
        user_data_dir=str(tmp_path / "profile"),
        proxy=None,
    )
    assert not any(arg.startswith("--proxy-server=") for arg in command)


def test_verify_chrome_command_forwards_explicit_verify_proxy(tmp_path: Path) -> None:
    command = verify_module._build_verify_chrome_command(
        binary=tmp_path / "chrome",
        port=9222,
        user_data_dir=str(tmp_path / "profile"),
        proxy="http://127.0.0.1:10809",
    )
    assert "--proxy-server=http://127.0.0.1:10809" in command


def _write_verify_auth(tmp_path: Path) -> Path:
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "SAPISID",
                        "value": "sapisid-value",
                        "domain": ".google.com",
                        "path": "/",
                    }
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )
    return auth


def _rpc_driver(tmp_path: Path, handler: Any) -> CdpVerifyBrowserDriver:
    return CdpVerifyBrowserDriver(
        auth_file=_write_verify_auth(tmp_path),
        http_transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [([2], 2), ([1], 1), ([[2]], 2)],
)
async def test_confirm_rpc_parses_scalar_nested_and_pending(
    tmp_path: Path, payload: object, expected: int
) -> None:
    driver = _rpc_driver(tmp_path, lambda _req: httpx.Response(200, json=payload))
    assert await driver.confirm_verified("0") == expected


@pytest.mark.asyncio
async def test_confirm_rpc_401_with_marker_raises_session_invalid(
    tmp_path: Path,
) -> None:
    driver = _rpc_driver(
        tmp_path,
        lambda _req: httpx.Response(401, json={"error": "SESSION_COOKIE_INVALID"}),
    )
    with pytest.raises(VerifySessionInvalid):
        await driver.confirm_verified("0")


@pytest.mark.asyncio
async def test_confirm_rpc_401_without_marker_stays_pending(tmp_path: Path) -> None:
    driver = _rpc_driver(
        tmp_path, lambda _req: httpx.Response(401, json={"error": "transient"})
    )
    assert await driver.confirm_verified("0") is None


@pytest.mark.asyncio
async def test_confirm_rpc_403_region_marker_raises_region_blocked(
    tmp_path: Path,
) -> None:
    driver = _rpc_driver(
        tmp_path,
        lambda _req: httpx.Response(
            403, json={"error": "Region not supported", "path": "available-regions"}
        ),
    )
    with pytest.raises(VerifyRegionBlocked):
        await driver.confirm_verified("0")


@pytest.mark.asyncio
async def test_confirm_rpc_403_without_marker_stays_pending(tmp_path: Path) -> None:
    driver = _rpc_driver(
        tmp_path, lambda _req: httpx.Response(403, json={"error": "permission"})
    )
    assert await driver.confirm_verified("0") is None


@pytest.mark.asyncio
async def test_confirm_rpc_timeout_and_non_json_return_none(tmp_path: Path) -> None:
    def timeout_handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    timeout_driver = _rpc_driver(tmp_path, timeout_handler)
    assert await timeout_driver.confirm_verified("0") is None

    non_json_driver = _rpc_driver(
        tmp_path, lambda _req: httpx.Response(200, text="<html>nope</html>")
    )
    assert await non_json_driver.confirm_verified("0") is None


@pytest.mark.asyncio
async def test_confirm_rpc_client_disables_env_proxy_and_forwards_explicit_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class _FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, *args: Any) -> bool:
            return False

        async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
            del args, kwargs
            return httpx.Response(200, json=[2])

    monkeypatch.setattr(verify_module.httpx, "AsyncClient", _FakeAsyncClient)
    driver = CdpVerifyBrowserDriver(
        auth_file=_write_verify_auth(tmp_path),
        proxy="http://127.0.0.1:10809",
    )
    assert await driver.confirm_verified("0") == 2
    # Explicit AISTUDIO_VERIFY_PROXY is forwarded, and host HTTPS_PROXY /
    # ALL_PROXY must never leak into the confirmation RPC.
    assert captured["trust_env"] is False
    assert captured["proxy"] == "http://127.0.0.1:10809"
    assert captured["transport"] is None


@pytest.mark.asyncio
async def test_reimport_same_process_clears_rotator_memory_verify_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "AISTUDIO_ROTATOR_STATE_FILE", str(tmp_path / "rotator_state.json")
    )
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    rotator = AccountRotator(store)
    rotator.record_verify_required("acc_1")
    stats = rotator.get_account_stats("acc_1")
    assert stats is not None and stats.verify_required is True

    svc = AccountService(store)
    state = RuntimeState(account_service=svc, client=None, rotator=rotator)
    response = await import_cookies(
        ImportCookiesRequest(
            cookies="SID=fresh; SAPISID=fresh",
            email="acc_1@example.com",
            account_id="acc_1",
            tier="Free",
        ),
        account_service=svc,
        runtime_state=state,
    )

    assert response.account_id == "acc_1"
    account = store.get_account("acc_1")
    assert account is not None and account.verify_required is False
    # Same process (no restart): the rotator memory flag must already be clear.
    cleared = rotator.get_account_stats("acc_1")
    assert cleared is not None and cleared.verify_required is False
    assert rotator.is_auth_locked("acc_1") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["probe_and_import", "import_bundle"])
async def test_reimport_entrypoints_clear_verify_lock_same_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """N-2: both bulk import entrypoints clear store + rotator locks in-process."""
    from unittest.mock import AsyncMock

    from aistudio_api.api.routes_accounts import (
        BundleAccountItem,
        ImportBundleRequest,
        ProbeAndImportRequest,
        import_bundle,
        probe_and_import,
    )
    from aistudio_api.infrastructure.account import cookie_parser

    monkeypatch.setenv(
        "AISTUDIO_ROTATOR_STATE_FILE", str(tmp_path / "rotator_state.json")
    )
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1", verify_required=True)
    rotator = AccountRotator(store)
    rotator.record_verify_required("acc_1")
    stats = rotator.get_account_stats("acc_1")
    assert stats is not None and stats.verify_required is True

    svc = AccountService(store)
    state = RuntimeState(account_service=svc, client=None, rotator=rotator)

    if entry == "probe_and_import":
        monkeypatch.setattr(
            cookie_parser,
            "probe_google_accounts_infinite",
            AsyncMock(
                return_value=[
                    {"auth_user": "0", "email": "acc_1@example.com", "status_code": 200}
                ]
            ),
        )
        await probe_and_import(
            ProbeAndImportRequest(cookies="SID=fresh; SAPISID=fresh"),
            account_service=svc,
            runtime_state=state,
        )
    else:
        await import_bundle(
            ImportBundleRequest(
                accounts=[
                    BundleAccountItem(
                        email="acc_1@example.com",
                        cookies="SID=fresh; SAPISID=fresh",
                        auth_user="0",
                    )
                ]
            ),
            account_service=svc,
            runtime_state=state,
        )

    account = store.get_account("acc_1")
    assert account is not None and account.verify_required is False
    # Same process (no restart): the rotator memory flag must already be clear.
    cleared = rotator.get_account_stats("acc_1")
    assert cleared is not None and cleared.verify_required is False
    assert rotator.is_auth_locked("acc_1") is False


class _DelayedStartDriver:
    """``start`` completes after a delay (or raises) to model a slow launch."""

    def __init__(
        self,
        *,
        delay: float = 0.2,
        error: BaseException | None = None,
    ) -> None:
        self.delay = delay
        self.error = error
        self.started = asyncio.Event()
        self.closed = False
        self.confirm_calls: list[str] = []

    async def start(self) -> QrChallenge:
        self.started.set()
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return _QR

    async def poll(self) -> PageProbe:
        return PageProbe(_VERIFY_URL, False)

    async def confirm_verified(self, auth_user: str) -> int | None:
        self.confirm_calls.append(auth_user)
        return 1

    async def close(self) -> None:
        self.closed = True


def test_default_ttl_is_300_seconds() -> None:
    assert verify_module.DEFAULT_TTL_SECONDS == 300.0


def test_default_code_timeout_is_45_seconds(tmp_path: Path) -> None:
    # A 45s budget covers the observed +22~24s QR arrival on a weak host.
    # There is no env override for this value: only the constructor argument.
    assert verify_module.DEFAULT_CODE_TIMEOUT_SECONDS == 45.0

    driver = CdpVerifyBrowserDriver(auth_file=tmp_path / "auth.json")
    assert driver._code_timeout == 45.0

    store = AccountStore(tmp_path / "accounts")
    service = VerifyAccountService(store, None)
    assert service._code_timeout == 45.0

    # Explicit constructor override still wins (no env layer involved).
    overridden = CdpVerifyBrowserDriver(
        auth_file=tmp_path / "auth.json", code_timeout_seconds=3.0
    )
    assert overridden._code_timeout == 3.0


@pytest.mark.asyncio
async def test_new_session_expires_after_default_ttl(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    driver = FakeDriver()
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    try:
        await service.start("acc_1")
        session = service.session
        assert session is not None
        assert session.expires_at - session.created_at == pytest.approx(300.0)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_duplicate_start_waits_for_in_flight_launch(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    driver = _DelayedStartDriver(delay=0.25)
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    first = asyncio.create_task(service.start("acc_1"))
    try:
        await asyncio.wait_for(driver.started.wait(), timeout=2.0)
        began = time.monotonic()
        second = await asyncio.wait_for(service.start("acc_1"), timeout=3.0)
        elapsed = time.monotonic() - began
        result = await asyncio.wait_for(first, timeout=2.0)

        # The duplicate must observe the real outcome, never a bare ``starting``.
        assert second["state"] == VerifySessionState.PENDING.value
        assert second["qr_url"] == _QR.qr_url
        assert second["session_id"] == result["session_id"]
        assert elapsed >= 0.1
    finally:
        first.cancel()
        await service.close()


@pytest.mark.asyncio
async def test_duplicate_start_sees_launch_failure_without_hanging(
    tmp_path: Path,
) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    driver = _DelayedStartDriver(delay=0.2, error=VerifyChallengeTimeout("qr_timeout"))
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    first = asyncio.create_task(service.start("acc_1"))
    try:
        await asyncio.wait_for(driver.started.wait(), timeout=2.0)
        second = await asyncio.wait_for(service.start("acc_1"), timeout=3.0)
        result = await asyncio.wait_for(first, timeout=2.0)

        assert second["state"] == VerifySessionState.ERROR.value
        assert second["message"] == "qr_timeout"
        assert result["state"] == VerifySessionState.ERROR.value
        await service.wait_for_cleanup()
        assert driver.closed is True
    finally:
        first.cancel()
        await service.close()


@pytest.mark.asyncio
async def test_duplicate_start_sees_cancel_without_hanging(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    driver = _DelayedStartDriver(delay=5.0)
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    first = asyncio.create_task(service.start("acc_1"))
    try:
        await asyncio.wait_for(driver.started.wait(), timeout=2.0)
        duplicate = asyncio.create_task(service.start("acc_1"))
        # Let the duplicate acquire the lock and attach to the in-flight launch
        # before the cancel, otherwise it would legitimately create a new session.
        await asyncio.sleep(0.05)
        await service.cancel("acc_1")

        result = await asyncio.wait_for(first, timeout=2.0)
        duplicate_result = await asyncio.wait_for(duplicate, timeout=2.0)
        assert result["state"] == VerifySessionState.CANCELLED.value
        assert duplicate_result["state"] == VerifySessionState.CANCELLED.value
    finally:
        first.cancel()
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("confirm_status", "expected_state", "expected_message", "cleared"),
    [
        (2, VerifySessionState.PASSED.value, "verified", True),
        (3, VerifySessionState.PASSED.value, "not_required", True),
        (1, VerifySessionState.PENDING.value, "still_pending", False),
        (None, VerifySessionState.PENDING.value, "still_pending", False),
    ],
)
async def test_force_status_queries_upstream_without_page_success(
    tmp_path: Path,
    confirm_status: int | None,
    expected_state: str,
    expected_message: str,
    cleared: bool,
) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1", verify_required=True)
    driver = FakeDriver(
        poll_results=[PageProbe(_VERIFY_URL, False)],
        confirm_status=confirm_status,
    )
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    try:
        started = await service.start("acc_1")
        status = await service.status("acc_1", str(started["session_id"]), force=True)
        assert status["state"] == expected_state
        assert status["message"] == expected_message
        assert driver.confirm_calls == ["0"]
        account = store.get_account("acc_1")
        assert account is not None
        assert account.verify_required is (not cleared)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_non_force_status_throttles_upstream_rpc(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1", verify_required=True)
    driver = FakeDriver(
        poll_results=[PageProbe(_VERIFY_URL, False)],
        confirm_status=1,
    )
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    try:
        started = await service.start("acc_1")
        session_id = str(started["session_id"])

        first = await service.status("acc_1", session_id)
        assert first["state"] == VerifySessionState.PENDING.value
        assert first["message"] == "still_pending"
        assert len(driver.confirm_calls) == 1
        session = service.session
        assert session is not None
        assert session.last_confirm_at > 0.0

        # Inside the 10s window a plain poll must not hit upstream again.
        await service.status("acc_1", session_id)
        assert len(driver.confirm_calls) == 1

        session.last_confirm_at = time.time() - 11.0
        await service.status("acc_1", session_id)
        assert len(driver.confirm_calls) == 2
    finally:
        await service.close()


# ---------------------------------------------------------------------------
# Session-less ``check`` (POST /verify/check) coverage
# ---------------------------------------------------------------------------


def _add_check_account(
    store: AccountStore,
    account_id: str,
    *,
    verify_required: bool = True,
    cookies: list[dict[str, object]] | None = None,
) -> None:
    store.save_account(
        name=account_id,
        email=f"{account_id}@example.com",
        storage_state={
            "cookies": cookies
            if cookies is not None
            else [
                {
                    "name": "SAPISID",
                    "value": "sapisid-value",
                    "domain": ".google.com",
                    "path": "/",
                },
                {
                    "name": "SID",
                    "value": "sid-value",
                    "domain": ".google.com",
                    "path": "/",
                },
            ],
            "origins": [],
        },
        account_id=account_id,
        auth_user="3",
        tier="Free",
        enabled=True,
    )
    if verify_required:
        store.update_account_refresh(account_id, verify_required=True)


class FakeStatusDriver:
    """Status-only probe: records confirm calls, never starts a browser."""

    def __init__(
        self,
        status: int | None = 2,
        *,
        error: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self.status = status
        self.error = error
        self.delay = delay
        self.confirm_calls: list[str] = []

    async def confirm_verified(self, auth_user: str) -> int | None:
        self.confirm_calls.append(auth_user)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.status


def _check_service(
    store: AccountStore,
    status_driver: FakeStatusDriver,
    *,
    rotator: FakeRotator | None = None,
    **kwargs: Any,
) -> VerifyAccountService:
    def unused_start_factory(_session: Any, _auth_file: Path) -> Any:
        raise AssertionError("check must never create a QR/浏览器会话")

    return VerifyAccountService(
        store,
        cast(Any, rotator),
        driver_factory=unused_start_factory,
        status_driver_factory=lambda _auth_file, _auth_user: status_driver,
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "message"), [(2, "verified"), (3, "not_required")])
async def test_check_passed_status_clears_verify_lock_without_session(
    tmp_path: Path, status: int, message: str
) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_check_account(store, "acc_1", verify_required=True)
    rotator = FakeRotator()
    driver = FakeStatusDriver(status=status)
    service = _check_service(store, driver, rotator=rotator)
    try:
        result = await service.check("acc_1")

        assert result == {
            "state": "passed",
            "message": message,
            "upstream_status": status,
            "verify_required": False,
        }
        account = store.get_account("acc_1")
        assert account is not None and account.verify_required is False
        # Same rotator path as a real pass: memory flag is cleared too.
        assert rotator.stats["acc_1"].verify_required is False
        assert driver.confirm_calls == ["3"]
        # No QR session, no instance, no browser launch.
        assert service.session is None
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "message"),
    [(1, "still_pending"), (None, "upstream_pending")],
)
async def test_check_pending_status_keeps_verify_lock(
    tmp_path: Path, status: int | None, message: str
) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_check_account(store, "acc_1", verify_required=True)
    driver = FakeStatusDriver(status=status)
    service = _check_service(store, driver)
    try:
        result = await service.check("acc_1")

        assert result["state"] == "pending"
        assert result["message"] == message
        assert result["upstream_status"] == status
        assert result["verify_required"] is True
        account = store.get_account("acc_1")
        assert account is not None and account.verify_required is True
        assert service.session is None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_check_missing_sapisid_returns_error_without_upstream_call(
    tmp_path: Path,
) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_check_account(
        store,
        "acc_1",
        cookies=[
            {"name": "SID", "value": "sid-only", "domain": ".google.com", "path": "/"}
        ],
    )
    driver = FakeStatusDriver(status=2)
    service = _check_service(store, driver)
    try:
        result = await service.check("acc_1")
    finally:
        await service.close()

    assert result["state"] == "error"
    assert "SAPISID" in str(result["message"])
    assert result["upstream_status"] is None
    assert driver.confirm_calls == []


@pytest.mark.asyncio
async def test_check_missing_auth_file_returns_readable_error(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_check_account(store, "acc_1")
    auth_file = store.get_auth_path_optional("acc_1")
    assert auth_file is not None
    auth_file.unlink()
    driver = FakeStatusDriver(status=2)
    service = _check_service(store, driver)
    try:
        result = await service.check("acc_1")
    finally:
        await service.close()

    assert result["state"] == "error"
    assert "凭据文件缺失" in str(result["message"])
    assert driver.confirm_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (VerifySessionInvalid("session_invalid"), "会话已失效"),
        (VerifyRegionBlocked("region_blocked"), "地区不支持"),
        (RuntimeError("SID=abc boom"), "RuntimeError"),
    ],
)
async def test_check_upstream_failures_map_to_error(
    tmp_path: Path, error: BaseException, expected: str
) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_check_account(store, "acc_1", verify_required=True)
    driver = FakeStatusDriver(error=error)
    service = _check_service(store, driver)
    try:
        result = await service.check("acc_1")
    finally:
        await service.close()

    assert result["state"] == "error"
    assert expected in str(result["message"])
    assert result["upstream_status"] is None
    account = store.get_account("acc_1")
    assert account is not None and account.verify_required is True


@pytest.mark.asyncio
async def test_check_unknown_account_raises(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    service = _check_service(store, FakeStatusDriver())
    try:
        with pytest.raises(VerifyAccountNotFound):
            await service.check("acc_missing")
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_concurrent_checks_share_one_upstream_call(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_check_account(store, "acc_1", verify_required=True)
    driver = FakeStatusDriver(status=2, delay=0.05)
    service = _check_service(store, driver)
    try:
        first, second = await asyncio.gather(
            service.check("acc_1"), service.check("acc_1")
        )
    finally:
        await service.close()

    assert first == second
    assert first["state"] == "passed"
    assert len(driver.confirm_calls) == 1


@pytest.mark.asyncio
async def test_check_is_idempotent_after_pass(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_check_account(store, "acc_1", verify_required=True)
    driver = FakeStatusDriver(status=2)
    service = _check_service(store, driver)
    try:
        first = await service.check("acc_1")
        second = await service.check("acc_1")
    finally:
        await service.close()

    assert first["state"] == second["state"] == "passed"
    assert second["verify_required"] is False
    # Both checks are harmless upstream confirmations; the lock stays cleared.
    assert len(driver.confirm_calls) == 2


class _DescribingDriver(FakeDriver):
    async def describe_page(self) -> dict[str, object]:
        return {
            "url": _VERIFY_URL,
            "title": "Verify your account",
            "text": "Verify device for user@example.com SID=abc123",
            "verify_button": True,
            "qr_canvas": True,
        }


@pytest.mark.asyncio
async def test_refresh_logs_page_state_and_upstream_status_without_secrets(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="aistudio.verify-account")
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1", verify_required=True)
    driver = _DescribingDriver(
        poll_results=[PageProbe(_VERIFY_URL, False)],
        confirm_status=1,
    )
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    try:
        started = await service.start("acc_1")
        await service.status("acc_1", str(started["session_id"]), force=True)
    finally:
        await service.close()

    page_records = [
        r.getMessage() for r in caplog.records if "验证会话页面状态" in r.getMessage()
    ]
    assert page_records, "page state must be logged on every poll"
    page_log = page_records[-1]
    assert "page_url=https://aistudio.google.com/verify-account" in page_log
    assert "page_title=Verify your account" in page_log
    assert "probe_success=False" in page_log
    assert "user@example.com" not in page_log
    assert "***@***" in page_log
    assert "SID=***" in page_log

    manual = [
        r.getMessage() for r in caplog.records if "验证会话手动检查" in r.getMessage()
    ]
    assert manual and "upstream_status=1" in manual[-1]
    assert "manual=true" in manual[-1]


@pytest.mark.asyncio
async def test_start_failure_logs_exception_type_and_masked_message(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="aistudio.verify-account")
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    driver = FakeDriver(start_error=RuntimeError("launch boom SID=secretvalue"))
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    try:
        started = await service.start("acc_1")
    finally:
        await service.close()

    assert started["state"] == VerifySessionState.ERROR.value
    errors = [
        r
        for r in caplog.records
        if r.levelno == logging.ERROR and "验证会话启动失败" in r.getMessage()
    ]
    assert errors, "start failure must emit an ERROR record"
    message = errors[-1].getMessage()
    assert "account=acc_1" in message
    assert "error_type=RuntimeError" in message
    assert "SID=***" in message
    assert "secretvalue" not in message
    assert errors[-1].exc_info is not None


@pytest.mark.asyncio
async def test_describe_page_handles_closed_and_live_pages(tmp_path: Path) -> None:
    driver = CdpVerifyBrowserDriver(auth_file=tmp_path / "auth.json")
    closed = await driver.describe_page()
    assert closed["page_closed"] is True

    class _Page:
        url = _VERIFY_URL

        def is_closed(self) -> bool:
            return False

        async def evaluate(self, expression: str, **kwargs: Any) -> object:
            assert "window.location.href" in expression
            assert "verify device" in expression
            return {
                "url": _VERIFY_URL,
                "title": "Verify",
                "text": "hello",
                "verify_button": True,
                "qr_canvas": False,
            }

    driver._page = cast(Any, _Page())
    live = await driver.describe_page()
    assert live["url"] == _VERIFY_URL
    assert live["title"] == "Verify"
    assert live["verify_button"] is True


def test_safe_page_text_masks_email_and_tokens() -> None:
    preview = verify_module._safe_page_text(
        "Contact dev@example.com cookie SID=abcdef " + "x" * 400
    )
    assert "dev@example.com" not in preview
    assert "***@***" in preview
    assert "SID=***" in preview
    assert len(preview) <= verify_module._PAGE_TEXT_LOG_LIMIT


# ---------------------------------------------------------------------------
# verify-account observation: CDP network record + QR frame diagnostics
# ---------------------------------------------------------------------------


class _NetFakePage:
    """Fake CDP page exposing the three network hooks the driver subscribes to."""

    def __init__(self) -> None:
        self.request_cb: Any = None
        self.response_cb: Any = None
        self.failed_cb: Any = None
        self.unsubscribed: list[str] = []

    def on_request(self, callback: Any) -> Any:
        self.request_cb = callback
        return lambda: self.unsubscribed.append("request")

    def on_response(self, callback: Any) -> Any:
        self.response_cb = callback
        return lambda: self.unsubscribed.append("response")

    def on_loading_failed(self, callback: Any) -> Any:
        self.failed_cb = callback
        return lambda: self.unsubscribed.append("loading_failed")


def test_net_trace_records_only_whitelisted_fields() -> None:
    page = _NetFakePage()
    driver = CdpVerifyBrowserDriver(auth_file=Path("unused.json"))
    driver._register_net_trace(page)
    assert page.request_cb is not None and page.response_cb is not None
    assert page.failed_cb is not None
    assert len(driver._net_unsubscribers) == 3

    page.request_cb(
        {
            "request_id": "req-1",
            "url": "https://aistudio.google.com/verify-account?token=SUPERSECRET",
            "method": "POST",
            "headers": {"Cookie": "SID=secret-cookie", "Authorization": "Bearer x"},
            "post_data": "post-body-secret",
        }
    )
    page.response_cb(
        {
            "request_id": "req-1",
            "url": "https://aistudio.google.com/verify-account?token=SUPERSECRET",
            "status": 200,
            "headers": {"Set-Cookie": "leak=1"},
            "mime_type": "application/json; charset=utf-8",
        }
    )

    entries = list(driver._net_trace)
    assert len(entries) == 1
    entry = entries[0]
    assert {f.name for f in dataclass_fields(entry)} == {
        "ts",
        "method",
        "url_path",
        "status",
        "mime",
        "request_id",
        "failed",
        "body_preview",
    }
    assert entry.method == "POST"
    assert entry.url_path == "verify-account"
    assert "?" not in entry.url_path
    assert entry.status == 200
    assert entry.mime == "application/json"
    assert entry.request_id == "req-1"
    assert entry.failed == ""
    rendered = repr(entry)
    assert "secret-cookie" not in rendered
    assert "post-body-secret" not in rendered
    assert "SUPERSECRET" not in rendered
    assert "Bearer" not in rendered

    # Pages without network hooks (test doubles / future engines) must not raise.
    driver._register_net_trace(object())


def test_net_trace_masks_rpc_service_segment_but_keeps_method_name() -> None:
    host, path = verify_module._sanitize_url_for_log(
        "https://alkalimakersuite-pa.clients6.google.com/$rpc/"
        "google.internal.alkali.applications.makersuite.v1.MakerSuiteService/"
        "GetUserVerificationStatus?key=SECRETKEY"
    )
    assert host == "alkalimakersuite-pa.clients6.google.com"
    assert path == "rpc/MakerSuiteService/GetUserVerificationStatus"
    assert "SECRETKEY" not in path
    # QR short codes are masked, not logged in clear text.
    assert verify_module._sanitize_url_for_log("https://x/qr/ABCDEF123456")[1] == "qr/…"


@pytest.mark.asyncio
async def test_qr_timeout_dumps_net_trace(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="aistudio.verify-account")
    page = _NetFakePage()
    driver = CdpVerifyBrowserDriver(
        auth_file=Path("unused.json"), code_timeout_seconds=0.3
    )
    driver._register_net_trace(page)
    page.request_cb(
        {
            "request_id": "req-9",
            "url": "https://www.google.com/recaptcha/api2/bframe?k=abc",
            "method": "GET",
            "headers": {"Cookie": "SID=secret"},
            "post_data": "",
        }
    )

    qr_page = _BlankFramePage()
    with pytest.raises(VerifyChallengeTimeout):
        await driver._wait_for_qr(cast(Any, qr_page))

    messages = [record.getMessage() for record in caplog.records]
    dump = [m for m in messages if "网络记录 reason=qr_timeout" in m]
    assert dump, "qr_timeout must dump the network record before raising"
    assert "total=1" in dump[-1]
    assert "interesting=1" in dump[-1]
    assert any("recaptcha/api2/bframe" in m for m in messages)
    assert all("secret" not in m for m in messages)


class _FrameNavFakePage(_NetFakePage):
    """Fake page that also exposes the frame-navigation hook."""

    def __init__(self) -> None:
        super().__init__()
        self.frame_cb: Any = None

    def on_frame_navigated(self, callback: Any) -> Any:
        self.frame_cb = callback
        return lambda: self.unsubscribed.append("frame_navigated")


def _feed_interesting_requests(page: _NetFakePage, count: int) -> None:
    for index in range(1, count + 1):
        page.request_cb(
            {
                "request_id": f"req-{index}",
                "url": f"https://aistudio.google.com/verify-account?n={index}",
                "method": "GET",
            }
        )


def test_net_trace_dump_prints_tail_not_head(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="aistudio.verify-account")
    page = _NetFakePage()
    driver = CdpVerifyBrowserDriver(auth_file=Path("unused.json"))
    driver._register_net_trace(page)
    _feed_interesting_requests(page, 60)
    driver._dump_net_trace("tail_test")

    messages = [record.getMessage() for record in caplog.records]
    summary = [m for m in messages if "网络记录 reason=tail_test" in m]
    assert summary and "interesting=60" in summary[-1]
    joined = "\n".join(messages)
    assert "#60 " in joined, "the newest interesting entry must be printed"
    assert "…更早 20 条 interesting 未展示 #21…" in joined
    assert "#1 " not in joined, "the oldest entry must be dropped from the tail"


def test_net_trace_summary_flags_verify_user(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="aistudio.verify-account")
    driver = CdpVerifyBrowserDriver(auth_file=Path("unused.json"))

    with_user = _NetFakePage()
    driver._register_net_trace(with_user)
    with_user.request_cb(
        {
            "request_id": "req-v",
            "url": "https://alkalimakersuite-pa.clients6.google.com/$rpc/"
            "google.internal.alkali.applications.makersuite.v1.MakerSuiteService/"
            "VerifyUser",
            "method": "POST",
        }
    )
    driver._dump_net_trace("with_user")
    summary = [
        r.getMessage()
        for r in caplog.records
        if "网络记录 reason=with_user" in r.getMessage()
    ]
    assert summary and "verify_user=yes" in summary[-1]

    without_user = _NetFakePage()
    other_driver = CdpVerifyBrowserDriver(auth_file=Path("unused.json"))
    other_driver._register_net_trace(without_user)
    _feed_interesting_requests(without_user, 1)
    other_driver._dump_net_trace("without_user")
    summary = [
        r.getMessage()
        for r in caplog.records
        if "网络记录 reason=without_user" in r.getMessage()
    ]
    assert summary and "verify_user=no" in summary[-1]


def test_net_trace_summary_counts_failed_and_navigations(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="aistudio.verify-account")
    page = _FrameNavFakePage()
    driver = CdpVerifyBrowserDriver(auth_file=Path("unused.json"))
    driver._register_net_trace(page)
    driver._register_frame_nav_trace(page)
    assert page.frame_cb is not None
    assert driver._frame_nav_hook == "page"

    page.request_cb(
        {
            "request_id": "req-f",
            "url": "https://aistudio.google.com/verify-account?x=1",
            "method": "GET",
        }
    )
    page.failed_cb({"requestId": "req-f", "errorText": "net::ERR_FAILED"})
    page.frame_cb({"url": "https://www.google.com/recaptcha/api2/bframe?k=1"})
    driver._dump_net_trace("counts")

    summary = [
        r.getMessage()
        for r in caplog.records
        if "网络记录 reason=counts" in r.getMessage()
    ]
    assert summary
    assert "failed=1" in summary[-1]
    assert "navigations=1" in summary[-1]


def test_frame_navigation_whitelist_and_log_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="aistudio.verify-account")
    page = _FrameNavFakePage()
    driver = CdpVerifyBrowserDriver(auth_file=Path("unused.json"))
    driver._register_frame_nav_trace(page)
    assert page.frame_cb is not None

    page.frame_cb({"url": "https://example.com/not-whitelisted"})
    assert len(driver._frame_navs) == 0

    page.frame_cb({"url": "https://www.google.com/recaptcha/api2/bframe?k=abc"})
    assert len(driver._frame_navs) == 1
    ts, display = driver._frame_navs[0]
    assert isinstance(ts, float)
    assert "bframe" in display
    messages = [r.getMessage() for r in caplog.records]
    assert any("[Verify] 帧导航" in m for m in messages)


class _NetDumpDriver(FakeDriver):
    """FakeDriver that records ``still_pending`` dump invocations."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.dump_reasons: list[str] = []

    def _dump_net_trace(self, reason: str) -> None:
        self.dump_reasons.append(reason)


@pytest.mark.asyncio
async def test_still_pending_dump_is_throttled(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1", verify_required=True)
    driver = _NetDumpDriver(
        poll_results=[PageProbe(_VERIFY_URL, False)], confirm_status=1
    )
    service = VerifyAccountService(store, None, driver_factory=lambda _s, _a: driver)
    try:
        started = await service.start("acc_1")
        session_id = str(started["session_id"])

        await service.status("acc_1", session_id, force=True)
        await service.status("acc_1", session_id, force=True)
        assert driver.dump_reasons.count("still_pending") == 1

        session = service.session
        assert session is not None
        session.last_net_dump_at = time.time() - 11.0
        await service.status("acc_1", session_id, force=True)
        assert driver.dump_reasons.count("still_pending") == 2
    finally:
        await service.close()


class _BlankFramePage:
    """Frame tree where the QR lives in a frame whose URL is still about:blank."""

    def __init__(self) -> None:
        self.worlds = 0

        owner = self

        class _Cdp:
            async def send(
                self, method: str, params: dict[str, object] | None = None
            ) -> dict[str, object]:
                if method == "Page.getFrameTree":
                    return {
                        "frameTree": {
                            "frame": {"id": "main", "url": _VERIFY_URL},
                            "childFrames": [
                                {"frame": {"id": "bframe_blank", "url": "about:blank"}}
                            ],
                        }
                    }
                if method == "Page.createIsolatedWorld":
                    owner.worlds += 1
                    return {"executionContextId": 200 + owner.worlds}
                return {"result": {"value": {"hasCanvas": True, "dataImageCount": 1}}}

        self.cdp = _Cdp()

    async def evaluate(self, expression: str, *args: Any, **kwargs: Any) -> object:
        if "window.location.href" in expression:
            return _VERIFY_URL
        return None


@pytest.mark.asyncio
async def test_blank_url_frame_is_logged_not_silently_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="aistudio.verify-account")
    page = _BlankFramePage()
    driver = CdpVerifyBrowserDriver(
        auth_file=Path("unused.json"), code_timeout_seconds=1.0
    )
    challenge = await driver._read_bframe_qr(cast(Any, page))
    assert challenge is None

    messages = [record.getMessage() for record in caplog.records]
    blank = [m for m in messages if "filter_reason=blank_url" in m]
    assert blank, "an about:blank candidate frame must be logged as filtered"
    assert any("frame_id=bframe_blank" in m for m in blank)
    assert any("qr_image_only=true" in m for m in messages)
    # The tentative read is allowed once per frame per session only.
    assert page.worlds == 1


def test_read_qr_js_extension_keeps_short_code_extraction() -> None:
    js = verify_module._READ_QR_JS
    for field in ("canvasAriaLabel", "imgQrAlts", "dataImageCount", "hasCanvas"):
        assert field in js
    # Only the count of data: images is returned, never the payload itself.
    assert 'img[src^="data:image"]' in js

    legacy_payload = {
        "href": "https://recaptcha.net/qr/ABCDEF123456",
        "title": "",
        "fallback": "",
        "hasCanvas": True,
    }
    extended_payload = {
        **legacy_payload,
        "canvasAriaLabel": "",
        "imgQrAlts": [],
        "dataImageCount": 0,
    }

    def extract(payload: dict[str, object]) -> str | None:
        return verify_module._extract_short_code(
            tuple(value for _, value in verify_module._qr_code_sources(payload))
        )

    assert extract(legacy_payload) == extract(extended_payload) == "ABCDEF123456"
    # New read-only sources can also carry the code, searched after the legacy
    # href/title/fallback order.
    alt_payload = {
        "href": "",
        "title": "",
        "fallback": "",
        "canvasAriaLabel": "",
        "imgQrAlts": ["QR code https://recaptcha.net/qr/ZYXWVU654321"],
        "dataImageCount": 3,
    }
    assert extract(alt_payload) == "ZYXWVU654321"
    # A data: image payload with no textual code must still yield ``None``.
    assert (
        extract({"href": "", "title": "", "fallback": "", "dataImageCount": 4}) is None
    )


# ---------------------------------------------------------------------------
# verify-account observation: bounded response-body capture (VerifyUser 403)
# ---------------------------------------------------------------------------

_VERIFY_USER_URL = (
    "https://alkalimakersuite-pa.clients6.google.com/$rpc/"
    "google.internal.alkali.applications.makersuite.v1.MakerSuiteService/VerifyUser"
)
_USERVERIFY_URL = "https://www.google.com/recaptcha/enterprise/userverify"
_CLR_URL = "https://www.google.com/recaptcha/enterprise/clr"
_GENERATE_ACCESS_TOKEN_URL = (
    "https://alkalimakersuite-pa.clients6.google.com/$rpc/"
    "google.internal.alkali.applications.makersuite.v1.MakerSuiteService/"
    "GenerateAccessToken"
)
_WAA_CREATE_URL = "https://waa-pa.clients6.google.com/waa/Waa/Create"


class _BodyFakePage(_NetFakePage):
    """Fake page exposing both net hooks and a response-body getter."""

    def __init__(
        self,
        body: str = "",
        *,
        base64_encoded: bool = False,
        shape: str = "str",
    ) -> None:
        super().__init__()
        self.body = body
        self.base64_encoded = base64_encoded
        self.shape = shape
        self.body_calls = 0
        self.body_request_ids: list[str] = []

    async def get_response_body(self, request_id: str) -> object:
        self.body_request_ids.append(request_id)
        self.body_calls += 1
        if self.shape == "tuple":
            return (self.body, self.base64_encoded)
        if self.shape == "dict":
            return {"body": self.body, "base64Encoded": self.base64_encoded}
        return self.body


def _feed_net_pair(
    page: _NetFakePage,
    request_id: str,
    url: str,
    *,
    status: int = 403,
    mime: str = "application/json",
) -> None:
    page.request_cb({"request_id": request_id, "url": url, "method": "POST"})
    page.response_cb(
        {
            "request_id": request_id,
            "url": url,
            "status": status,
            "headers": {},
            "mime_type": mime,
        }
    )


async def _flush_net_bodies(driver: CdpVerifyBrowserDriver) -> None:
    for _ in range(3):
        tasks = list(driver._net_body_tasks)
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_net_body_preview_is_recorded_and_dumped_as_indented_subline(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="aistudio.verify-account")
    page = _BodyFakePage('{"x":1}')
    driver = CdpVerifyBrowserDriver(auth_file=Path("unused.json"))
    driver._page = cast(Any, page)
    driver._register_net_trace(page)

    _feed_net_pair(page, "req-a", _VERIFY_USER_URL)
    await _flush_net_bodies(driver)

    entries = list(driver._net_trace)
    assert len(entries) == 1
    assert entries[0].body_preview == '{"x":1}'
    assert page.body_request_ids == ["req-a"]

    driver._dump_net_trace("body_test")
    messages = [record.getMessage() for record in caplog.records]
    assert any('body={"x":1}' in message for message in messages)
    assert any(message.startswith("      body=") for message in messages)
    assert any("[Verify] VerifyUser 响应 status=403" in message for message in messages)


@pytest.mark.asyncio
async def test_net_body_preview_decodes_base64_tuple_and_dict() -> None:
    encoded = base64.b64encode(b'{"ok":true}').decode("ascii")
    for shape in ("tuple", "dict"):
        page = _BodyFakePage(encoded, base64_encoded=True, shape=shape)
        driver = CdpVerifyBrowserDriver(auth_file=Path("unused.json"))
        driver._page = cast(Any, page)
        driver._register_net_trace(page)
        _feed_net_pair(page, "req-b", _USERVERIFY_URL, status=200)
        await _flush_net_bodies(driver)
        assert next(iter(driver._net_trace)).body_preview == '{"ok":true}', shape


@pytest.mark.asyncio
async def test_net_body_preview_truncates_to_budget() -> None:
    page = _BodyFakePage("a" * 500)
    driver = CdpVerifyBrowserDriver(auth_file=Path("unused.json"))
    driver._page = cast(Any, page)
    driver._register_net_trace(page)

    _feed_net_pair(page, "req-c", _USERVERIFY_URL, status=200)
    await _flush_net_bodies(driver)

    preview = next(iter(driver._net_trace)).body_preview
    assert preview == "a" * verify_module.NET_BODY_PREVIEW_LIMIT + "…"
    assert len(preview) <= verify_module.NET_BODY_PREVIEW_LIMIT + 1


@pytest.mark.asyncio
async def test_net_body_preview_masks_long_tokens_and_cookie() -> None:
    long_token = "A" * 40
    body = '{"token":"' + long_token + '","note":"Cookie: SID=abcdefghijklmnop"}'
    page = _BodyFakePage(body)
    driver = CdpVerifyBrowserDriver(auth_file=Path("unused.json"))
    driver._page = cast(Any, page)
    driver._register_net_trace(page)

    _feed_net_pair(page, "req-d", _USERVERIFY_URL, status=200)
    await _flush_net_bodies(driver)

    preview = next(iter(driver._net_trace)).body_preview
    assert long_token not in preview
    assert "SID=abcdefghijklmnop" not in preview


@pytest.mark.asyncio
async def test_net_body_binary_records_only_length(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="aistudio.verify-account")
    content = "PDF-1.4-binary-content"
    page = _BodyFakePage(content)
    driver = CdpVerifyBrowserDriver(auth_file=Path("unused.json"))
    driver._page = cast(Any, page)
    driver._register_net_trace(page)

    _feed_net_pair(page, "req-e", _CLR_URL, status=200, mime="application/binary")
    await _flush_net_bodies(driver)
    driver._dump_net_trace("binary_test")

    entry = next(iter(driver._net_trace))
    assert entry.body_preview == f"len={len(content)}"
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "body=len=" in joined
    assert content not in joined


@pytest.mark.asyncio
async def test_net_body_capture_is_capped_per_session() -> None:
    page = _BodyFakePage('{"ok":1}')
    driver = CdpVerifyBrowserDriver(auth_file=Path("unused.json"))
    driver._page = cast(Any, page)
    driver._register_net_trace(page)

    for index in range(verify_module.NET_BODY_MAX_PER_SESSION + 1):
        _feed_net_pair(page, f"req-{index}", _USERVERIFY_URL, status=200)
    await _flush_net_bodies(driver)

    assert driver._net_body_fetches == verify_module.NET_BODY_MAX_PER_SESSION
    assert page.body_calls == verify_module.NET_BODY_MAX_PER_SESSION
    assert len(driver._net_bodies) <= verify_module.NET_BODY_MAX_PER_SESSION


@pytest.mark.asyncio
async def test_net_trace_summary_includes_verify_user_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="aistudio.verify-account")
    page = _BodyFakePage('{"error":"denied"}')
    driver = CdpVerifyBrowserDriver(auth_file=Path("unused.json"))
    driver._page = cast(Any, page)
    driver._register_net_trace(page)

    _feed_net_pair(page, "req-g", _VERIFY_USER_URL, status=403)
    await _flush_net_bodies(driver)
    driver._dump_net_trace("summary_status")

    summary = [
        record.getMessage()
        for record in caplog.records
        if "网络记录 reason=summary_status" in record.getMessage()
    ]
    assert summary and "verify_user=yes(403)" in summary[-1]


@pytest.mark.asyncio
async def test_generate_access_token_401_body_is_captured_with_immediate_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="aistudio.verify-account")
    assert verify_module._net_body_url_interesting("GenerateAccessToken") is True

    page = _BodyFakePage('[7,"..."]', shape="tuple")
    driver = CdpVerifyBrowserDriver(auth_file=Path("unused.json"))
    driver._page = cast(Any, page)
    driver._register_net_trace(page)

    _feed_net_pair(page, "req-gat", _GENERATE_ACCESS_TOKEN_URL, status=401)
    await _flush_net_bodies(driver)

    # No ``_dump_net_trace`` call: the line must come from the capture itself.
    immediate = [
        record.getMessage()
        for record in caplog.records
        if "GenerateAccessToken 响应" in record.getMessage()
    ]
    assert immediate, "GenerateAccessToken body must be logged on arrival"
    assert "status=401" in immediate[-1]
    assert "body=" in immediate[-1]
    assert '[7,"..."]' in immediate[-1]
    assert next(iter(driver._net_trace)).body_preview == '[7,"..."]'


@pytest.mark.asyncio
async def test_waa_create_is_whitelisted_and_records_body_preview() -> None:
    assert verify_module._net_body_url_interesting("Waa/Create") is True

    page = _BodyFakePage('{"ok":1}')
    driver = CdpVerifyBrowserDriver(auth_file=Path("unused.json"))
    driver._page = cast(Any, page)
    driver._register_net_trace(page)

    _feed_net_pair(page, "req-waa", _WAA_CREATE_URL, status=200)
    await _flush_net_bodies(driver)

    entry = next(iter(driver._net_trace))
    assert entry.url_path == "waa/Waa/Create"
    assert entry.body_preview == '{"ok":1}'
    assert page.body_request_ids == ["req-waa"]


class _VerifyStartPage:
    """Minimal fake CDP page for ``CdpVerifyBrowserDriver.start`` (no network)."""

    def __init__(
        self,
        *,
        calls: list[str],
        cookies: list[dict[str, object]] | None = None,
        url: str = _VERIFY_URL,
    ) -> None:
        self.calls = calls
        self.cookies = list(cookies or [])
        self.url = url
        self.goto_calls: list[str] = []
        self.closed = False

    async def goto(self, url: str, **_kwargs: Any) -> None:
        target = str(url)
        self.goto_calls.append(target)
        self.calls.append(f"goto:{target}")

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    async def evaluate(self, expression: str, *_args: Any, **_kwargs: Any) -> object:
        if "window.location.href" in expression:
            return self.url
        return None

    async def get_cookies(self) -> list[dict[str, object]]:
        return self.cookies

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True


def _drive_verify_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    page: _VerifyStartPage,
    bootstrap_error: Exception | None = None,
) -> CdpVerifyBrowserDriver:
    """Wire ``driver.start`` to fakes so no real Chromium/CDP runs."""
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    binary = tmp_path / "chrome.exe"
    binary.write_text("", encoding="utf-8")
    driver = CdpVerifyBrowserDriver(auth_file=auth_file, chrome_path=str(binary))

    async def fake_exec(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(pid=4321, returncode=None)

    async def fake_load(_page: Any, _auth: str) -> bool:
        page.calls.append("load_cookies")
        return True

    async def fake_bootstrap(_page: Any) -> None:
        page.calls.append("bootstrap")
        if bootstrap_error is not None:
            raise bootstrap_error

    async def fake_connect_page(**_kwargs: Any) -> Any:
        return page

    async def fake_wait_for_qr(_self: Any, _page: Any) -> QrChallenge:
        return _QR

    async def fake_close(_self: Any) -> None:
        path = driver._user_data_dir
        if path:
            with contextlib.suppress(OSError):
                Path(path).rmdir()
            driver._user_data_dir = None

    monkeypatch.setattr(verify_module.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(
        verify_module,
        "CDPClient",
        lambda _port: SimpleNamespace(connect_page=fake_connect_page),
    )
    monkeypatch.setattr(verify_module, "load_cookies_to_page", fake_load)
    monkeypatch.setattr(verify_module, "bootstrap_google_session", fake_bootstrap)
    monkeypatch.setattr(CdpVerifyBrowserDriver, "_wait_for_qr", fake_wait_for_qr)
    monkeypatch.setattr(CdpVerifyBrowserDriver, "close", fake_close)
    return driver


@pytest.mark.asyncio
async def test_start_bootstraps_session_between_cookies_and_verify_navigation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    page = _VerifyStartPage(calls=calls)
    driver = _drive_verify_start(monkeypatch, tmp_path, page=page)

    challenge = await driver.start()

    assert challenge == _QR
    # Exactly one bootstrap, strictly after the Cookie load and strictly before
    # the verify-account navigation.
    assert calls == ["load_cookies", "bootstrap", f"goto:{_VERIFY_URL}"]


@pytest.mark.asyncio
async def test_start_survives_bootstrap_failure_and_still_fetches_qr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="aistudio.verify-account")
    calls: list[str] = []
    page = _VerifyStartPage(calls=calls)
    driver = _drive_verify_start(
        monkeypatch,
        tmp_path,
        page=page,
        bootstrap_error=RuntimeError("bootstrap stayed on login flow"),
    )

    challenge = await driver.start()

    assert challenge == _QR
    assert page.goto_calls == [_VERIFY_URL]
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and "会话引导失败" in record.getMessage()
    ]
    assert warnings and "RuntimeError" in warnings[-1]


@pytest.mark.asyncio
async def test_start_logs_cookie_names_without_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="aistudio.verify-account")
    secret_osid = "SECRET-OSID-VALUE-111"
    secret_sidcc = "SECRET-SIDCC-VALUE-222"
    secret_sid = "SECRET-SID-VALUE-333"
    calls: list[str] = []
    page = _VerifyStartPage(
        calls=calls,
        cookies=[
            {"name": "OSID", "value": secret_osid},
            {"name": "SIDCC", "value": secret_sidcc},
            {"name": "SID", "value": secret_sid},
        ],
    )
    driver = _drive_verify_start(monkeypatch, tmp_path, page=page)

    await driver.start()

    line = next(
        record.getMessage()
        for record in caplog.records
        if "[Verify] 会话引导后 Cookie" in record.getMessage()
    )
    assert "total=3" in line
    assert "osid=yes" in line
    assert "sidcc=yes" in line
    assert "secure_osid=no" in line
    assert "gaps=no" in line
    for secret in (secret_osid, secret_sidcc, secret_sid):
        assert secret not in line
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_start_logs_cookie_names_at_both_stages_in_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """E: the post-injection census runs before the post-bootstrap one."""
    caplog.set_level(logging.INFO, logger="aistudio.verify-account")
    page = _VerifyStartPage(
        calls=[],
        cookies=[
            {"name": "SID", "value": "SECRET-SID"},
            {"name": "OSID", "value": "SECRET-OSID"},
        ],
    )
    driver = _drive_verify_start(monkeypatch, tmp_path, page=page)

    await driver.start()

    lines = [
        record.getMessage()
        for record in caplog.records
        if "[Verify]" in record.getMessage() and "Cookie: total=" in record.getMessage()
    ]
    injection = next(line for line in lines if "[Verify] Cookie 注入后 Cookie:" in line)
    bootstrap = next(line for line in lines if "[Verify] 会话引导后 Cookie:" in line)
    assert "sid=yes" in injection
    assert "osid=yes" in injection
    assert "sid=yes" in bootstrap
    assert lines.index(injection) < lines.index(bootstrap)
    assert "SECRET-SID" not in caplog.text
    assert "SECRET-OSID" not in caplog.text
