"""Region-403 canary arbitration for the external exit pool.

This is deliberately separate from ``routing_pipeline``: the pipeline decides
*when* to ask (external mode, real generation, no body yielded yet) and *what*
to do with the answer, while this module owns the probe mechanics:

* one lightweight authenticated RPC (``ListModels``) through the same signer
  transport as real generation, on the same exit;
* a global concurrency of one probe and a per-exit cooldown;
* the ``POST /v1/events`` report and the binding invalidation on ``exit_bad``.

Nothing here runs when ``AISTUDIO_EXIT_MODE`` is not ``external``: the module
is only constructed through :func:`build_exit_arbitration` and returns ``None``
in that case, so the default request path is untouched.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from aistudio_api.domain.errors import (
    is_account_verification_required_message,
    is_region_restricted_message,
)
from aistudio_api.infrastructure.exit_pool import get_exit_resolver
from aistudio_api.infrastructure.exit_pool.resolver import ExitAssignment, ExitResolver
from aistudio_api.infrastructure.gateway.public_web_constants import LIST_MODELS_URL
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("exit-arbitration")

CanaryOutcome = Literal["exit_bad", "account_bad", "inconclusive"]

DEFAULT_CANARY_TIMEOUT_S = 9.0
DEFAULT_PROBE_COOLDOWN_S = 600.0


@dataclass(frozen=True)
class CanaryVerdict:
    """One canary probe result plus a bounded, log-safe detail."""

    outcome: CanaryOutcome
    detail: str = ""


class CanaryProbe(Protocol):
    async def probe(self, *, socks_url: str, exit_id: str) -> CanaryVerdict: ...


def _body_text(body: object) -> str:
    if isinstance(body, (bytes, bytearray)):
        return bytes(body).decode("utf-8", errors="replace")
    return str(body or "")


class SignerCanaryProbe:
    """Probe one exit with the canary account through the signer transport.

    The canary auth file is copied to a throwaway temp directory first so the
    rolling ``Set-Cookie`` write-back of the probe session can never mutate the
    real account credentials.

    ``ListModels`` is the same lightweight RPC production model discovery uses
    (``model_discovery._fetch_via_signer``): it goes through
    ``BrowserlessSession.send_hooked_request`` → ``SignerClient.send_rpc`` and
    therefore reuses the signer's cookie jar, fingerprint, proxy egress and
    Firefox 152 request shape.  Unlike ``GenerateContent`` it needs no WAA
    proof: ``generate_snapshot``/``mint_proof`` is only required for the
    generation body template, and the signer's ``SendProtected`` does not
    inject a proof itself.  The probe therefore exercises the real transport
    without needing a generation payload.
    """

    def __init__(
        self,
        *,
        runtime_getter: Callable[[], object | None],
        account_store: object,
        canary_account: str,
        timeout_s: float = DEFAULT_CANARY_TIMEOUT_S,
        slot_account_ids_getter: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self._runtime_getter = runtime_getter
        self._account_store = account_store
        self._canary_account = canary_account
        self._timeout_s = max(1.0, timeout_s)
        self._slot_account_ids_getter = slot_account_ids_getter
        # Process-local contamination latch: once the canary account gets an
        # upstream response that says it is not a clean probe identity (verify
        # account, 401, unexpected status) we stop probing for this process.
        self._contaminated_reason: str | None = None

    def _auth_file(self) -> str | None:
        getter = getattr(self._account_store, "get_auth_path_optional", None)
        if not callable(getter):
            return None
        try:
            try:
                value = getter(self._canary_account, require_exists=True)
            except TypeError:
                value = getter(self._canary_account)
        except Exception as exc:
            logger.warning("[exit] 读取 canary 凭据失败: %s", type(exc).__name__)
            return None
        # Real ``AccountStore.get_auth_path_optional`` returns ``Path | None``;
        # only ``str`` was accepted before, which silently disabled the probe.
        if isinstance(value, Path):
            value = str(value)
        return value if isinstance(value, str) and value else None

    def _canary_auth_user(self) -> str:
        """Return the canary account's real ``u/<n>`` index, defaulting to "0"."""
        getter = getattr(self._account_store, "get_account", None)
        if not callable(getter):
            return "0"
        try:
            account = getter(self._canary_account)
        except Exception as exc:
            logger.debug(
                "[exit] 读取 canary 账号元数据失败，auth_user 回退 0: %s",
                type(exc).__name__,
            )
            return "0"
        value = getattr(account, "auth_user", None)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, int) and value >= 0:
            return str(value)
        return "0"

    def _slot_conflict(self) -> bool:
        """True when the canary account is also bound to a scheduling slot."""
        getter = self._slot_account_ids_getter
        if getter is None:
            return False
        try:
            raw = getter()
        except Exception as exc:
            logger.warning(
                "[exit] 无法读取实例槽位账号集合，canary 互斥校验跳过一次: %s",
                type(exc).__name__,
            )
            return False
        if not isinstance(raw, (list, tuple, set, frozenset)):
            return False
        return self._canary_account in {str(item) for item in raw if item}

    async def probe(self, *, socks_url: str, exit_id: str) -> CanaryVerdict:
        if self._contaminated_reason is not None:
            return CanaryVerdict(
                "inconclusive", f"canary-contaminated:{self._contaminated_reason}"
            )
        if self._slot_conflict():
            # A slot also owns this account's signer worker; probing would
            # release/steal it.  Operators must keep the canary out of slots.
            logger.warning(
                "[exit] canary 账号仍被实例槽位占用，跳过探针以免抢占 signer worker "
                "account=%s exit=%s",
                self._canary_account,
                exit_id,
            )
            return CanaryVerdict("inconclusive", "canary-in-use")
        runtime = self._runtime_getter()
        builder = getattr(runtime, "create_canary_session", None)
        if not callable(builder):
            return CanaryVerdict("inconclusive", "no-browserless-runtime")
        auth_file = self._auth_file()
        if auth_file is None:
            return CanaryVerdict("inconclusive", "canary-credentials-missing")

        workdir = Path(tempfile.mkdtemp(prefix="aistudio-exit-canary-"))
        probe_auth = workdir / "auth.json"
        session: object | None = None
        try:
            shutil.copyfile(auth_file, probe_auth)
            session = await cast(
                Awaitable[object],
                builder(
                    account_id=self._canary_account,
                    auth_file=str(probe_auth),
                    proxy=socks_url,
                    auth_user=self._canary_auth_user(),
                ),
            )
            sender = getattr(session, "send_hooked_request", None)
            if not callable(sender):
                return CanaryVerdict("inconclusive", "no-signer-transport")
            status, body = await asyncio.wait_for(
                cast(
                    Awaitable[tuple[object, object]],
                    sender(body="[]", url=LIST_MODELS_URL),
                ),
                timeout=self._timeout_s,
            )
        except TimeoutError:
            return CanaryVerdict("inconclusive", "timeout")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return CanaryVerdict("inconclusive", type(exc).__name__)
        finally:
            if session is not None:
                closer = getattr(session, "close", None)
                if callable(closer):
                    try:
                        await cast(Awaitable[Any], closer())
                    except Exception as exc:
                        logger.debug(
                            "[exit] canary 会话关闭失败 exit=%s: %s",
                            exit_id,
                            type(exc).__name__,
                        )
            shutil.rmtree(workdir, ignore_errors=True)

        code = int(status) if isinstance(status, int) else 0
        if code == 200:
            return CanaryVerdict("account_bad", "list_models_ok")
        text = _body_text(body)
        # verify-account wins over region: a response that says both must never
        # be judged as an exit-level failure.
        if is_account_verification_required_message(text):
            return self._mark_contaminated("verify-account", exit_id)
        if code == 403 and is_region_restricted_message(text):
            return CanaryVerdict("exit_bad", "list_models_region_403")
        return self._mark_contaminated(f"status_{code}", exit_id)

    def _mark_contaminated(self, reason: str, exit_id: str) -> CanaryVerdict:
        """Latch the canary as polluted and stop probing for this process."""
        self._contaminated_reason = reason
        logger.warning(
            "[exit] canary 返回非区域失败，本进程内标记 canary 账号受污染并停止再探测 "
            "account=%s exit=%s reason=%s",
            self._canary_account,
            exit_id,
            reason,
        )
        return CanaryVerdict("inconclusive", f"canary-contaminated:{reason}")


class ExitArbitration:
    """Adjudicate a region 403 into ``exit_bad`` / ``account_bad`` / inconclusive."""

    def __init__(
        self,
        *,
        resolver: ExitResolver,
        canary_probe: CanaryProbe | None,
        clock: Callable[[], float] = time.monotonic,
        probe_cooldown_s: float = DEFAULT_PROBE_COOLDOWN_S,
    ) -> None:
        self._resolver = resolver
        self._canary_probe = canary_probe
        self._clock = clock
        self._cooldown_s = max(0.0, probe_cooldown_s)
        self._cooldowns: dict[str, float] = {}
        self._probe_lock = asyncio.Lock()

    @property
    def resolver(self) -> ExitResolver:
        return self._resolver

    def _in_cooldown(self, exit_id: str) -> bool:
        until = self._cooldowns.get(exit_id)
        return until is not None and until > self._clock()

    async def adjudicate(self, *, account_id: str, kind: str) -> CanaryOutcome:
        """Classify one region 403 for an account's currently bound exit."""
        assignment = self._resolver.assignment_for(
            account_id, kind=kind, include_expired=True
        )
        if assignment is None:
            # The request did not actually run on an external exit (degraded
            # fallback to the global proxy); there is no exit to blame.
            logger.info(
                "[exit] region 403 但账号无外部出口绑定，按 inconclusive 处理 kind=%s",
                kind,
            )
            return "inconclusive"
        if self._canary_probe is None:
            logger.warning(
                "[exit] 未配置 canary 账号，region 403 无法仲裁，按 inconclusive 处理 "
                "exit=%s",
                assignment.exit_id,
            )
            return "inconclusive"
        if self._in_cooldown(assignment.exit_id):
            logger.info(
                "[exit] 出口处于 canary 冷却期，region 403 按 inconclusive 处理 exit=%s",
                assignment.exit_id,
            )
            return "inconclusive"
        if self._probe_lock.locked():
            logger.info(
                "[exit] canary 探针已在运行（全局并发 1），region 403 按 inconclusive 处理"
            )
            return "inconclusive"

        async with self._probe_lock:
            if self._in_cooldown(assignment.exit_id):
                return "inconclusive"
            verdict = await self._canary_probe.probe(
                socks_url=assignment.socks_url,
                exit_id=assignment.exit_id,
            )
            self._cooldowns[assignment.exit_id] = self._clock() + self._cooldown_s

        return await self._apply(
            account_id=account_id,
            kind=kind,
            assignment=assignment,
            verdict=verdict,
        )

    async def _apply(
        self,
        *,
        account_id: str,
        kind: str,
        assignment: ExitAssignment,
        verdict: CanaryVerdict,
    ) -> CanaryOutcome:
        logger.warning(
            "[exit] canary 仲裁结果=%s exit=%s kind=%s detail=%s",
            verdict.outcome,
            assignment.exit_id,
            kind,
            verdict.detail or "-",
        )
        if verdict.outcome == "exit_bad":
            # Report first so the manager can migrate the binding before the
            # caller rebuilds the slot and resolves a replacement exit.
            await self._resolver.report_event(
                account_id=account_id,
                exit_id=assignment.exit_id,
                event_kind="region_403",
                canary="exit_bad",
            )
            # The manager migrates every account bound to this exit, so drop
            # all of their TTL caches instead of only the reporting account.
            self._resolver.invalidate_exit(assignment.exit_id)
            return "exit_bad"
        if verdict.outcome == "account_bad":
            self._resolver.report_event_nowait(
                account_id=account_id,
                exit_id=assignment.exit_id,
                event_kind="region_403",
                canary="account_bad",
            )
            return "account_bad"
        self._resolver.report_event_nowait(
            account_id=account_id,
            exit_id=assignment.exit_id,
            event_kind="region_403",
            canary="inconclusive",
        )
        return "inconclusive"


def _slot_account_ids(
    getter: Callable[[], Iterable[str]] | None,
) -> set[str] | None:
    if getter is None:
        return None
    try:
        raw = getter()
    except Exception as exc:
        logger.warning(
            "[exit] 读取实例槽位账号集合失败，无法校验 canary 互斥: %s",
            type(exc).__name__,
        )
        return None
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return None
    return {str(item) for item in raw if item}


def build_exit_arbitration(
    *,
    runtime_getter: Callable[[], object | None] | None = None,
    account_store: object | None = None,
    resolver: ExitResolver | None = None,
    slot_account_ids_getter: Callable[[], Iterable[str]] | None = None,
) -> ExitArbitration | None:
    """Build the arbitration service, or ``None`` when the mode is off."""
    active = resolver if resolver is not None else get_exit_resolver()
    if active is None:
        return None
    probe: SignerCanaryProbe | None = None
    canary_account = active.config.canary_account
    if canary_account:
        slot_ids = _slot_account_ids(slot_account_ids_getter)
        if slot_ids is None:
            logger.warning(
                "[exit] 已配置 canary 账号但无法读取实例槽位占用；必须人工确保 "
                "canary 账号不参与实例调度 account=%s",
                canary_account,
            )
        elif canary_account in slot_ids:
            logger.error(
                "[exit] canary 账号同时被实例槽位占用；探针将跳过，必须先把该账号"
                "移出所有槽位 account=%s",
                canary_account,
            )
    if canary_account and runtime_getter is not None and account_store is not None:
        probe = SignerCanaryProbe(
            runtime_getter=runtime_getter,
            account_store=account_store,
            canary_account=canary_account,
            slot_account_ids_getter=slot_account_ids_getter,
        )
    return ExitArbitration(resolver=active, canary_probe=probe)


__all__ = [
    "CanaryOutcome",
    "CanaryProbe",
    "CanaryVerdict",
    "ExitArbitration",
    "SignerCanaryProbe",
    "build_exit_arbitration",
]
