"""verify-account 扫码验证会话服务（后端受控浏览器取码 + 状态确认）。

设计边界：

* 全局同一时间只允许一个验证会话；同账号重复 ``start`` 返回现有会话。
* 状态机：``starting -> pending -> passed | failed | expired | error |
  region_blocked | session_invalid``；显式取消进入 ``cancelled``。
* 浏览器进程/临时目录必须在 ``finally`` 清理；任何路径都不记录 Cookie、
  邮箱或二维码短码。
* 通过判定先看页面状态（离开 verify-account 或成功文案），再用
  ``GetUserVerificationStatus`` 做二次确认，只有返回 ``2`` 才置 ``passed``。
  确认由 Python 侧直连 RPC（httpx + SAPISIDHASH + Firefox 152 UA），与验证
  浏览器使用同一出口（默认直连，配置 ``AISTUDIO_VERIFY_PROXY`` 时走该代理），
  避免依赖 ``document.cookie``（SAPISID 可能 HttpOnly）与页面 CORS 预检。
* 只允许由 API 轮询驱动状态推进；服务内部除一次性过期/清理定时器外没有
  后台轮询循环。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import platform
import re
import secrets
import shutil
import signal
import socket
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import httpx

from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.account.cookie_parser import (
    DEFAULT_ACCEPT_LANGUAGE,
    DEFAULT_BROWSER_USER_AGENT,
    calculate_sapisid_hash,
    cookies_for_google_rpc,
)
from aistudio_api.infrastructure.browser.browser_engine import (
    build_chromium_args,
    find_chromium_executable,
)
from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage
from aistudio_api.infrastructure.gateway.public_web_constants import (
    AI_STUDIO_HOME_URL,
    AI_STUDIO_ORIGIN,
    EXT_BIN,
    EXT_BIN_HEADER,
    GRPC_WEB_USER_AGENT,
    WEB_API_KEY,
    rpc_url,
)
from aistudio_api.infrastructure.gateway.session_auth import (
    is_aistudio_page_url,
    is_login_page_url,
    load_cookies_to_page,
)
from aistudio_api.infrastructure.utils.logger import get_logger

if TYPE_CHECKING:
    from aistudio_api.application.account_rotator import AccountRotator

log = get_logger("verify-account")

VERIFY_ACCOUNT_URL = "https://aistudio.google.com/verify-account"
DEFAULT_PROXY_ENV = "AISTUDIO_VERIFY_PROXY"
CHROME_PATH_ENV = "AISTUDIO_VERIFY_CHROME_PATH"
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)

DEFAULT_TTL_SECONDS = 180.0
DEFAULT_CODE_TIMEOUT_SECONDS = 20.0
PASSED_RETENTION_SECONDS = 10.0
TERMINAL_RETENTION_SECONDS = 10.0
CLICK_RETRY_INTERVAL_SECONDS = 2.0
CONFIRM_RPC_TIMEOUT_SECONDS = 8.0
# ``CdpVerifyBrowserDriver.close`` is bounded at ~1.5s+1.0s (process) + 2s
# (Page.close) + 2s (client) = 6.5s worst case; keep the outer guard above it so
# a healthy teardown is never cancelled half-way.
DRIVER_CLOSE_TIMEOUT_SECONDS = 7.0
PAGE_CLOSE_TIMEOUT_SECONDS = 2.0
CLIENT_CLOSE_TIMEOUT_SECONDS = 2.0

_SHORT_CODE_RE = re.compile(r"recaptcha\.net/qr/([A-Za-z0-9_-]{6,32})")
_VERIFY_RPC_URL = rpc_url("GetUserVerificationStatus")
_SUCCESS_MARKERS = (
    "your account was verified",
    "verified. you can now continue using ai studio",
    "no verification needed",
    "account verified",
)
_FAILURE_MARKERS = (
    "we couldn't verify your account",
    "couldn't verify your account",
    "try a different mobile device",
    "verification failed",
    "unable to verify your account",
)

# Conservative HTTP classification for the direct GetUserVerificationStatus
# RPC.  A bare 403/401 is ambiguous (permission/quota/time-skew), so only bodies
# carrying these upstream markers map to a terminal session state; every other
# non-200 stays pending and is retried by the next status call.
_REGION_403_MARKERS = (
    "available-regions",
    "region not supported",
    "not available in your country",
)
_SESSION_INVALID_401_MARKERS = (
    "session_cookie_invalid",
    "credentials_missing",
    "unauthenticated",
    "invalid credentials",
    "not authenticated",
)

# The page component disables this button while reCAPTCHA state is ``loading``;
# a click on a disabled element does not dispatch, so the previous version
# silently burned its single click and then timed out waiting for the bframe.
_VERIFY_BUTTON_USABLE_PREDICATE = (
    "!el.disabled && el.getAttribute('aria-disabled') !== 'true'"
)


def _verify_button_usable(*, disabled: bool, aria_disabled: str | None) -> bool:
    """Python mirror of the in-page predicate (see ``_VERIFY_BUTTON_USABLE_PREDICATE``).

    Disabled nodes must never be clicked; this pure helper makes that rule
    directly unit-testable without a real DOM.
    """
    return not disabled and aria_disabled != "true"


_CLICK_VERIFY_DEVICE_JS = """
(() => {
  const nodes = Array.from(
    document.querySelectorAll('button, a, [role="button"]')
  );
  const usable = (el) => __USABLE__;
  const preferred = nodes.find(
    (el) => usable(el) && el.hasAttribute('data-test-verify-button')
  );
  const byText = nodes.find((el) => {
    const text = (el.textContent || '').trim().toLowerCase();
    return usable(el) && text.includes('verify device');
  });
  const match = preferred || byText;
  if (!match) return false;
  match.click();
  return true;
})()
""".replace("__USABLE__", _VERIFY_BUTTON_USABLE_PREDICATE)

_READ_QR_JS = """
(() => {
  const button = document.getElementById('rc-mobile-verification-button');
  const canvas = document.querySelector(
    'canvas.recaptchaJavascriptChallengeQrCanvas'
  );
  const fallback = document.querySelector(
    '.recaptchaJavascriptChallengeQrFallbackText'
  );
  return {
    href: button && button.href ? String(button.href) : '',
    title: canvas && canvas.title ? String(canvas.title) : '',
    fallback: fallback && fallback.textContent ? String(fallback.textContent) : '',
    hasCanvas: !!canvas,
  };
})()
"""

_PAGE_SNAPSHOT_JS = """
(() => {
  const body = document.body;
  const text = body && body.innerText ? String(body.innerText) : '';
  return {
    url: String(window.location.href || ''),
    text: text.slice(0, 20000),
  };
})()
"""


class VerifySessionState(StrEnum):
    STARTING = "starting"
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    EXPIRED = "expired"
    ERROR = "error"
    REGION_BLOCKED = "region_blocked"
    SESSION_INVALID = "session_invalid"
    CANCELLED = "cancelled"


_ACTIVE_STATES = frozenset({VerifySessionState.STARTING, VerifySessionState.PENDING})
_TERMINAL_STATES = frozenset(
    {
        VerifySessionState.PASSED,
        VerifySessionState.FAILED,
        VerifySessionState.EXPIRED,
        VerifySessionState.ERROR,
        VerifySessionState.REGION_BLOCKED,
        VerifySessionState.SESSION_INVALID,
        VerifySessionState.CANCELLED,
    }
)


class VerifyAccountError(RuntimeError):
    """Base class for verify-account session failures."""


class VerifyAccountNotFound(VerifyAccountError):
    def __init__(self, account_id: str) -> None:
        super().__init__(f"账号不存在: {account_id}")
        self.account_id = account_id


class VerifySessionConflict(VerifyAccountError):
    def __init__(self, account_id: str) -> None:
        super().__init__("已有其它账号的验证会话正在进行")
        self.account_id = account_id


class VerifySessionNotFound(VerifyAccountError):
    def __init__(self) -> None:
        super().__init__("验证会话不存在或已结束")


class VerifyBrowserUnavailable(VerifyAccountError):
    """The verification browser cannot be launched on this host."""


class VerifySessionInvalid(VerifyAccountError):
    """Navigation landed on the Google login page."""


class VerifyRegionBlocked(VerifyAccountError):
    """Navigation landed on the available-regions page."""


class VerifyChallengeTimeout(VerifyAccountError):
    """No reCAPTCHA bframe / QR canvas appeared before the deadline."""


@dataclass(frozen=True)
class QrChallenge:
    qr_url: str
    short_code: str


@dataclass(frozen=True)
class PageProbe:
    page_url: str
    success: bool
    failed: bool = False


class VerifyBrowserSession(Protocol):
    async def start(self) -> QrChallenge: ...

    async def poll(self) -> PageProbe: ...

    async def confirm_verified(self, auth_user: str) -> int | None: ...

    async def close(self) -> None: ...


VerifyDriverFactory = Callable[["VerifySession", Path], VerifyBrowserSession]


@dataclass
class VerifySession:
    session_id: str
    account_id: str
    auth_user: str
    state: VerifySessionState
    created_at: float
    expires_at: float
    qr_url: str | None = None
    short_code: str | None = None
    message: str | None = None
    finished_at: float | None = None
    cleanup_at: float | None = None

    def to_public(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "state": self.state.value,
            "qr_url": self.qr_url,
            "short_code": self.short_code,
            "expires_at": _iso(self.expires_at),
            "message": self.message,
        }


class VerifyAccountController(Protocol):
    async def start(self, account_id: str) -> dict[str, object]: ...

    async def status(
        self,
        account_id: str,
        session_id: str,
        *,
        simulate: str | None = None,
    ) -> dict[str, object]: ...

    async def cancel(self, account_id: str) -> dict[str, object]: ...


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def _extract_short_code(values: tuple[str, ...]) -> str | None:
    for value in values:
        match = _SHORT_CODE_RE.search(value)
        if match:
            return match.group(1)
    return None


def _iter_frame_tree(node: object) -> Iterator[dict[str, object]]:
    if not isinstance(node, dict):
        return
    frame = node.get("frame")
    if isinstance(frame, dict):
        yield frame
    children = node.get("childFrames")
    if isinstance(children, list):
        for child in children:
            yield from _iter_frame_tree(child)


def _is_success_page(url: str, text: str) -> bool:
    if "available-regions" in url or is_login_page_url(url):
        return False
    if "verify-account" not in url and is_aistudio_page_url(url):
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in _SUCCESS_MARKERS)


def _is_failure_page(url: str, text: str) -> bool:
    if _is_success_page(url, text):
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _FAILURE_MARKERS)


def _body_has_marker(body: str, markers: tuple[str, ...]) -> bool:
    lowered = body.lower()
    return any(marker in lowered for marker in markers)


class VerifyAccountService:
    """In-process single-session verify-account coordinator."""

    def __init__(
        self,
        account_store: AccountStore,
        rotator: AccountRotator | None,
        *,
        driver_factory: VerifyDriverFactory | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        code_timeout_seconds: float = DEFAULT_CODE_TIMEOUT_SECONDS,
        passed_retention_seconds: float = PASSED_RETENTION_SECONDS,
        terminal_retention_seconds: float = TERMINAL_RETENTION_SECONDS,
    ) -> None:
        self._store = account_store
        self._rotator = rotator
        self._driver_factory = driver_factory or _default_driver_factory
        self._ttl = max(1.0, ttl_seconds)
        self._code_timeout = max(0.1, code_timeout_seconds)
        self._passed_retention = max(0.0, passed_retention_seconds)
        self._terminal_retention = max(0.0, terminal_retention_seconds)
        self._lock = asyncio.Lock()
        self._session: VerifySession | None = None
        self._driver: VerifyBrowserSession | None = None
        self._cancel_event: asyncio.Event | None = None
        self._session_lock: asyncio.Lock | None = None
        self._timer_task: asyncio.Task[None] | None = None
        self._cleanup_tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    @property
    def session(self) -> VerifySession | None:
        """Current session snapshot (read-only; mainly for diagnostics/tests)."""
        return self._session

    async def start(self, account_id: str) -> dict[str, object]:
        """Create the session placeholder, then launch the browser outside the lock.

        The global lock only guards session bookkeeping and never awaits the
        slow browser launch / QR fetch.  ``cancel``/``status``/``close`` return
        promptly even while ``driver.start()`` is still running, and cancelling
        while ``starting`` aborts the launch so this call returns ``cancelled``.
        """
        session: VerifySession
        driver: VerifyBrowserSession
        cancel_event: asyncio.Event
        async with self._lock:
            await self._cleanup_due_locked()
            existing = self._session
            if existing is not None:
                reusable = existing.account_id == account_id and (
                    existing.state in _ACTIVE_STATES
                    or existing.state is VerifySessionState.PASSED
                )
                if reusable:
                    log.info(
                        "验证会话复用: session=%s account=%s state=%s",
                        existing.session_id,
                        existing.account_id,
                        existing.state.value,
                    )
                    return existing.to_public()
                if existing.state in _ACTIVE_STATES:
                    raise VerifySessionConflict(existing.account_id)
                await self._dispose_session_locked()

            account = self._store.get_account(account_id)
            if account is None:
                raise VerifyAccountNotFound(account_id)

            now = time.time()
            session = VerifySession(
                session_id=f"verify_{secrets.token_hex(8)}",
                account_id=account.id,
                auth_user=str(getattr(account, "auth_user", "0") or "0"),
                state=VerifySessionState.STARTING,
                created_at=now,
                expires_at=now + self._ttl,
            )
            self._session = session
            self._closed = False
            cancel_event = asyncio.Event()
            self._cancel_event = cancel_event
            self._session_lock = asyncio.Lock()

            auth_file = self._store.get_auth_path_optional(account_id)
            if auth_file is None or not auth_file.is_file():
                self._finish_locked(
                    session, VerifySessionState.ERROR, "credentials_missing"
                )
                return session.to_public()

            try:
                driver = self._driver_factory(session, auth_file)
            except VerifyBrowserUnavailable:
                await self._dispose_session_locked()
                raise
            except Exception as exc:
                log.warning(
                    "验证会话驱动创建失败: session=%s account=%s error=%s",
                    session.session_id,
                    session.account_id,
                    type(exc).__name__,
                )
                self._finish_locked(session, VerifySessionState.ERROR, "browser_error")
                return session.to_public()
            self._driver = driver

        start_task = asyncio.create_task(driver.start())
        cancel_wait = asyncio.create_task(cancel_event.wait())
        start_done = False
        cancel_fired = False
        try:
            done, _pending = await asyncio.wait(
                {start_task, cancel_wait},
                timeout=self._code_timeout + 50.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            start_done = start_task in done
            cancel_fired = cancel_wait in done or cancel_event.is_set()
        except asyncio.CancelledError:
            # The caller (e.g. a disconnected ASGI handler) cancelled this
            # coroutine: never leave the placeholder stuck in STARTING.
            start_task.cancel()
            with contextlib.suppress(BaseException):
                await start_task
            with contextlib.suppress(BaseException):
                await self._abort_start(session, driver)
            raise
        finally:
            cancel_wait.cancel()
            with contextlib.suppress(BaseException):
                await cancel_wait

        challenge: QrChallenge | None = None
        start_error: BaseException | None = None
        if cancel_fired:
            start_task.cancel()
            with contextlib.suppress(BaseException):
                await start_task
        elif start_done:
            try:
                challenge = start_task.result()
            except BaseException as exc:
                start_error = exc
        else:
            start_task.cancel()
            with contextlib.suppress(BaseException):
                await start_task
            start_error = VerifyChallengeTimeout("qr_timeout")

        async with self._lock:
            if (
                self._session is not session
                or session.state is not VerifySessionState.STARTING
            ):
                # Cancelled (or superseded) while the browser was launching.
                if self._driver is driver:
                    self._driver = None
                    self._schedule_driver_close(driver, session)
                return session.to_public()
            if cancel_event.is_set():
                self._finish_locked(session, VerifySessionState.CANCELLED, "cancelled")
                return session.to_public()
            if challenge is not None:
                session.qr_url = challenge.qr_url
                session.short_code = challenge.short_code
                session.state = VerifySessionState.PENDING
                log.info(
                    "验证会话就绪: session=%s account=%s ttl=%.0fs",
                    session.session_id,
                    session.account_id,
                    self._ttl,
                )
                self._schedule_timer_locked(session.expires_at, session.session_id)
                return session.to_public()

            if isinstance(start_error, VerifyBrowserUnavailable):
                await self._dispose_session_locked()
                raise start_error
            state = VerifySessionState.ERROR
            message = "browser_error"
            if isinstance(start_error, VerifySessionInvalid):
                state, message = VerifySessionState.SESSION_INVALID, "login_redirect"
            elif isinstance(start_error, VerifyRegionBlocked):
                state, message = VerifySessionState.REGION_BLOCKED, "available_regions"
            elif isinstance(start_error, (VerifyChallengeTimeout, TimeoutError)):
                state, message = VerifySessionState.ERROR, "qr_timeout"
            elif start_error is not None:
                log.warning(
                    "验证会话启动失败: session=%s account=%s error=%s",
                    session.session_id,
                    session.account_id,
                    type(start_error).__name__,
                )
            self._finish_locked(session, state, message)
            return session.to_public()

    async def _abort_start(
        self, session: VerifySession, driver: VerifyBrowserSession
    ) -> None:
        """Mark an interrupted ``start`` cancelled and release its driver."""
        async with self._lock:
            if self._session is not session:
                return
            if session.state is VerifySessionState.STARTING:
                self._finish_locked(session, VerifySessionState.CANCELLED, "cancelled")
                return
            if self._driver is driver:
                self._driver = None
                self._schedule_driver_close(driver, session)

    async def status(
        self,
        account_id: str,
        session_id: str,
        *,
        simulate: str | None = None,
    ) -> dict[str, object]:
        del simulate
        async with self._lock:
            await self._cleanup_due_locked()
            session = self._session
            if (
                session is None
                or session.session_id != session_id
                or session.account_id != account_id
            ):
                raise VerifySessionNotFound
            if session.state in _TERMINAL_STATES:
                return session.to_public()
            if session.state is VerifySessionState.STARTING:
                # Browser launch/QR fetch runs unlocked; status stays responsive
                # and must not poll a driver that has not navigated yet.
                return session.to_public()
            driver = self._driver
            cancel_event = self._cancel_event
            session_lock = self._session_lock
        if driver is None or cancel_event is None or session_lock is None:
            return session.to_public()

        # Per-session lock serialises poll/confirm without blocking cancel,
        # start for another account, or the global bookkeeping lock.
        async with session_lock:
            if cancel_event.is_set() or session.state in _TERMINAL_STATES:
                return session.to_public()
            await self._refresh(session, driver, cancel_event)
        return session.to_public()

    async def cancel(self, account_id: str) -> dict[str, object]:
        """Cancel the session and return immediately; teardown runs in background."""
        result: dict[str, object] = {"state": VerifySessionState.CANCELLED.value}
        driver: VerifyBrowserSession | None = None
        async with self._lock:
            await self._cleanup_due_locked()
            session = self._session
            if session is None or session.account_id != account_id:
                return result
            if session.state in _ACTIVE_STATES:
                cancel_event = self._cancel_event
                if cancel_event is not None:
                    cancel_event.set()
                driver = self._driver
                self._driver = None
                session.state = VerifySessionState.CANCELLED
                session.message = "cancelled"
                session.finished_at = time.time()
                session.cleanup_at = session.finished_at + self._terminal_retention
                log.info(
                    "验证会话取消: session=%s account=%s",
                    session.session_id,
                    session.account_id,
                )
                self._schedule_timer_locked(session.cleanup_at, session.session_id)
            result = {"state": session.state.value}
        # Driver teardown never blocks the HTTP response: a slow process kill
        # must not make cancel wait, and the bounded task still frees resources.
        if driver is not None:
            self._schedule_driver_close(driver, session)
        return result

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            cancel_event = self._cancel_event
            if cancel_event is not None:
                cancel_event.set()
            await self._dispose_session_locked()
        await self.wait_for_cleanup()

    async def wait_for_cleanup(self) -> None:
        """Await background driver teardown (diagnostics/tests/shutdown)."""
        while self._cleanup_tasks:
            tasks = list(self._cleanup_tasks)
            await asyncio.gather(*tasks, return_exceptions=True)
            # ``gather`` on already-done tasks does not yield to the loop, so
            # the done callbacks may not have run yet: drop them explicitly to
            # keep this loop finite.
            for task in tasks:
                self._cleanup_tasks.discard(task)

    def _schedule_driver_close(
        self,
        driver: VerifyBrowserSession | None,
        session: VerifySession | None = None,
    ) -> None:
        if driver is None:
            return
        task = asyncio.create_task(
            self._close_driver_bounded(
                driver,
                session_id=session.session_id if session is not None else None,
                account_id=session.account_id if session is not None else None,
                # Captured before ``close`` drops its own ``_process`` reference
                # so a failed/slow teardown can still be located by pid.
                pid=getattr(driver, "pid", None),
            )
        )
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _close_driver_bounded(
        self,
        driver: VerifyBrowserSession,
        *,
        session_id: str | None = None,
        account_id: str | None = None,
        pid: int | None = None,
    ) -> None:
        try:
            await asyncio.wait_for(driver.close(), timeout=DRIVER_CLOSE_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Opaque ids only: never log cookies/e-mail here.
            log.warning(
                "验证浏览器后台清理受限结束: session=%s account=%s pid=%s error=%s",
                session_id or "-",
                account_id or "-",
                pid if pid is not None else "-",
                type(exc).__name__,
            )

    async def _await_or_cancel(
        self, awaitable: Awaitable[object], cancel_event: asyncio.Event
    ) -> tuple[bool, object]:
        """Await ``awaitable`` unless the session is cancelled first."""
        task = asyncio.ensure_future(awaitable)
        if cancel_event.is_set():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            return False, None
        cancel_wait = asyncio.create_task(cancel_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {task, cancel_wait}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            raise
        finally:
            cancel_wait.cancel()
            with contextlib.suppress(BaseException):
                await cancel_wait
        if task in done:
            return True, task.result()
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        return False, None

    async def _refresh(
        self,
        session: VerifySession,
        driver: VerifyBrowserSession,
        cancel_event: asyncio.Event,
    ) -> None:
        if session.state in _TERMINAL_STATES or cancel_event.is_set():
            return
        now = time.time()
        if now >= session.expires_at:
            await self._finish(session, VerifySessionState.EXPIRED, "expired")
            return
        try:
            done, probe = await self._await_or_cancel(driver.poll(), cancel_event)
        except Exception as exc:
            log.warning(
                "验证会话页面探测失败: session=%s account=%s error=%s",
                session.session_id,
                session.account_id,
                type(exc).__name__,
            )
            await self._finish(session, VerifySessionState.ERROR, "browser_poll_failed")
            return
        if not done:
            return
        assert isinstance(probe, PageProbe)

        if is_login_page_url(probe.page_url):
            await self._finish(
                session, VerifySessionState.SESSION_INVALID, "login_redirect"
            )
            return
        if "available-regions" in probe.page_url:
            await self._finish(
                session, VerifySessionState.REGION_BLOCKED, "available_regions"
            )
            return
        if probe.failed:
            await self._finish(session, VerifySessionState.FAILED, "page_failed")
            return
        if not probe.success:
            return

        try:
            done, status = await self._await_or_cancel(
                driver.confirm_verified(session.auth_user), cancel_event
            )
        except VerifySessionInvalid:
            await self._finish(
                session, VerifySessionState.SESSION_INVALID, "session_invalid"
            )
            return
        except VerifyRegionBlocked:
            await self._finish(
                session, VerifySessionState.REGION_BLOCKED, "region_blocked"
            )
            return
        except Exception as exc:
            # Bounded: the direct RPC has its own short timeout; any transport
            # error stays pending and is retried by the next status call.
            log.warning(
                "验证会话状态确认失败: session=%s account=%s error=%s",
                session.session_id,
                session.account_id,
                type(exc).__name__,
            )
            return
        if not done:
            return
        if status == 2:
            self._clear_verify_required(session.account_id)
            await self._finish(session, VerifySessionState.PASSED, "verified")

    def _clear_verify_required(self, account_id: str) -> None:
        self._store.update_account_refresh(account_id, verify_required=False)
        rotator = self._rotator
        if rotator is None:
            return
        clearer = getattr(rotator, "record_verify_cleared", None)
        if callable(clearer):
            # Persists rotator_state immediately so a restart cannot revive a
            # stale ``verify_required=true`` from before this successful pass.
            clearer(account_id)
            return
        stats = rotator.get_account_stats(account_id)
        if stats is not None:
            stats.verify_required = False
            stats.last_verify_required_at = 0.0
        save = getattr(rotator, "save_state", None)
        if callable(save):
            save()

    async def _finish(
        self,
        session: VerifySession,
        state: VerifySessionState,
        message: str,
    ) -> None:
        """Finish a session from unlocked code without blocking on teardown."""
        async with self._lock:
            if self._session is not session:
                return
            self._finish_locked(session, state, message)

    def _finish_locked(
        self,
        session: VerifySession,
        state: VerifySessionState,
        message: str,
    ) -> None:
        if session.state in _TERMINAL_STATES:
            return
        session.state = state
        session.message = message
        session.finished_at = time.time()
        retention = (
            self._passed_retention
            if state is VerifySessionState.PASSED
            else self._terminal_retention
        )
        session.cleanup_at = session.finished_at + retention
        driver = self._driver
        self._driver = None
        duration = max(0.0, session.finished_at - session.created_at)
        log.info(
            "验证会话结束: session=%s account=%s state=%s duration=%.1fs",
            session.session_id,
            session.account_id,
            state.value,
            duration,
        )
        self._schedule_timer_locked(session.cleanup_at, session.session_id)
        self._schedule_driver_close(driver, session)

    def _schedule_timer_locked(self, deadline: float, session_id: str) -> None:
        current = self._timer_task
        if current is not None and not current.done():
            current.cancel()
        self._timer_task = asyncio.create_task(self._timer(deadline, session_id))

    async def _timer(self, deadline: float, session_id: str) -> None:
        if self._timer_task is asyncio.current_task():
            self._timer_task = None
        delay = max(0.0, deadline - time.time())
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if self._timer_task is asyncio.current_task():
                self._timer_task = None
            session = self._session
            if session is None or session.session_id != session_id:
                return
            now = time.time()
            if session.state is VerifySessionState.PENDING:
                if now >= session.expires_at:
                    self._finish_locked(session, VerifySessionState.EXPIRED, "expired")
                return
            if session.cleanup_at is not None and now >= session.cleanup_at:
                await self._dispose_session_locked()

    async def _cleanup_due_locked(self) -> None:
        session = self._session
        if session is None:
            return
        if (
            session.state in _TERMINAL_STATES
            and session.cleanup_at is not None
            and time.time() >= session.cleanup_at
        ):
            await self._dispose_session_locked()

    async def _dispose_session_locked(self) -> None:
        current = self._timer_task
        self._timer_task = None
        if current is not None and not current.done():
            current.cancel()
            with contextlib.suppress(BaseException):
                await current
        session = self._session
        if session is not None and session.state in _ACTIVE_STATES:
            session.state = VerifySessionState.CANCELLED
            session.message = "cancelled"
            session.finished_at = time.time()
        driver = self._driver
        self._driver = None
        self._session = None
        self._cancel_event = None
        self._session_lock = None
        self._schedule_driver_close(driver, session)


class CdpVerifyBrowserDriver:
    """Headless CloakBrowser + CDP implementation of ``VerifyBrowserSession``."""

    def __init__(
        self,
        *,
        auth_file: Path,
        auth_user: str = "0",
        chrome_path: str | None = None,
        proxy: str | None = None,
        code_timeout_seconds: float = DEFAULT_CODE_TIMEOUT_SECONDS,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._auth_file = auth_file
        self._auth_user = auth_user or "0"
        self._chrome_path = (
            chrome_path
            if chrome_path is not None
            else (os.environ.get(CHROME_PATH_ENV) or "").strip() or None
        )
        self._proxy = (
            proxy
            if proxy is not None
            else (os.environ.get(DEFAULT_PROXY_ENV) or "").strip() or None
        )
        self._code_timeout = max(0.1, code_timeout_seconds)
        self._http_transport = http_transport
        self._process: asyncio.subprocess.Process | None = None
        self._client: CDPClient | None = None
        self._page: CDPPage | None = None
        self._user_data_dir: str | None = None
        self._worlds: dict[str, int] = {}
        self._closed = False

    def _resolve_binary(self) -> Path:
        """Reuse the repository Chromium resolver (local/Termux wrapper) first.

        ``AISTUDIO_VERIFY_CHROME_PATH`` always wins; otherwise the shared
        ``find_chromium_executable`` handles ``.cloakbrowser/**/chrome`` and the
        Termux ``scripts/cloakbrowser_termux/run-chrome.sh`` wrapper.
        """
        configured = self._chrome_path
        if configured:
            binary = Path(configured)
            if binary.is_file():
                return binary
            raise VerifyBrowserUnavailable(_chrome_unavailable_hint(configured))
        try:
            return Path(find_chromium_executable())
        except FileNotFoundError as exc:
            raise VerifyBrowserUnavailable(_chrome_unavailable_hint(None)) from exc

    @property
    def pid(self) -> int | None:
        """Current Chromium pid, if launched (used for teardown diagnostics)."""
        process = self._process
        return process.pid if process is not None else None

    async def start(self) -> QrChallenge:
        binary = self._resolve_binary()
        self._user_data_dir = tempfile.mkdtemp(prefix="aistudio-verify-")
        port = _free_port()
        command = _build_verify_chrome_command(
            binary=binary,
            port=port,
            user_data_dir=self._user_data_dir,
            proxy=self._proxy,
        )
        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=platform.system() != "Windows",
            )
            client = CDPClient(port)
            self._client = client
            page = await client.connect_page(block_assets=False)
            self._page = page
        except Exception as exc:
            await self.close()
            raise VerifyBrowserUnavailable(
                "验证浏览器启动失败，请检查 AISTUDIO_VERIFY_CHROME_PATH 与运行用户权限"
            ) from exc

        try:
            loaded = await load_cookies_to_page(page, str(self._auth_file))
            if not loaded:
                raise VerifyAccountError("credentials_missing")
            await page.goto(
                VERIFY_ACCOUNT_URL, timeout_s=30.0, wait_until="domcontentloaded"
            )
            await self._raise_on_redirect(page)
            return await self._wait_for_qr(page)
        except BaseException:
            await self.close()
            raise

    async def poll(self) -> PageProbe:
        page = self._page
        if page is None or page.is_closed():
            return PageProbe(page_url="", success=False)
        snapshot = await _page_snapshot(page)
        url = str(snapshot.get("url") or page.url or "")
        text = str(snapshot.get("text") or "")
        return PageProbe(
            page_url=url,
            success=_is_success_page(url, text),
            failed=_is_failure_page(url, text),
        )

    async def confirm_verified(self, auth_user: str) -> int | None:
        """Call GetUserVerificationStatus directly from Python.

        This avoids depending on ``document.cookie`` (SAPISID may be HttpOnly)
        and on the page's cross-origin preflight.  It uses the same egress as
        the verification browser: direct by default, or ``AISTUDIO_VERIFY_PROXY``.
        Classification: 200 ``[2]``=verified, 200 ``[1]``=pending; 401 and 403
        only map to a terminal ``session_invalid``/``region_blocked`` when the
        body carries an explicit upstream marker, otherwise the session stays
        pending.  Timeouts/other errors stay pending and are retried.
        """
        cookies = _read_auth_cookies(self._auth_file)
        if not cookies:
            return None
        authorization = calculate_sapisid_hash(cookies)
        if not authorization:
            log.warning("验证状态确认缺少 SAPISID，保持 pending 等待下次重试")
            return None
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers = {
            "Content-Type": "application/json+protobuf",
            "User-Agent": DEFAULT_BROWSER_USER_AGENT,
            "accept-language": DEFAULT_ACCEPT_LANGUAGE,
            "X-User-Agent": GRPC_WEB_USER_AGENT,
            "X-Goog-Api-Key": WEB_API_KEY,
            "X-Goog-AuthUser": auth_user or self._auth_user or "0",
            EXT_BIN_HEADER: EXT_BIN,
            "X-AIStudio-Visit-Id": f"v1_{uuid.uuid4()}",
            "Origin": AI_STUDIO_ORIGIN,
            "Referer": AI_STUDIO_HOME_URL,
            "Authorization": authorization,
            "Cookie": cookie_header,
        }

        try:
            async with httpx.AsyncClient(
                timeout=CONFIRM_RPC_TIMEOUT_SECONDS,
                proxy=self._proxy,
                # Never inherit HTTPS_PROXY/ALL_PROXY from the host: the browser
                # and this RPC must share one egress (explicit env or direct).
                trust_env=False,
                transport=self._http_transport,
            ) as client:
                resp = await client.post(_VERIFY_RPC_URL, headers=headers, content="[]")
        except Exception as exc:
            log.debug("验证状态直连 RPC 失败: %s", type(exc).__name__)
            return None
        body = resp.text or ""
        if resp.status_code == 401:
            if _body_has_marker(body, _SESSION_INVALID_401_MARKERS):
                raise VerifySessionInvalid("session_invalid")
            log.debug("验证状态直连 RPC 返回 401 但未命中失效标记，保持 pending")
            return None
        if resp.status_code == 403:
            if _body_has_marker(body, _REGION_403_MARKERS):
                raise VerifyRegionBlocked("region_blocked")
            log.debug("验证状态直连 RPC 返回 403 但未命中地区标记，保持 pending")
            return None
        if resp.status_code != 200:
            log.debug("验证状态直连 RPC 返回 HTTP %d，保持 pending", resp.status_code)
            return None
        try:
            data = resp.json()
        except Exception:
            return None
        value: object = data[0] if isinstance(data, list) and data else None
        if isinstance(value, list) and value:
            value = value[0]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(value)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        user_data_dir = self._user_data_dir
        self._user_data_dir = None
        try:
            # Terminate the process first: after the browser is gone, Page.close
            # / CDP close fail fast (and are still bounded) instead of waiting
            # out the 30s default CDP timeout on a hung renderer.
            process = self._process
            self._process = None
            if process is not None:
                await _terminate_process(process)
            page = self._page
            self._page = None
            if page is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        page.close(), timeout=PAGE_CLOSE_TIMEOUT_SECONDS
                    )
            client = self._client
            self._client = None
            if client is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        client.close(), timeout=CLIENT_CLOSE_TIMEOUT_SECONDS
                    )
        finally:
            # Also runs when the outer bounded ``wait_for`` cancels teardown so
            # a timed-out close never leaves the temp profile on disk.
            if user_data_dir:
                shutil.rmtree(user_data_dir, ignore_errors=True)

    async def _raise_on_redirect(self, page: CDPPage) -> None:
        url = await _current_url(page)
        if is_login_page_url(url):
            raise VerifySessionInvalid("login_redirect")
        if "available-regions" in url:
            raise VerifyRegionBlocked("available_regions")

    async def _wait_for_qr(self, page: CDPPage) -> QrChallenge:
        deadline = time.monotonic() + self._code_timeout
        last_click_at = 0.0
        while time.monotonic() < deadline:
            url = await _current_url(page)
            if is_login_page_url(url):
                raise VerifySessionInvalid("login_redirect")
            if "available-regions" in url:
                raise VerifyRegionBlocked("available_regions")
            challenge = await self._read_bframe_qr(page)
            if challenge is not None:
                return challenge
            now = time.monotonic()
            if now - last_click_at >= CLICK_RETRY_INTERVAL_SECONDS:
                # Only enabled elements are clicked.  A disabled button returns
                # false and the loop keeps retrying inside the same 20s budget;
                # success is only declared once the bframe actually yields a QR.
                clicked = False
                with contextlib.suppress(Exception):
                    clicked = bool(await page.evaluate(_CLICK_VERIFY_DEVICE_JS))
                if clicked:
                    last_click_at = now
            await asyncio.sleep(0.5)
        raise VerifyChallengeTimeout("qr_timeout")

    async def _read_bframe_qr(self, page: CDPPage) -> QrChallenge | None:
        try:
            response = await page.cdp.send("Page.getFrameTree")
        except Exception:
            return None
        frames = list(_iter_frame_tree(response.get("frameTree")))
        live_ids = {str(frame.get("id") or "") for frame in frames if frame.get("id")}
        # A navigated/rebuilt bframe invalidates its old isolated world.
        for stale_id in [key for key in self._worlds if key not in live_ids]:
            self._worlds.pop(stale_id, None)
        for frame in frames:
            url = str(frame.get("url") or "")
            if "bframe" not in url and "recaptcha" not in url:
                continue
            frame_id = str(frame.get("id") or "")
            if not frame_id:
                continue
            value = await self._evaluate_qr_in_frame(page, frame_id)
            if not isinstance(value, dict):
                continue
            payload = value.get("value")
            if not isinstance(payload, dict):
                continue
            short_code = _extract_short_code(
                (
                    str(payload.get("href") or ""),
                    str(payload.get("title") or ""),
                    str(payload.get("fallback") or ""),
                )
            )
            if short_code:
                return QrChallenge(
                    qr_url=f"https://recaptcha.net/qr/{short_code}",
                    short_code=short_code,
                )
        return None

    async def _evaluate_qr_in_frame(
        self, page: CDPPage, frame_id: str
    ) -> dict[str, object] | None:
        """Evaluate the QR probe, rebuilding the isolated world when stale."""
        for attempt in range(2):
            context_id = self._worlds.get(frame_id)
            if context_id is None:
                context_id = await self._create_isolated_world(page, frame_id)
                if context_id is None:
                    return None
            try:
                result = await page.cdp.send(
                    "Runtime.evaluate",
                    {
                        "contextId": context_id,
                        "expression": _READ_QR_JS,
                        "returnByValue": True,
                    },
                )
            except Exception:
                # Context/frame was destroyed mid-challenge: drop the cache and
                # rebuild once inside the same round.
                self._worlds.pop(frame_id, None)
                if attempt == 0:
                    continue
                return None
            value = result.get("result")
            if isinstance(value, dict) and value.get("exceptionDetails"):
                self._worlds.pop(frame_id, None)
                if attempt == 0:
                    continue
                return None
            return value if isinstance(value, dict) else None
        return None

    async def _create_isolated_world(self, page: CDPPage, frame_id: str) -> int | None:
        try:
            world = await page.cdp.send(
                "Page.createIsolatedWorld",
                {
                    "frameId": frame_id,
                    "worldName": "aistudio_verify_qr",
                    "grantUniveralAccess": True,
                },
            )
        except Exception:
            return None
        raw_context = world.get("executionContextId")
        if not isinstance(raw_context, int):
            return None
        self._worlds[frame_id] = raw_context
        return raw_context


def _chrome_unavailable_hint(configured: str | None) -> str:
    current = f"当前配置: {configured}；" if configured else ""
    return (
        f"未找到验证浏览器（CloakBrowser/Chromium）。{current}"
        "可用以下任一方式提供：\n"
        "  1) 仓库默认安装路径 .cloakbrowser/chromium-*/chrome"
        "（先运行 scripts/setup-browser.sh 或 scripts\\install-browser.bat）；\n"
        "  2) Termux 下使用仓库包装脚本 scripts/cloakbrowser_termux/run-chrome.sh"
        "（glibc/bionic ABI 需要 proot，不能直连 /opt/cloakbrowser/chrome）；\n"
        "  3) 设置 AISTUDIO_VERIFY_CHROME_PATH 指向上述任一可执行文件。"
    )


def _read_auth_cookies(auth_file: Path) -> dict[str, str]:
    try:
        raw = json.loads(auth_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return cookies_for_google_rpc(raw)


def _build_verify_chrome_command(
    *,
    binary: Path,
    port: int,
    user_data_dir: str,
    proxy: str | None,
) -> list[str]:
    # Reuse the canonical launcher argument set so the verification Chromium
    # shares the pool's low-memory, SwiftShader, ``--disable-features``,
    # ``--no-zygote`` and fingerprint (platform/timezone/locale) contract
    # instead of drifting (§4.7 / P2-R5).
    #
    # Intentional differences from a pooled instance:
    # * ``--remote-debugging-port`` is kept: CDP is how the verify flow reads
    #   the reCAPTCHA bframe world (it is a real requirement here, not debug).
    # * the proven 1280x900 window replaces the canonical 1280x800;
    # * egress stays direct unless ``AISTUDIO_VERIFY_PROXY`` is set, so the
    #   ``settings.proxy_url`` fallback inside ``build_chromium_args`` is
    #   stripped when no explicit verify proxy is configured.
    args = build_chromium_args(
        port=port,
        user_data_dir=user_data_dir,
        headless=True,
        proxy_url=proxy,
        extra_args=[
            f"--user-agent={CHROME_UA}",
            "--lang=en-US",
        ],
    )
    args = [
        "--window-size=1280,900" if arg.startswith("--window-size=") else arg
        for arg in args
    ]
    if proxy is None:
        args = [arg for arg in args if not arg.startswith("--proxy-server=")]
    return [str(binary), *args]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _current_url(page: CDPPage) -> str:
    with contextlib.suppress(Exception):
        value = await page.evaluate("() => window.location.href", timeout_s=3.0)
        if value:
            return str(value)
    return page.url or ""


async def _page_snapshot(page: CDPPage) -> dict[str, object]:
    try:
        value = await page.evaluate(_PAGE_SNAPSHOT_JS, timeout_s=5.0)
    except Exception:
        return {}
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


async def _taskkill_tree_windows(pid: int) -> None:
    """Kill a Windows process tree while the parent-child links still exist."""
    with contextlib.suppress(Exception):
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/F",
            "/T",
            "/PID",
            str(pid),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()


async def _terminate_process(
    process: asyncio.subprocess.Process,
    timeout_s: float = 1.5,
) -> None:
    if process.returncode is not None:
        return
    if platform.system() == "Windows":
        # ``taskkill /T`` must run before the root exits, otherwise Chromium
        # renderer/GPU children can be reparented and survive.  Terminating the
        # root first (the old order) made the tree unkillable by PID.
        await _taskkill_tree_windows(process.pid)
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(process.wait(), timeout=timeout_s)
        if process.returncode is None:
            _signal_process(process, signal.SIGKILL)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=1.0)
        return
    _signal_process(process, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_s)
        return
    except (TimeoutError, asyncio.CancelledError):
        pass
    _signal_process(process, signal.SIGKILL)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(process.wait(), timeout=1.0)


def _signal_process(process: asyncio.subprocess.Process, sig: int) -> None:
    if platform.system() == "Windows":
        with contextlib.suppress(ProcessLookupError):
            if sig == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()
        return
    try:
        os.killpg(os.getpgid(process.pid), sig)
    except OSError:
        with contextlib.suppress(ProcessLookupError):
            if sig == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()


def _default_driver_factory(
    session: VerifySession, auth_file: Path
) -> VerifyBrowserSession:
    return CdpVerifyBrowserDriver(
        auth_file=auth_file,
        auth_user=session.auth_user,
    )


_verify_service: VerifyAccountService | None = None


async def init_verify_account_service(
    account_store: AccountStore,
    rotator: AccountRotator | None,
    *,
    driver_factory: VerifyDriverFactory | None = None,
) -> VerifyAccountService:
    global _verify_service
    await shutdown_verify_account_service()
    _verify_service = VerifyAccountService(
        account_store,
        rotator,
        driver_factory=driver_factory,
    )
    return _verify_service


def get_verify_account_service() -> VerifyAccountService | None:
    return _verify_service


def set_verify_account_service(service: VerifyAccountService | None) -> None:
    """Test hook: inject a service instance without touching the lifespan."""
    global _verify_service
    _verify_service = service


async def shutdown_verify_account_service() -> None:
    global _verify_service
    service = _verify_service
    _verify_service = None
    if service is not None:
        await service.close()


__all__ = [
    "CdpVerifyBrowserDriver",
    "PageProbe",
    "QrChallenge",
    "VerifyAccountController",
    "VerifyAccountError",
    "VerifyAccountNotFound",
    "VerifyAccountService",
    "VerifyBrowserSession",
    "VerifyBrowserUnavailable",
    "VerifyChallengeTimeout",
    "VerifyDriverFactory",
    "VerifyRegionBlocked",
    "VerifySession",
    "VerifySessionConflict",
    "VerifySessionInvalid",
    "VerifySessionNotFound",
    "VerifySessionState",
    "get_verify_account_service",
    "init_verify_account_service",
    "set_verify_account_service",
    "shutdown_verify_account_service",
]
