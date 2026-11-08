"""One pooled BrowserContext session.

A PooledSession owns the chat page, cookie jar and transport for one isolated
BrowserContext. It is created by the future InstancePool (B2-3) and never
represents a privileged default session.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from hashlib import sha256

from aistudio_api.config import resolve_fingerprint_spoof_enabled
from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage
from aistudio_api.infrastructure.browser.fingerprint import (
    apply_fingerprint,
    build_fingerprint_profile,
)
from aistudio_api.infrastructure.browser.scripts import (
    DOM_GC_CLEANUP_JS,
    SNAPSHOT_GENERATE_JS,
    build_snapshot_args,
)
from aistudio_api.infrastructure.gateway.botguard_bootstrap import (
    bootstrap_botguard_and_template,
)
from aistudio_api.infrastructure.gateway.capture import RequestCaptureService
from aistudio_api.infrastructure.gateway.session import build_snapshot_hash_parts
from aistudio_api.infrastructure.gateway.session_auth import (
    get_auth_user_from_meta,
    get_tier_from_meta,
    load_cookies_to_page,
    navigate_to_aistudio,
    save_browser_cookies,
)
from aistudio_api.infrastructure.gateway.session_state import (
    SessionContext,
    should_warm_snapshot,
)
from aistudio_api.infrastructure.gateway.transport import (
    XHRStreamTransport,
    refresh_page_auth_state,
)
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("pooled-session")


class PooledSession:
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
        self._last_snapshot_at: float | None = None
        self._force_snapshot_warmup = False
        self.last_snapshot_warmup = False
        self._in_flight = 0
        self._stream_cleanup_count = 0
        self._capture_service = RequestCaptureService(self)

    @property
    def capture_service(self) -> RequestCaptureService:
        return self._capture_service

    @classmethod
    async def bootstrap(
        cls,
        cdp: CDPClient,
        *,
        context_id: str,
        auth_file: str | None,
        auth_user: str | None,
        realm_id: str,
        account_id: str | None = None,
        timeout_s: float = 90.0,
    ) -> PooledSession:
        page = await cdp.create_page_in_context(context_id, block_assets=True)
        target_auth_user = auth_user or get_auth_user_from_meta(auth_file)
        page.account_auth_file = auth_file
        try:
            # P3-F: register the account fingerprint before navigation/cookies so
            # the very first document already sees the spoofed navigator/WebGL.
            if resolve_fingerprint_spoof_enabled():
                profile = build_fingerprint_profile(
                    account_id=account_id, auth_file=auth_file
                )
                await apply_fingerprint(page, profile, enabled=True)

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
                    auth_user=target_auth_user,
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
            tier=get_tier_from_meta(auth_file),
        )
        log.info(
            "PooledSession 就绪: realm=%s account=%s auth_user=%s",
            realm_id,
            auth_file,
            target_auth_user,
        )
        return cls(context=context, transport=XHRStreamTransport(), realm_id=realm_id)

    @property
    def active_context(self) -> SessionContext:
        return self._context

    @property
    def engine(self) -> str:
        return "browser"

    @property
    def page(self) -> CDPPage | None:
        return self._context.page

    @property
    def in_flight_count(self) -> int:
        return self._in_flight

    def is_alive(self) -> bool:
        page = self._context.page
        return page is not None and not page.is_closed()

    @asynccontextmanager
    async def request_scope(self):
        self._in_flight += 1
        self._context.incr_in_flight()
        try:
            yield self._context
        finally:
            self._in_flight = max(0, self._in_flight - 1)
            self._context.decr_in_flight()

    async def ensure_ready(self, timeout_s: float = 120.0) -> SessionContext:
        del timeout_s
        if self._context.require_page().is_closed():
            raise RuntimeError(f"PooledSession {self._realm_id} page is closed")
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
            log.debug("PooledSession 模板模型改写失败: %s", exc)
        return base

    async def generate_snapshot(
        self, contents: list[AistudioContent], *, payload_size: int | None = None
    ) -> str:
        prompt = " ".join(build_snapshot_hash_parts(contents))
        content_hash = sha256(prompt.encode("utf-8")).hexdigest()
        async with self._snapshot_lock:
            now = time.monotonic()
            prompt_bytes = len(prompt.encode("utf-8"))
            warmup = should_warm_snapshot(
                last_snapshot_at=self._last_snapshot_at,
                now=now,
                prompt_bytes=prompt_bytes,
                payload_bytes=payload_size or 0,
                force=self._force_snapshot_warmup,
            )
            args = build_snapshot_args(
                prompt=prompt, content_hash=content_hash, warmup=warmup
            )
            for attempt in range(5):
                try:
                    raw_res = await self._context.require_page().evaluate(
                        SNAPSHOT_GENERATE_JS,
                        args=args,
                        timeout_s=25.0,
                    )
                    snapshot = ""
                    warmup_used = warmup
                    if isinstance(raw_res, dict):
                        snapshot = str(raw_res.get("snapshot") or "")
                        warmup_used = bool(raw_res.get("warmup_used", warmup))
                    elif isinstance(raw_res, str):
                        snapshot = raw_res
                    if snapshot:
                        self._last_snapshot_at = time.monotonic()
                        self._force_snapshot_warmup = False
                        self.last_snapshot_warmup = warmup_used
                        return snapshot
                except Exception as exc:
                    if "low_quality_snapshot" in str(exc):
                        raise RuntimeError(
                            "PooledSession snapshot generation failed "
                            f"(low_quality_snapshot) for {content_hash[:8]}"
                        ) from exc
                    log.debug("PooledSession 快照第 %d 次失败: %s", attempt + 1, exc)
                if attempt < 4:
                    await self._context.require_page().wait_for_timeout(250)
        raise RuntimeError(
            f"PooledSession snapshot generation failed for {content_hash[:8]}"
        )

    async def refresh_auth_state(self) -> bool:
        """Invalidate and refetch this realm's cached cookie/signature state.

        Any 403-driven refresh also forces the next snapshot through a local
        warmup so the WAA challenge sees fresh cookie state.
        """
        self._force_snapshot_warmup = True
        page = self._context.require_page()
        if page.is_closed():
            log.warning("刷新鉴权态失败: realm=%s 页面已关闭", self._realm_id)
            return False
        return await refresh_page_auth_state(page)

    async def send_hooked_request(
        self,
        *,
        body: str,
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
            ctx.require_page(),
            url=captured_url,
            headers=captured_headers,
            body=body,
            auth_user=ctx.auth_user,
            tier=ctx.tier,
        )

    async def send_streaming_request(
        self,
        *,
        body: str,
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
        ctx.incr_in_flight()
        try:
            async for event in self._transport.send_streaming_request(
                ctx.require_page(),
                url=captured_url,
                headers=captured_headers,
                body=body,
                auth_user=ctx.auth_user,
                tier=ctx.tier,
            ):
                yield event
        finally:
            ctx.decr_in_flight()
            await self.cleanup_stream_page(ctx.require_page())

    async def cleanup_stream_page(self, page: CDPPage | None = None) -> None:
        self._stream_cleanup_count += 1
        target_page = page or self._context.require_page()
        if (
            target_page is not None
            and not target_page.is_closed()
            and (self._in_flight <= 0 or (self._stream_cleanup_count % 5 == 0))
        ):
            with suppress(Exception):
                await target_page.evaluate(DOM_GC_CLEANUP_JS)
            if self._in_flight <= 0 and (self._stream_cleanup_count % 10 == 0):
                with suppress(Exception):
                    await target_page.collect_garbage()
                import gc

                gc.collect()

    async def save_current_cookies(self) -> bool:
        return await save_browser_cookies(
            self._context.require_page(), self._context.auth_file
        )

    async def close(self) -> None:
        self._capture_service.clear_templates(invalidate_base=True)
        with suppress(Exception):
            await self._context.require_page().close()
