"""VPS bug 2: 403 same-account resign must refresh cookies, not resend stale ones."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.application.routing_pipeline import RoutingPipeline
from aistudio_api.domain.errors import AuthError
from aistudio_api.infrastructure.account import cookie_parser
from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.gateway.capture import (
    CapturedRequest,
    RequestCaptureService,
)
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.instance_client import InstanceRequestClient
from aistudio_api.infrastructure.gateway.pooled_session import PooledSession
from aistudio_api.infrastructure.gateway.session import (
    DEFAULT_BOOTSTRAP_TEMPLATE,
    BrowserSession,
)
from aistudio_api.infrastructure.gateway.session_state import SessionContext
from aistudio_api.infrastructure.gateway.transport import (
    XHRStreamTransport,
    _ensure_authorization_header,
    refresh_page_auth_state,
)
from tests.unit.routing_fakes import (
    FakeManager,
    RebindFakeManager,
    make_attempt,
    make_context,
    make_instance,
)


class _TrackingClient:
    def __init__(
        self,
        *,
        refresh_result: bool = True,
        refresh_error: Exception | None = None,
    ) -> None:
        self.events: list[str] = []
        self.refresh_calls = 0
        self.clear_calls = 0
        self._refresh_result = refresh_result
        self._refresh_error = refresh_error

    async def refresh_auth_state(self) -> bool:
        self.events.append("refresh")
        self.refresh_calls += 1
        if self._refresh_error is not None:
            raise self._refresh_error
        return self._refresh_result

    def clear_templates(self) -> bool:
        self.events.append("clear")
        self.clear_calls += 1
        return True


class _RefreshableSession:
    def __init__(self) -> None:
        self.refresh_calls = 0

    async def refresh_auth_state(self) -> bool:
        self.refresh_calls += 1
        return True


class _RebindRotator:
    def select_rebind_account(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(id="acc-2", tier="Free")

    def record_auth_error(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def record_error(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


def _log_count(caplog: pytest.LogCaptureFixture, text: str) -> int:
    return sum(text in record.getMessage() for record in caplog.records)


REFRESH_FAILED_LOG = "\u9274\u6743\u5237\u65b0\u5931\u8d25"
REFRESH_ERROR_LOG = "\u9274\u6743\u5237\u65b0\u5f02\u5e38"
COOKIE_REFRESH_FAILED_LOG = "\u5237\u65b0\u9875\u9762\u9274\u6743 cookies \u5931\u8d25"
CLEARED_TEMPLATES_LOG = "\u5df2\u6e05\u7a7a\u8bf7\u6c42\u6a21\u677f\u7f13\u5b58"


@pytest.mark.asyncio
async def test_403_first_retry_refreshes_auth_before_clearing_templates() -> None:
    client = _TrackingClient(refresh_result=True)
    context = make_context([make_instance("inst-1", account_id="acc-1")])
    attempt = make_attempt(client)

    pipeline = RoutingPipeline(
        instance_manager=FakeManager([make_instance("inst-1", account_id="acc-1")])
    )
    outcome = await pipeline.handle_error(
        context, attempt, AuthError("HTTP 403: WAA denied"), has_yielded_data=False
    )

    assert outcome == "retry_same"
    assert client.events == ["refresh", "clear"]
    assert client.refresh_calls == 1
    assert attempt.same_retries == 1
    assert context.retry_same is True
    assert context.budget.retries == 1


@pytest.mark.asyncio
async def test_403_auth_refresh_failure_raises_without_resending(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _TrackingClient(refresh_result=False)
    context = make_context([make_instance("inst-1", account_id="acc-1")])
    attempt = make_attempt(client)
    pipeline = RoutingPipeline(
        instance_manager=FakeManager([make_instance("inst-1", account_id="acc-1")])
    )

    with caplog.at_level(logging.WARNING), pytest.raises(AuthError):
        await pipeline.handle_error(
            context,
            attempt,
            AuthError("HTTP 403: WAA denied"),
            has_yielded_data=False,
        )

    assert client.events == ["refresh"]
    assert attempt.same_retries == 0
    assert context.retry_same is False
    assert context.failed_accounts == {"acc-1"}
    assert _log_count(caplog, REFRESH_FAILED_LOG) == 1


@pytest.mark.asyncio
async def test_403_auth_refresh_failure_switches_when_candidate_exists(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _TrackingClient(refresh_result=False)
    manager = FakeManager(
        [
            make_instance("inst-1", account_id="acc-1", number=1),
            make_instance("inst-2", account_id="acc-2", number=2),
        ]
    )
    context = make_context(manager.instances)
    attempt = make_attempt(client)
    pipeline = RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {"max_switches": 2},
    )

    with caplog.at_level(logging.WARNING):
        outcome = await pipeline.handle_error(
            context, attempt, AuthError("HTTP 403: WAA denied"), has_yielded_data=False
        )

    assert outcome == "switch"
    assert client.events == ["refresh"]
    assert attempt.same_retries == 0
    assert context.retry_same is False
    assert context.failed_accounts == {"acc-1"}
    assert context.budget.switches == 1
    assert _log_count(caplog, REFRESH_FAILED_LOG) == 1


@pytest.mark.asyncio
async def test_403_auth_refresh_exception_switches_instead_of_resending(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _TrackingClient(refresh_error=RuntimeError("CDP timed out"))
    manager = FakeManager(
        [
            make_instance("inst-1", account_id="acc-1", number=1),
            make_instance("inst-2", account_id="acc-2", number=2),
        ]
    )
    context = make_context(manager.instances)
    attempt = make_attempt(client)
    pipeline = RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {"max_switches": 2},
    )

    with caplog.at_level(logging.WARNING):
        outcome = await pipeline.handle_error(
            context, attempt, AuthError("HTTP 403: WAA denied"), has_yielded_data=False
        )

    assert outcome == "switch"
    assert attempt.same_retries == 0
    assert context.retry_same is False
    assert _log_count(caplog, REFRESH_ERROR_LOG) == 1


@pytest.mark.asyncio
async def test_refresh_auth_state_invalidates_and_refetches_before_next_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cookie_parser.time, "time", lambda: 1_700_000_000)
    stale = {"SAPISID": "stale-sapisid", "SID": "sid"}
    fresh = {
        "SAPISID": "fresh-sapisid",
        "__Secure-1PAPISID": "fresh-1papisid",
        "__Secure-1PSIDTS": "fresh-1psidts",
    }
    page = MagicMock(spec=CDPPage)
    page.is_closed = MagicMock(return_value=False)
    page._cached_cookies = dict(stale)
    seen_at_fetch: list[dict[str, str] | None] = []

    async def fake_get_cookies() -> list[dict[str, object]]:
        seen_at_fetch.append(page._cached_cookies)
        page._cached_cookies = dict(fresh)
        return [{"name": key, "value": value} for key, value in fresh.items()]

    page.get_cookies = AsyncMock(side_effect=fake_get_cookies)
    session = BrowserSession(port=9222)
    session.set_ready_context(
        SessionContext(
            page=page,
            snap_key="snap-key",
            auth_user="0",
            auth_file=None,
            base_template=dict(DEFAULT_BOOTSTRAP_TEMPLATE),
        )
    )

    assert await session.refresh_auth_state() is True
    assert seen_at_fetch == [None]
    assert page._cached_cookies == fresh
    page.get_cookies.assert_awaited_once()

    headers = await _ensure_authorization_header(page, {}, auth_user="0", tier="Free")
    assert headers["Authorization"] == cookie_parser.calculate_sapisid_hash(fresh)
    assert headers["Authorization"] != cookie_parser.calculate_sapisid_hash(stale)


@pytest.mark.asyncio
async def test_refresh_page_auth_state_failure_is_reported_and_not_cached(
    caplog: pytest.LogCaptureFixture,
) -> None:
    page = MagicMock(spec=CDPPage)
    page.is_closed = MagicMock(return_value=False)
    page._cached_cookies = {"SAPISID": "stale"}
    page.get_cookies = AsyncMock(side_effect=RuntimeError("CDP unavailable"))

    with caplog.at_level(logging.WARNING):
        assert await refresh_page_auth_state(page) is False

    assert page._cached_cookies is None
    assert _log_count(caplog, COOKIE_REFRESH_FAILED_LOG) == 1


@pytest.mark.asyncio
async def test_pooled_session_refreshes_its_own_realm_cookies() -> None:
    class _Page:
        def __init__(self) -> None:
            self._cached_cookies: dict[str, str] | None = {"SAPISID": "stale"}
            self.cached_at_fetch: dict[str, str] | None = None
            self.fetch_calls = 0

        def is_closed(self) -> bool:
            return False

        async def get_cookies(self) -> list[dict[str, object]]:
            self.fetch_calls += 1
            self.cached_at_fetch = self._cached_cookies
            self._cached_cookies = {
                "SAPISID": "fresh",
                "__Secure-1PSIDTS": "fresh-ts",
            }
            return [
                {"name": key, "value": value}
                for key, value in self._cached_cookies.items()
            ]

    page = _Page()
    session = PooledSession(
        context=SessionContext(
            page=page,  # type: ignore[arg-type]
            snap_key="snap-key",
            auth_user="0",
            auth_file=None,
            base_template=dict(DEFAULT_BOOTSTRAP_TEMPLATE),
        ),
        transport=XHRStreamTransport(),
        realm_id="slot-1-g1",
    )

    assert await session.refresh_auth_state() is True
    assert page.cached_at_fetch is None
    assert page.fetch_calls == 1
    assert page._cached_cookies == {
        "SAPISID": "fresh",
        "__Secure-1PSIDTS": "fresh-ts",
    }


@pytest.mark.asyncio
async def test_instance_client_refresh_auth_state_delegates_to_session() -> None:
    session = _RefreshableSession()
    client = InstanceRequestClient(
        session,  # type: ignore[arg-type]
        account_id="acc-1",
        tier="Free",
        surface="chat",
    )

    assert await client.refresh_auth_state() is True
    assert session.refresh_calls == 1


def test_clear_templates_empty_cache_is_silent_and_idempotent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = RequestCaptureService(_RefreshableSession())  # type: ignore[arg-type]
    caplog.set_level(logging.INFO)

    assert [service.clear_templates() for _ in range(3)] == [False, False, False]

    assert _log_count(caplog, CLEARED_TEMPLATES_LOG) == 0

    service._templates["gemini-3.7-flash"] = CapturedRequest(
        url="https://example.invalid", headers={}, body="[]"
    )
    assert service.clear_templates() is True
    assert service.clear_templates() is False

    assert _log_count(caplog, CLEARED_TEMPLATES_LOG) == 1


def test_ai_studio_client_clear_templates_returns_cache_result() -> None:
    client = AIStudioClient(port=0)

    assert client.clear_templates() is False
    client._capture_service._templates["gemini-3.7-flash"] = CapturedRequest(
        url="https://example.invalid", headers={}, body="[]"
    )
    assert client.clear_templates() is True
    assert client.clear_templates() is False

    service = client.__dict__.pop("_capture_service")
    try:
        assert client.clear_templates() is False
    finally:
        client.__dict__["_capture_service"] = service


@pytest.mark.asyncio
async def test_403_switch_rebind_path_logs_one_clear_at_most(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sessions = {"inst-1": _RefreshableSession(), "inst-2": _RefreshableSession()}
    manager = RebindFakeManager(
        [
            make_instance("inst-1", account_id="acc-1", number=1),
            make_instance("inst-2", account_id="acc-2", number=2),
        ],
        sessions=sessions,
    )
    client = InstanceRequestClient(
        sessions["inst-1"],  # type: ignore[arg-type]
        account_id="acc-1",
        tier="Free",
        surface="chat",
    )
    client._capture._templates["gemini-3.7-flash"] = CapturedRequest(
        url="https://example.invalid", headers={}, body="[]"
    )
    context = make_context(manager.instances, max_switches=2)
    # Second 403 for the same attempt: the handler must not clear again before
    # the rebind; only the rebind's own clear is allowed to log.
    attempt = make_attempt(client, same_retries=1)
    pipeline = RoutingPipeline(
        instance_manager=manager,
        rotator=_RebindRotator(),  # type: ignore[arg-type]
        routing_settings_provider=lambda: {"max_switches": 2},
    )

    with caplog.at_level(logging.INFO):
        outcome = await pipeline.handle_error(
            context, attempt, AuthError("HTTP 403: WAA denied"), has_yielded_data=False
        )

    assert outcome == "switch"
    assert manager.reassign_calls == [("inst-1", "acc-2")]
    assert _log_count(caplog, CLEARED_TEMPLATES_LOG) == 1
