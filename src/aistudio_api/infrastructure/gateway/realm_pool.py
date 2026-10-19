"""Cross-account Realm pool (WP5 phase 2, level B).

A Realm is one isolated Chromium BrowserContext with its own cookie jar, chat
page and (lazy) BuildSurface. It lets two accounts serve requests at the same
time without switching the default session. Realms are created lazily and are
never recycled while they have in-flight work.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage
from aistudio_api.infrastructure.browser.scripts import SNAPSHOT_GENERATE_JS
from aistudio_api.infrastructure.gateway.botguard_bootstrap import (
    bootstrap_botguard_and_template,
)
from aistudio_api.infrastructure.gateway.session_auth import (
    get_auth_user_from_meta,
    load_cookies_to_page,
    navigate_to_aistudio,
    save_browser_cookies,
)
from aistudio_api.infrastructure.gateway.session_state import SessionContext
from aistudio_api.infrastructure.gateway.transport import XHRStreamTransport
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import get_logger

if TYPE_CHECKING:
    from aistudio_api.infrastructure.gateway.build_surface import BuildSurface

log = get_logger("realm")


class RealmCapacityError(RuntimeError):
    """No realm slot is available and no idle realm can be retired."""


class RealmSession:
    """Minimal session surface consumed by capture/streaming services."""

    def __init__(
        self,
        *,
        context: SessionContext,
        transport: XHRStreamTransport,
        realm_id: str,
    ) -> None:
        self._context = context
        self._transport = transport
        self._realm_id = realm_id
        self._snapshot_lock = asyncio.Lock()
        self._in_flight = 0

    @classmethod
    async def bootstrap(
        cls,
        cdp: CDPClient,
        *,
        context_id: str,
        auth_file: str | None,
        auth_user: str | None,
        realm_id: str,
        timeout_s: float = 90.0,
    ) -> RealmSession:
        page = await cdp.create_page_in_context(context_id, block_assets=True)
        target_auth_user = auth_user or get_auth_user_from_meta(auth_file)
        try:
            page.set_liveness_checker(lambda: not page.is_closed())
            await load_cookies_to_page(page, auth_file)
            await navigate_to_aistudio(
                page, auth_user=target_auth_user, auth_file=auth_file
            )

            async def reload_cb(target: CDPPage) -> bool:
                return await load_cookies_to_page(target, auth_file)

            snap_key, base_template = await asyncio.wait_for(
                bootstrap_botguard_and_template(
                    page,
                    reload_cookies_cb=reload_cb,
                    is_proc_alive_cb=lambda: not page.is_closed(),
                ),
                timeout=timeout_s,
            )
        except Exception:
            with suppress(Exception):
                await page.close()
            raise
        context = SessionContext(
            page=page,
            snap_key=snap_key,
            auth_user=target_auth_user,
            auth_file=auth_file,
            base_template=base_template,
        )
        log.info(
            "Realm 就绪: realm=%s account=%s auth_user=%s",
            realm_id,
            auth_file,
            target_auth_user,
        )
        return cls(context=context, transport=XHRStreamTransport(), realm_id=realm_id)

    @property
    def active_context(self) -> SessionContext:
        return self._context

    @property
    def page(self) -> CDPPage:
        return self._context.page

    @property
    def in_flight_count(self) -> int:
        return self._in_flight

    @asynccontextmanager
    async def request_scope(self):
        self._in_flight += 1
        self._context.in_flight += 1
        try:
            yield self._context
        finally:
            self._in_flight = max(0, self._in_flight - 1)
            self._context.decr_in_flight()

    async def ensure_ready(self, timeout_s: float = 60.0) -> SessionContext:
        del timeout_s
        if self._context.page.is_closed():
            raise RuntimeError(f"Realm {self._realm_id} page is closed")
        return self._context

    async def capture_template(self, model: str) -> dict[str, object]:
        base = dict(self._context.base_template)
        raw_body = base.get("body")
        if not isinstance(raw_body, str) or not raw_body:
            raise RuntimeError("realm template body missing")
        normalized = model if model.startswith("models/") else f"models/{model}"
        try:
            if raw_body.startswith('["models/') and "," in raw_body:
                first_comma = raw_body.find(",")
                base["body"] = f'["{normalized}"' + raw_body[first_comma:]
            elif raw_body.startswith("["):
                body_list = json.loads(raw_body)
                if isinstance(body_list, list) and body_list:
                    body_list[0] = normalized
                    base["body"] = json.dumps(body_list, separators=(",", ":"))
        except Exception as exc:
            log.debug("Realm 模板模型改写失败: %s", exc)
        return base

    async def generate_snapshot(self, contents: list[AistudioContent]) -> str:
        hash_parts: list[str] = []
        for content in contents:
            for part in content.parts:
                if part.text is not None:
                    hash_parts.append(str(part.text))
                elif part.inline_data is not None:
                    hash_parts.append(str(part.inline_data[1]))
                elif part.file_id is not None:
                    hash_parts.append(str(part.file_id))
                elif part.executable_code is not None:
                    hash_parts.append(str(part.executable_code[1]))
                elif part.code_execution_result is not None:
                    hash_parts.append(str(part.code_execution_result[1]))
                else:
                    hash_parts.append("")
        content_hash = sha256(" ".join(hash_parts).encode("utf-8")).hexdigest()
        async with self._snapshot_lock:
            for attempt in range(3):
                try:
                    snapshot = await self._context.page.evaluate(
                        SNAPSHOT_GENERATE_JS,
                        args=content_hash,
                        timeout_s=10.0,
                    )
                    if isinstance(snapshot, str) and snapshot:
                        return snapshot
                except Exception as exc:
                    log.debug("Realm 快照第 %d 次失败: %s", attempt + 1, exc)
                    if attempt < 2:
                        await self._context.page.wait_for_timeout(100)
        raise RuntimeError(f"Realm snapshot generation failed for {content_hash[:8]}")

    async def send_hooked_request(
        self,
        *,
        body: str,
        timeout_ms: int,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        ctx = await self.ensure_ready()
        captured_url = url or str(ctx.base_template.get("url") or "")
        raw_headers = ctx.base_template.get("headers")
        captured_headers = headers or (
            {str(k): str(v) for k, v in raw_headers.items()}
            if isinstance(raw_headers, dict)
            else {}
        )
        return await self._transport.send_hooked_request(
            ctx.page,
            url=captured_url,
            headers=captured_headers,
            body=body,
            timeout_ms=timeout_ms,
            auth_user=ctx.auth_user,
        )

    async def send_streaming_request(
        self,
        *,
        body: str,
        timeout_ms: int,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> AsyncGenerator[tuple[str, object], None]:
        ctx = await self.ensure_ready()
        captured_url = url or str(ctx.base_template.get("url") or "")
        raw_headers = ctx.base_template.get("headers")
        captured_headers = headers or (
            {str(k): str(v) for k, v in raw_headers.items()}
            if isinstance(raw_headers, dict)
            else {}
        )
        ctx.in_flight += 1
        try:
            async for event in self._transport.send_streaming_request(
                ctx.page,
                url=captured_url,
                headers=captured_headers,
                body=body,
                timeout_ms=timeout_ms,
                auth_user=ctx.auth_user,
            ):
                yield event
        finally:
            ctx.decr_in_flight()

    async def cleanup_stream_page(self, page: CDPPage | None = None) -> None:
        del page
        return

    async def save_current_cookies(self) -> bool:
        return await save_browser_cookies(self._context.page, self._context.auth_file)

    async def close(self) -> None:
        with suppress(Exception):
            await self._context.page.close()


@dataclass
class Realm:
    realm_id: str
    account_id: str
    context_id: str
    session: RealmSession
    auth_file: str | None
    auth_user: str
    last_used: float = field(default_factory=time.monotonic)
    build_surface: Any = None

    @property
    def in_flight(self) -> int:
        return self.session.in_flight_count

    async def ensure_build_surface(self, cdp: CDPClient) -> BuildSurface:
        from aistudio_api.infrastructure.gateway.build_surface import BuildSurface

        if self.build_surface is None:
            self.build_surface = BuildSurface(
                cdp,
                context_id=self.context_id,
                realm_id=self.realm_id,
            )
        surface = self.build_surface
        await surface.ensure_ready(
            self.auth_file, self.auth_user, account_id=self.account_id
        )
        return surface


class RealmPool:
    """Lazy BrowserContext pool keyed by account."""

    def __init__(
        self,
        cdp_provider: Callable[[], Awaitable[CDPClient]],
        *,
        features_provider: Callable[[], dict[str, object]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cdp_provider = cdp_provider
        self._features_provider = features_provider
        self._clock = clock
        self._realms: dict[str, Realm] = {}
        self._by_account: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._counter = itertools.count(1)

    def _max_realms(self) -> int:
        if self._features_provider is None:
            return 2
        try:
            features = self._features_provider()
        except Exception:
            return 2
        raw = features.get("routing")
        routing = raw if isinstance(raw, dict) else {}
        if not bool(routing.get("realm_mode", True)):
            return 1
        try:
            return max(1, int(routing.get("max_realms", 2)))
        except (TypeError, ValueError):
            return 2

    @property
    def live_realms(self) -> int:
        return len(self._realms)

    def get(self, realm_id: str) -> Realm | None:
        return self._realms.get(realm_id)

    def page_counts(self) -> dict[str, int]:
        """Live chat/build pages across realms (read-only budget input)."""
        counts = {"chat": 0, "build": 0}
        for realm in self._realms.values():
            try:
                if not realm.session.page.is_closed():
                    counts["chat"] += 1
            except Exception:
                pass
            surface = realm.build_surface
            page = getattr(getattr(surface, "state", None), "page", None)
            if page is not None:
                try:
                    if not page.is_closed():
                        counts["build"] += 1
                except Exception:
                    pass
        return counts

    def snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "realm_id": realm.realm_id,
                "account_id": realm.account_id,
                "context_id": realm.context_id,
                "in_flight": realm.in_flight,
                "last_used": realm.last_used,
                "build_ready": realm.build_surface is not None,
            }
            for realm in self._realms.values()
        ]

    async def acquire(
        self,
        *,
        account_id: str,
        auth_file: str | None,
        auth_user: str | None = None,
        timeout_s: float = 90.0,
    ) -> Realm:
        async with self._lock:
            existing_id = self._by_account.get(account_id)
            if existing_id:
                existing = self._realms.get(existing_id)
                if existing is not None and not existing.session.page.is_closed():
                    existing.last_used = self._clock()
                    return existing
                await self._close_realm(existing_id)

            limit = self._max_realms()
            while len(self._realms) >= limit:
                if not await self._retire_one_idle():
                    raise RealmCapacityError(
                        f"realm capacity exhausted: live={len(self._realms)} limit={limit}"
                    )

            cdp = await self._cdp_provider()
            context_id = await cdp.create_browser_context()
            realm_id = f"realm-{account_id}-{next(self._counter)}"
            try:
                session = await RealmSession.bootstrap(
                    cdp,
                    context_id=context_id,
                    auth_file=auth_file,
                    auth_user=auth_user,
                    realm_id=realm_id,
                    timeout_s=timeout_s,
                )
            except Exception:
                with suppress(Exception):
                    await cdp.dispose_browser_context(context_id)
                raise
            realm = Realm(
                realm_id=realm_id,
                account_id=account_id,
                context_id=context_id,
                session=session,
                auth_file=auth_file,
                auth_user=session.active_context.auth_user,
                last_used=self._clock(),
            )
            self._realms[realm_id] = realm
            self._by_account[account_id] = realm_id
            log.info(
                "Realm 创建: realm=%s account=%s live=%d/%d",
                realm_id,
                account_id,
                len(self._realms),
                limit,
            )
            return realm

    async def _retire_one_idle(self) -> bool:
        idle = sorted(
            (realm for realm in self._realms.values() if realm.in_flight <= 0),
            key=lambda realm: realm.last_used,
        )
        if not idle:
            return False
        await self._close_realm(idle[0].realm_id)
        return True

    async def retire_idle(self) -> int:
        async with self._lock:
            closed = 0
            while await self._retire_one_idle():
                closed += 1
            return closed

    async def _close_realm(self, realm_id: str) -> None:
        realm = self._realms.pop(realm_id, None)
        if realm is None:
            return
        self._by_account.pop(realm.account_id, None)
        build_surface = realm.build_surface
        if build_surface is not None:
            state_page = getattr(getattr(build_surface, "state", None), "page", None)
            if state_page is not None:
                with suppress(Exception):
                    await state_page.close()
        await realm.session.close()
        with suppress(Exception):
            cdp = await self._cdp_provider()
            await cdp.dispose_browser_context(realm.context_id)
        log.info(
            "Realm 回收: realm=%s account=%s live=%d",
            realm.realm_id,
            realm.account_id,
            len(self._realms),
        )

    async def close(self) -> None:
        async with self._lock:
            for realm_id in list(self._realms):
                await self._close_realm(realm_id)


__all__ = [
    "Realm",
    "RealmCapacityError",
    "RealmPool",
    "RealmSession",
]
