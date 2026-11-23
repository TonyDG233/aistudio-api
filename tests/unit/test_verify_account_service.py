"""Unit tests for the verify-account session service (no real browser/network)."""

from __future__ import annotations

import asyncio
import json
import platform
import tempfile
import time
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
