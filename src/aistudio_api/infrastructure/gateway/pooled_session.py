"""One pooled BrowserContext session (WP5 v3).

A PooledSession owns the chat page, cookie jar and transport for one
``InstanceSlot``. It is created by ``InstancePool`` and never represents a
privileged default session.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from hashlib import sha256

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
    ) -> PooledSession:
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
                    log.debug("PooledSession 快照第 %d 次失败: %s", attempt + 1, exc)
                    if attempt < 2:
                        await self._context.page.wait_for_timeout(100)
        raise RuntimeError(
            f"PooledSession snapshot generation failed for {content_hash[:8]}"
        )

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
