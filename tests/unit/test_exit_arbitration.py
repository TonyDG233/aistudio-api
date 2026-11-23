"""Region-403 canary arbitration tests (offline fakes only)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.application.exit_arbitration import (
    CanaryVerdict,
    ExitArbitration,
    build_exit_arbitration,
    get_default_exit_arbitration,
    reset_default_exit_arbitration,
)
from aistudio_api.application.routing_pipeline import RoutingPipeline
from aistudio_api.domain.errors import (
    AuthError,
    TierRequiredError,
    classify_error,
    is_region_restricted_message,
)
from aistudio_api.domain.instances import Instance
from aistudio_api.infrastructure.exit_pool import ExitAssignment
from aistudio_api.infrastructure.gateway.public_web_constants import LIST_MODELS_URL
from tests.unit.routing_fakes import (
    FakeManager,
    make_attempt,
    make_context,
    make_instance,
)


class _TrackingClient:
    def __init__(self, *, refresh_result: bool = True) -> None:
        self.refresh_calls = 0
        self.clear_calls = 0
        self._refresh_result = refresh_result

    async def refresh_auth_state(self) -> bool:
        self.refresh_calls += 1
        return self._refresh_result

    def clear_templates(self) -> bool:
        self.clear_calls += 1
        return True


class _RestartManager(FakeManager):
    def __init__(self, instances: list[Instance]) -> None:
        super().__init__(instances)
        self.restart_calls: list[str] = []
        self.active: dict[str, int] = {}

    def instance_active_requests(self, instance_id: str) -> int:
        return self.active.get(instance_id, 0)

    async def restart(self, instance_id: str) -> Instance:
        self.restart_calls.append(instance_id)
        return next(item for item in self.instances if item.id == instance_id)


class _FailingRestartManager(_RestartManager):
    async def restart(self, instance_id: str) -> Instance:
        self.restart_calls.append(instance_id)
        raise RuntimeError("rebuild failed")


class _FakeResolver:
    def __init__(self, assignment: ExitAssignment | None) -> None:
        self.assignment = assignment
        self.invalidated: list[tuple[str, str | None]] = []
        self.invalidated_exits: list[str] = []
        self.events: list[dict[str, str]] = []
        self.report_error: Exception | None = None

    def assignment_for(
        self,
        account_id: str,
        *,
        kind: str = "chat",
        include_expired: bool = False,
    ) -> ExitAssignment | None:
        del account_id, kind, include_expired
        return self.assignment

    def invalidate(self, account_id: str, *, kind: str | None = None) -> None:
        self.invalidated.append((account_id, kind))

    def invalidate_exit(self, exit_id: str) -> None:
        self.invalidated_exits.append(exit_id)

    async def report_event(
        self,
        *,
        account_id: str,
        exit_id: str,
        event_kind: str,
        canary: str,
    ) -> bool:
        if self.report_error is not None:
            raise self.report_error
        self.events.append(
            {
                "account": account_id,
                "exit_id": exit_id,
                "kind": event_kind,
                "canary": canary,
            }
        )
        return True

    def report_event_nowait(
        self,
        *,
        account_id: str,
        exit_id: str,
        event_kind: str,
        canary: str,
    ) -> None:
        self.events.append(
            {
                "account": account_id,
                "exit_id": exit_id,
                "kind": event_kind,
                "canary": canary,
            }
        )


class _FakeProbe:
    def __init__(
        self,
        outcome: str = "inconclusive",
        *,
        gate: asyncio.Event | None = None,
        started: asyncio.Event | None = None,
    ) -> None:
        self.outcome = outcome
        self.gate = gate
        self.started = started
        self.calls: list[tuple[str, str]] = []

    async def probe(self, *, socks_url: str, exit_id: str) -> CanaryVerdict:
        self.calls.append((socks_url, exit_id))
        if self.started is not None:
            self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        return CanaryVerdict(cast(Any, self.outcome), "fake")


def _assignment() -> ExitAssignment:
    return ExitAssignment(
        exit_id="exit-1",
        socks_url="socks5h://172.17.0.1:40001",
        generation=1,
        ttl_s=300.0,
    )


def _region_error() -> AuthError:
    error = classify_error(403, '[7,"Region not supported."]')
    assert isinstance(error, AuthError)
    assert error.region_restricted is True
    return error


def _pipeline(
    manager: FakeManager,
    arbitration: ExitArbitration | None,
) -> RoutingPipeline:
    return RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {"max_switches": 2},
        exit_arbitration=arbitration,
    )


def _arbitration(
    probe: _FakeProbe,
    resolver: _FakeResolver | None = None,
    *,
    clock: Any = None,
) -> tuple[ExitArbitration, _FakeResolver]:
    fake_resolver = resolver or _FakeResolver(_assignment())
    if clock is None:
        arbitration = ExitArbitration(
            resolver=cast(Any, fake_resolver), canary_probe=probe
        )
    else:
        arbitration = ExitArbitration(
            resolver=cast(Any, fake_resolver), canary_probe=probe, clock=clock
        )
    return arbitration, fake_resolver


# ------------------------------------------------------------- classification


def test_region_classification_priority() -> None:
    region = classify_error(403, '[7,"Region not supported."]')
    assert isinstance(region, AuthError)
    assert region.region_restricted is True
    assert region.verification_required is False

    available = classify_error(
        403,
        '[7,"User location is not supported for the API use. Available-regions: US"]',
    )
    assert isinstance(available, AuthError)
    assert available.region_restricted is True

    verify = classify_error(
        403,
        '[7,"Region not supported. Please verify-account at '
        'aistudio.google.com/verify-account"]',
    )
    assert isinstance(verify, AuthError)
    assert verify.verification_required is True
    assert verify.region_restricted is False

    plain = classify_error(403, "permission denied")
    assert isinstance(plain, AuthError)
    assert plain.region_restricted is False

    not_403 = classify_error(400, "Region not supported")
    assert not isinstance(not_403, AuthError)
    assert is_region_restricted_message("REGION NOT SUPPORTED")

    # Only the 403 classifier sets the flag: a signer/login AuthError whose text
    # happens to mention a region must not hijack the exit pool.
    direct = AuthError("Region not supported")
    assert direct.region_restricted is False


# -------------------------------------------------------------- pipeline path


@pytest.mark.asyncio
async def test_off_mode_never_arbitrates_region_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aistudio_api.infrastructure.exit_pool import reset_exit_resolver

    monkeypatch.delenv("AISTUDIO_EXIT_MODE", raising=False)
    reset_exit_resolver()
    manager = _RestartManager([make_instance("inst-1", account_id="acc-1")])
    pipeline = RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {"max_switches": 2},
    )
    assert pipeline._exit_arbitration is None  # type: ignore[attr-defined]

    client = _TrackingClient()
    outcome = await pipeline.handle_error(
        make_context([make_instance("inst-1", account_id="acc-1")]),
        make_attempt(client, instance_id="inst-1", account_id="acc-1"),
        _region_error(),
        has_yielded_data=False,
    )
    # Existing 403 behavior: same-account re-sign retry, no exit switch.
    assert outcome == "retry_same"
    assert manager.restart_calls == []


@pytest.mark.asyncio
async def test_exit_bad_reports_invalidates_and_rebuilds_slot() -> None:
    probe = _FakeProbe("exit_bad")
    arbitration, resolver = _arbitration(probe)
    manager = _RestartManager([make_instance("inst-1", account_id="acc-1")])
    pipeline = _pipeline(manager, arbitration)
    context = make_context([make_instance("inst-1", account_id="acc-1")])
    client = _TrackingClient()

    outcome = await pipeline.handle_error(
        context,
        make_attempt(client, instance_id="inst-1", account_id="acc-1"),
        _region_error(),
        has_yielded_data=False,
    )

    assert outcome == "switch"
    assert probe.calls == [("socks5h://172.17.0.1:40001", "exit-1")]
    assert manager.restart_calls == ["inst-1"]
    assert resolver.invalidated == []
    assert resolver.invalidated_exits == ["exit-1"]
    assert resolver.events == [
        {
            "account": "acc-1",
            "exit_id": "exit-1",
            "kind": "region_403",
            "canary": "exit_bad",
        }
    ]
    assert context.exit_rebuild_retried is True
    assert "inst-1" not in context.attempted_instances
    assert context.failed_accounts == set()
    assert client.clear_calls == 1


@pytest.mark.asyncio
async def test_account_bad_event_kind_override_reports_verify_account() -> None:
    probe = _FakeProbe("account_bad")
    arbitration, resolver = _arbitration(probe)

    outcome = await arbitration.adjudicate(
        account_id="acc-1",
        kind="chat",
        account_bad_event_kind="verify_account",
    )

    assert outcome == "account_bad"
    assert resolver.events == [
        {
            "account": "acc-1",
            "exit_id": "exit-1",
            "kind": "verify_account",
            "canary": "account_bad",
        }
    ]
    assert resolver.invalidated_exits == []


def test_default_arbitration_registry_resets() -> None:
    reset_default_exit_arbitration()
    assert get_default_exit_arbitration() is None

    resolver = SimpleNamespace(config=SimpleNamespace(canary_account=""))
    built = build_exit_arbitration(resolver=cast(Any, resolver))
    assert built is not None
    # Explicit-resolver builds never replace the process-wide instance.
    assert get_default_exit_arbitration() is None


def test_default_arbitration_registered_when_built_without_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2-4: production wiring (resolver=None) must register the default."""
    reset_default_exit_arbitration()
    assert get_default_exit_arbitration() is None
    resolver = SimpleNamespace(config=SimpleNamespace(canary_account=""))
    monkeypatch.setattr(
        "aistudio_api.application.exit_arbitration.get_exit_resolver",
        lambda: resolver,
    )

    built = build_exit_arbitration()

    assert built is not None
    assert get_default_exit_arbitration() is built


@pytest.mark.asyncio
async def test_account_bad_keeps_exit_and_uses_existing_switch_logic() -> None:
    probe = _FakeProbe("account_bad")
    arbitration, resolver = _arbitration(probe)
    manager = _RestartManager([make_instance("inst-1", account_id="acc-1")])
    pipeline = _pipeline(manager, arbitration)

    outcome = await pipeline.handle_error(
        make_context([make_instance("inst-1", account_id="acc-1")]),
        make_attempt(_TrackingClient(), instance_id="inst-1", account_id="acc-1"),
        _region_error(),
        has_yielded_data=False,
    )

    assert outcome == "retry_same"
    assert manager.restart_calls == []
    assert resolver.invalidated == []
    assert resolver.events[0]["canary"] == "account_bad"


@pytest.mark.asyncio
async def test_inconclusive_keeps_exit_and_uses_existing_switch_logic() -> None:
    probe = _FakeProbe("inconclusive")
    arbitration, resolver = _arbitration(probe)
    manager = _RestartManager([make_instance("inst-1", account_id="acc-1")])
    pipeline = _pipeline(manager, arbitration)

    outcome = await pipeline.handle_error(
        make_context([make_instance("inst-1", account_id="acc-1")]),
        make_attempt(_TrackingClient(), instance_id="inst-1", account_id="acc-1"),
        _region_error(),
        has_yielded_data=False,
    )

    assert outcome == "retry_same"
    assert manager.restart_calls == []
    assert resolver.invalidated == []
    assert resolver.events[0]["canary"] == "inconclusive"


@pytest.mark.asyncio
async def test_yielded_data_never_arbitrates() -> None:
    probe = _FakeProbe("exit_bad")
    arbitration, _resolver = _arbitration(probe)
    manager = _RestartManager([make_instance("inst-1", account_id="acc-1")])
    pipeline = _pipeline(manager, arbitration)

    with pytest.raises(AuthError):
        await pipeline.handle_error(
            make_context([make_instance("inst-1", account_id="acc-1")]),
            make_attempt(_TrackingClient(), instance_id="inst-1", account_id="acc-1"),
            _region_error(),
            has_yielded_data=True,
        )
    assert probe.calls == []
    assert manager.restart_calls == []


@pytest.mark.asyncio
async def test_count_tokens_context_never_arbitrates() -> None:
    probe = _FakeProbe("exit_bad")
    arbitration, _resolver = _arbitration(probe)
    manager = _RestartManager([make_instance("inst-1", account_id="acc-1")])
    pipeline = _pipeline(manager, arbitration)
    context = make_context([make_instance("inst-1", account_id="acc-1")])
    context.record_attempts = False

    outcome = await pipeline.handle_error(
        context,
        make_attempt(_TrackingClient(), instance_id="inst-1", account_id="acc-1"),
        _region_error(),
        has_yielded_data=False,
    )
    assert outcome == "retry_same"
    assert probe.calls == []
    assert manager.restart_calls == []


@pytest.mark.asyncio
async def test_event_report_failure_does_not_block_or_crash() -> None:
    probe = _FakeProbe("exit_bad")
    resolver = _FakeResolver(_assignment())
    resolver.report_error = RuntimeError("events endpoint down")
    arbitration, _resolver = _arbitration(probe, resolver)
    manager = _RestartManager([make_instance("inst-1", account_id="acc-1")])
    pipeline = _pipeline(manager, arbitration)

    outcome = await pipeline.handle_error(
        make_context([make_instance("inst-1", account_id="acc-1")]),
        make_attempt(_TrackingClient(), instance_id="inst-1", account_id="acc-1"),
        _region_error(),
        has_yielded_data=False,
    )
    # Arbitration failed internally, so the request falls back to the normal
    # 403 path instead of propagating the reporting error.
    assert outcome == "retry_same"
    assert manager.restart_calls == []


@pytest.mark.asyncio
async def test_region_403_tier_mismatch_raises_before_canary() -> None:
    probe = _FakeProbe("exit_bad")
    arbitration, resolver = _arbitration(probe)
    manager = _RestartManager(
        [make_instance("inst-1", account_id="acc-1", tier="Free")]
    )
    pipeline = _pipeline(manager, arbitration)
    context = make_context(
        [make_instance("inst-1", account_id="acc-1", tier="Free")],
        paid=True,
        required_tiers=("Pro", "Ultra"),
    )

    with pytest.raises(TierRequiredError):
        await pipeline.handle_error(
            context,
            make_attempt(_TrackingClient(), tier="Free"),
            _region_error(),
            has_yielded_data=False,
        )

    # P2-11: the local tier gate must reject before spending a probe/rebuild.
    assert probe.calls == []
    assert manager.restart_calls == []
    assert resolver.events == []


@pytest.mark.asyncio
async def test_exit_bad_busy_slot_degrades_without_restart() -> None:
    probe = _FakeProbe("exit_bad")
    arbitration, resolver = _arbitration(probe)
    manager = _RestartManager([make_instance("inst-1", account_id="acc-1")])
    manager.active["inst-1"] = 1
    pipeline = _pipeline(manager, arbitration)
    context = make_context([make_instance("inst-1", account_id="acc-1")])

    with pytest.raises(AuthError):
        await pipeline.handle_error(
            context,
            make_attempt(_TrackingClient(), instance_id="inst-1", account_id="acc-1"),
            _region_error(),
            has_yielded_data=False,
        )

    assert manager.restart_calls == []
    assert context.exit_rebuild_retried is True
    assert resolver.invalidated_exits == ["exit-1"]


@pytest.mark.asyncio
async def test_exit_bad_restart_failure_degrades_to_original_error() -> None:
    probe = _FakeProbe("exit_bad")
    arbitration, resolver = _arbitration(probe)
    manager = _FailingRestartManager([make_instance("inst-1", account_id="acc-1")])
    pipeline = _pipeline(manager, arbitration)
    context = make_context([make_instance("inst-1", account_id="acc-1")])

    with pytest.raises(AuthError):
        await pipeline.handle_error(
            context,
            make_attempt(_TrackingClient(), instance_id="inst-1", account_id="acc-1"),
            _region_error(),
            has_yielded_data=False,
        )

    assert manager.restart_calls == ["inst-1"]
    assert context.exit_rebuild_retried is True
    assert resolver.invalidated_exits == ["exit-1"]


@pytest.mark.asyncio
async def test_second_exit_bad_does_not_rebuild_again() -> None:
    now = [0.0]
    probe = _FakeProbe("exit_bad")
    arbitration, resolver = _arbitration(probe, clock=lambda: now[0])
    manager = _RestartManager([make_instance("inst-1", account_id="acc-1")])
    pipeline = _pipeline(manager, arbitration)
    context = make_context([make_instance("inst-1", account_id="acc-1")])

    first = await pipeline.handle_error(
        context,
        make_attempt(_TrackingClient(), instance_id="inst-1", account_id="acc-1"),
        _region_error(),
        has_yielded_data=False,
    )
    assert first == "switch"
    assert manager.restart_calls == ["inst-1"]

    now[0] += 601.0
    with pytest.raises(AuthError):
        await pipeline.handle_error(
            context,
            make_attempt(_TrackingClient(), instance_id="inst-1", account_id="acc-1"),
            _region_error(),
            has_yielded_data=False,
        )

    # The per-request rebuild budget is single-use; the second exit_bad only
    # reports/invalidates and then fails the request.
    assert manager.restart_calls == ["inst-1"]
    assert len(probe.calls) == 2
    assert resolver.invalidated_exits == ["exit-1", "exit-1"]


@pytest.mark.asyncio
async def test_region_cooldown_hit_skips_probe_at_pipeline_level() -> None:
    now = [0.0]
    probe = _FakeProbe("inconclusive")
    arbitration, _resolver = _arbitration(probe, clock=lambda: now[0])
    manager = _RestartManager([make_instance("inst-1", account_id="acc-1")])
    pipeline = _pipeline(manager, arbitration)
    context = make_context([make_instance("inst-1", account_id="acc-1")])

    for _ in range(2):
        await pipeline.handle_error(
            context,
            make_attempt(_TrackingClient(), instance_id="inst-1", account_id="acc-1"),
            _region_error(),
            has_yielded_data=False,
        )

    assert len(probe.calls) == 1


@pytest.mark.asyncio
async def test_media_or_non_generation_403_never_arbitrates() -> None:
    probe = _FakeProbe("exit_bad")
    arbitration, _resolver = _arbitration(probe)
    manager = _RestartManager([make_instance("inst-1", account_id="acc-1")])
    pipeline = _pipeline(manager, arbitration)

    # A media/upload-flavoured 403 without the region marker is an ordinary
    # 403, never an exit arbitration trigger.
    media_error = classify_error(403, "media upload failed: permission denied")
    assert isinstance(media_error, AuthError)
    assert media_error.region_restricted is False

    await pipeline.handle_error(
        make_context([make_instance("inst-1", account_id="acc-1")]),
        make_attempt(_TrackingClient(), instance_id="inst-1", account_id="acc-1"),
        media_error,
        has_yielded_data=False,
    )
    assert probe.calls == []

    # The only non-generation caller (countTokens) clears ``record_attempts``;
    # even a region-classified 403 must not probe in that context.
    count_context = make_context([make_instance("inst-1", account_id="acc-1")])
    count_context.record_attempts = False
    await pipeline.handle_error(
        count_context,
        make_attempt(_TrackingClient(), instance_id="inst-1", account_id="acc-1"),
        _region_error(),
        has_yielded_data=False,
    )
    assert probe.calls == []


# -------------------------------------------------- cooldown / concurrency / gate


@pytest.mark.asyncio
async def test_probe_cooldown_is_per_exit() -> None:
    now = [0.0]
    probe = _FakeProbe("inconclusive")
    arbitration, _resolver = _arbitration(probe, clock=lambda: now[0])

    first = await arbitration.adjudicate(account_id="acc-1", kind="chat")
    second = await arbitration.adjudicate(account_id="acc-1", kind="chat")
    assert first == "inconclusive"
    assert second == "inconclusive"
    assert len(probe.calls) == 1

    now[0] += 601.0
    assert await arbitration.adjudicate(account_id="acc-1", kind="chat") == (
        "inconclusive"
    )
    assert len(probe.calls) == 2


@pytest.mark.asyncio
async def test_probe_global_concurrency_is_one() -> None:
    gate = asyncio.Event()
    started = asyncio.Event()
    probe = _FakeProbe("exit_bad", gate=gate, started=started)
    arbitration, _resolver = _arbitration(probe)

    first = asyncio.create_task(arbitration.adjudicate(account_id="acc-1", kind="chat"))
    await started.wait()
    second = await arbitration.adjudicate(account_id="acc-2", kind="chat")
    assert second == "inconclusive"
    assert len(probe.calls) == 1

    gate.set()
    assert await first == "exit_bad"
    assert len(probe.calls) == 1


@pytest.mark.asyncio
async def test_no_assignment_or_no_canary_is_inconclusive() -> None:
    no_assignment = _FakeResolver(None)
    probe = _FakeProbe("exit_bad")
    arbitration, _resolver = _arbitration(probe, no_assignment)
    assert await arbitration.adjudicate(account_id="acc-1", kind="chat") == (
        "inconclusive"
    )
    assert probe.calls == []

    unused_resolver = _FakeResolver(_assignment())
    no_probe = ExitArbitration(resolver=cast(Any, unused_resolver), canary_probe=None)
    assert await no_probe.adjudicate(account_id="acc-1", kind="chat") == (
        "inconclusive"
    )
    assert unused_resolver.events == []


# --------------------------------------------------------------- canary probe


class _FakeCanarySession:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        self.requests: list[tuple[object, str]] = []
        self.closed = False

    async def send_hooked_request(self, *, body: object, url: str) -> tuple[int, bytes]:
        self.requests.append((body, url))
        return self.status, self.body

    async def close(self) -> None:
        self.closed = True


class _FakeCanaryRuntime:
    def __init__(self, session: _FakeCanarySession) -> None:
        self.session = session
        self.calls: list[dict[str, object]] = []
        self.auth_bytes: bytes | None = None

    async def create_canary_session(
        self,
        *,
        account_id: str,
        auth_file: str,
        proxy: str,
        auth_user: str = "0",
        tier: str = "Free",
    ) -> _FakeCanarySession:
        from pathlib import Path

        self.calls.append(
            {
                "account_id": account_id,
                "auth_file": auth_file,
                "proxy": proxy,
                "auth_user": auth_user,
                "tier": tier,
            }
        )
        self.auth_bytes = Path(auth_file).read_bytes()
        return self.session


class _FakeStore:
    def __init__(self, auth_path: object, *, auth_user: object = None) -> None:
        self._auth_path = auth_path
        self._auth_user = auth_user

    def get_auth_path_optional(
        self, account_id: str, *, require_exists: bool = True
    ) -> object:
        del account_id, require_exists
        return self._auth_path

    def get_account(self, account_id: str) -> object:
        del account_id
        if self._auth_user is None:
            raise KeyError("missing account")
        return SimpleNamespace(auth_user=self._auth_user)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, b"[[]]", "account_bad"),
        (403, b'[7,"Region not supported."]', "exit_bad"),
        (403, b'[7,"permission denied"]', "inconclusive"),
        (500, b"boom", "inconclusive"),
    ],
)
async def test_signer_canary_probe_uses_signer_transport_and_temp_auth(
    tmp_path: object, status: int, body: bytes, expected: str
) -> None:
    from pathlib import Path

    from aistudio_api.application.exit_arbitration import SignerCanaryProbe

    base = cast(Path, tmp_path)
    real_auth = base / "auth.json"
    real_auth.write_bytes(b'{"cookies":[{"name":"SAPISID","value":"secret"}]}')

    session = _FakeCanarySession(status, body)
    runtime = _FakeCanaryRuntime(session)
    probe = SignerCanaryProbe(
        runtime_getter=lambda: runtime,
        account_store=_FakeStore(real_auth),
        canary_account="canary-acc",
        timeout_s=1.0,
    )

    verdict = await probe.probe(
        socks_url="socks5h://172.17.0.1:40001", exit_id="exit-1"
    )

    assert verdict.outcome == expected
    assert len(runtime.calls) == 1
    call = runtime.calls[0]
    assert call["account_id"] == "canary-acc"
    assert call["proxy"] == "socks5h://172.17.0.1:40001"
    assert call["auth_user"] == "0"
    assert call["auth_file"] != str(real_auth)
    assert runtime.auth_bytes == real_auth.read_bytes()
    assert len(session.requests) == 1
    assert session.requests[0][0] == "[]"
    assert session.requests[0][1] == LIST_MODELS_URL
    assert session.closed is True
    assert real_auth.read_bytes() == (
        b'{"cookies":[{"name":"SAPISID","value":"secret"}]}'
    )


@pytest.mark.asyncio
async def test_signer_canary_probe_inconclusive_without_credentials() -> None:
    from aistudio_api.application.exit_arbitration import SignerCanaryProbe

    class _MissingStore:
        def get_auth_path_optional(
            self, account_id: str, *, require_exists: bool = True
        ) -> str | None:
            del account_id, require_exists
            return None

    probe = SignerCanaryProbe(
        runtime_getter=lambda: _FakeCanaryRuntime(_FakeCanarySession(200, b"[]")),
        account_store=_MissingStore(),
        canary_account="canary-acc",
    )
    verdict = await probe.probe(socks_url="socks5h://172.17.0.1:40001", exit_id="e")
    assert verdict.outcome == "inconclusive"


@pytest.mark.asyncio
@pytest.mark.parametrize("as_str", [False, True])
async def test_signer_canary_probe_accepts_path_and_str_store(
    tmp_path: object, as_str: bool
) -> None:
    """P1-1: the real store returns ``Path``; the probe must not drop it."""
    from pathlib import Path

    from aistudio_api.application.exit_arbitration import SignerCanaryProbe

    base = cast(Path, tmp_path)
    real_auth = base / "auth.json"
    real_auth.write_bytes(b'{"cookies":[]}')
    store_path: object = str(real_auth) if as_str else real_auth
    session = _FakeCanarySession(200, b"[[]]")
    runtime = _FakeCanaryRuntime(session)
    probe = SignerCanaryProbe(
        runtime_getter=lambda: runtime,
        account_store=_FakeStore(store_path),
        canary_account="canary-acc",
        timeout_s=1.0,
    )

    assert probe._auth_file() is not None  # type: ignore[attr-defined]
    verdict = await probe.probe(
        socks_url="socks5h://172.17.0.1:40001", exit_id="exit-1"
    )

    assert verdict.outcome == "account_bad"
    assert len(runtime.calls) == 1
    assert session.requests == [("[]", LIST_MODELS_URL)]


@pytest.mark.asyncio
async def test_signer_canary_probe_uses_account_auth_user(
    tmp_path: object,
) -> None:
    from pathlib import Path

    from aistudio_api.application.exit_arbitration import SignerCanaryProbe

    base = cast(Path, tmp_path)
    real_auth = base / "auth.json"
    real_auth.write_bytes(b'{"cookies":[]}')
    runtime = _FakeCanaryRuntime(_FakeCanarySession(200, b"[[]]"))
    probe = SignerCanaryProbe(
        runtime_getter=lambda: runtime,
        account_store=_FakeStore(real_auth, auth_user="1"),
        canary_account="canary-acc",
        timeout_s=1.0,
    )

    assert await probe.probe(
        socks_url="socks5h://172.17.0.1:40001", exit_id="exit-1"
    ) == CanaryVerdict("account_bad", "list_models_ok")
    assert runtime.calls[0]["auth_user"] == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (
            403,
            (
                b'[7,"Region not supported. Please verify-account at '
                b'aistudio.google.com/verify-account"]'
            ),
            "verify-account",
        ),
        (401, b"unauthorized", "status_401"),
        (500, b"boom", "status_500"),
    ],
)
async def test_signer_canary_pollution_latch_stops_further_probes(
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
    status: int,
    body: bytes,
    reason: str,
) -> None:
    from pathlib import Path

    from aistudio_api.application.exit_arbitration import SignerCanaryProbe

    base = cast(Path, tmp_path)
    real_auth = base / "auth.json"
    real_auth.write_bytes(b'{"cookies":[{"name":"SAPISID","value":"secret"}]}')
    runtime = _FakeCanaryRuntime(_FakeCanarySession(status, body))
    probe = SignerCanaryProbe(
        runtime_getter=lambda: runtime,
        account_store=_FakeStore(real_auth),
        canary_account="canary-acc",
        timeout_s=1.0,
    )

    with caplog.at_level("WARNING"):
        first = await probe.probe(
            socks_url="socks5h://172.17.0.1:40001", exit_id="exit-1"
        )
        second = await probe.probe(
            socks_url="socks5h://172.17.0.1:40001", exit_id="exit-2"
        )

    assert first.outcome == "inconclusive"
    assert first.detail == f"canary-contaminated:{reason}"
    assert second.outcome == "inconclusive"
    assert second.detail == f"canary-contaminated:{reason}"
    # The contaminated latch must stop before building another session.
    assert len(runtime.calls) == 1
    messages = [record.getMessage() for record in caplog.records]
    assert any("canary-acc" in message and reason in message for message in messages)
    assert all("secret" not in message for message in messages)


@pytest.mark.asyncio
async def test_signer_canary_probe_skips_account_bound_to_a_slot(
    tmp_path: object,
) -> None:
    from pathlib import Path

    from aistudio_api.application.exit_arbitration import SignerCanaryProbe

    base = cast(Path, tmp_path)
    real_auth = base / "auth.json"
    real_auth.write_bytes(b'{"cookies":[]}')
    runtime = _FakeCanaryRuntime(_FakeCanarySession(200, b"[[]]"))
    probe = SignerCanaryProbe(
        runtime_getter=lambda: runtime,
        account_store=_FakeStore(real_auth),
        canary_account="canary-acc",
        timeout_s=1.0,
        slot_account_ids_getter=lambda: {"canary-acc", "other"},
    )

    verdict = await probe.probe(
        socks_url="socks5h://172.17.0.1:40001", exit_id="exit-1"
    )

    assert verdict == CanaryVerdict("inconclusive", "canary-in-use")
    assert runtime.calls == []


def test_build_exit_arbitration_warns_when_canary_in_slot(
    caplog: pytest.LogCaptureFixture,
) -> None:
    resolver = SimpleNamespace(config=SimpleNamespace(canary_account="canary-acc"))
    with caplog.at_level("WARNING"):
        arbitration = build_exit_arbitration(
            resolver=cast(Any, resolver),
            runtime_getter=lambda: _FakeCanaryRuntime(_FakeCanarySession(200, b"[[]]")),
            account_store=SimpleNamespace(),
            slot_account_ids_getter=lambda: {"canary-acc"},
        )
    assert arbitration is not None
    assert any("同时被实例槽位占用" in record.getMessage() for record in caplog.records)


def test_build_exit_arbitration_warns_when_slot_ids_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    resolver = SimpleNamespace(config=SimpleNamespace(canary_account="canary-acc"))
    with caplog.at_level("WARNING"):
        arbitration = build_exit_arbitration(
            resolver=cast(Any, resolver),
            runtime_getter=lambda: _FakeCanaryRuntime(_FakeCanarySession(200, b"[[]]")),
            account_store=SimpleNamespace(),
        )
    assert arbitration is not None
    assert any(
        "无法读取实例槽位占用" in record.getMessage() for record in caplog.records
    )


def test_build_exit_arbitration_is_none_when_mode_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aistudio_api.infrastructure.exit_pool import reset_exit_resolver

    for name in (
        "AISTUDIO_EXIT_MODE",
        "AISTUDIO_EXIT_ENDPOINT",
        "AISTUDIO_EXIT_TOKEN",
        "AISTUDIO_EXIT_HMAC_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    reset_exit_resolver()

    assert (
        build_exit_arbitration(
            runtime_getter=lambda: SimpleNamespace(),
            account_store=SimpleNamespace(),
        )
        is None
    )
