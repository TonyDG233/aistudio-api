"""Shared browser session management for gateway operations."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from hashlib import sha256
from pathlib import Path

from aistudio_api.config import settings
from aistudio_api.infrastructure.browser.browser_engine import (
    ChromiumProcess,
    launch_chromium_process,
)
from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage
from aistudio_api.infrastructure.browser.scripts import (
    DOM_GC_CLEANUP_JS,
    SNAPSHOT_GENERATE_JS,
)
from aistudio_api.infrastructure.gateway.botguard_bootstrap import (
    BOTGUARD_BOOTSTRAP_PROMPT,
    bootstrap_botguard_and_template,
)
from aistudio_api.infrastructure.gateway.session_auth import (
    bootstrap_google_session,
    derive_profile_dir,
    extract_account_email,
    get_auth_user_from_meta,
    is_login_page_url,
    load_cookies_to_page,
    navigate_to_aistudio,
    save_browser_cookies,
)
from aistudio_api.infrastructure.gateway.session_state import (
    SessionContext,
    SessionReadinessGate,
    SessionState,
)
from aistudio_api.infrastructure.gateway.transport import XHRStreamTransport
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("session")

_is_login_page_url = is_login_page_url

DEFAULT_BOOTSTRAP_TEMPLATE: dict[str, object] = {
    "url": "https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GenerateContent",
    "headers": {
        "Content-Type": "application/json+protobuf",
        "X-User-Agent": "grpc-web-javascript/0.1",
        "X-Goog-Api-Key": "AIzaSyDdP816MREB3SkjZO04QXbjsigfcI0GWOs",
        "X-Goog-AuthUser": "0",
        "Referer": "https://aistudio.google.com/",
    },
    "body": '["models/gemini-3.7-flash"]',
}


class BrowserSession:
    """Coordinates browser lifecycle, readiness gate, and blue-green tab transitions."""

    def __init__(self, port: int):
        self.port = port
        self._auth_file: str | None = settings.auth_file or None
        self._auth_user: str | None = None
        self._profile_dir = derive_profile_dir(self._auth_file)
        self._proc: ChromiumProcess | None = None
        self._cdp_client: CDPClient | None = None

        self._gate = SessionReadinessGate()
        self._transition_lock = asyncio.Lock()
        self._snapshot_lock = asyncio.Lock()
        self._transport = XHRStreamTransport()
        self._retiring_contexts: list[SessionContext] = []
        self._background_tasks: set[asyncio.Task[object]] = set()
        self._in_flight: int = 0
        self._last_activity_time: float = time.time()
        self._stream_cleanup_count: int = 0

    @property
    def state(self) -> SessionState:
        return self._gate.state

    @property
    def is_ready(self) -> bool:
        return self._gate.is_ready()

    @property
    def active_context(self) -> SessionContext | None:
        return self._gate.active_context

    @property
    def page(self) -> CDPPage | None:
        return self._gate.active_context.page if self._gate.active_context else None

    @property
    def snap_key(self) -> str | None:
        return self._gate.active_context.snap_key if self._gate.active_context else None

    @property
    def base_template(self) -> dict[str, object] | None:
        return (
            self._gate.active_context.base_template
            if self._gate.active_context
            else None
        )

    def set_ready_context(self, context: SessionContext) -> None:
        """Set active ready context (used by transitions and test fixtures)."""
        self._gate.set_ready(context)

    def get_current_auth_user(self) -> str:
        """获取当前活跃账号的 auth_user 编号（0, 1, 2...）。"""
        if self._auth_user is not None:
            return self._auth_user
        if self._gate.active_context is not None:
            return self._gate.active_context.auth_user
        return get_auth_user_from_meta(self._auth_file)

    @asynccontextmanager
    async def request_scope(self):
        """追踪活动请求并更新活跃时间戳。"""
        self._in_flight += 1
        self._last_activity_time = time.time()
        ctx = self.active_context
        if ctx is not None:
            ctx.in_flight += 1
        try:
            yield ctx
        finally:
            self._in_flight = max(0, self._in_flight - 1)
            if ctx is not None:
                ctx.decr_in_flight()
            self._last_activity_time = time.time()

    async def ensure_ready(self, timeout_s: float = 60.0) -> SessionContext:
        """Wait until session is 100% READY."""
        if self._gate.is_ready() and self._gate.active_context is not None:
            return self._gate.active_context

        if self._gate.state == SessionState.UNINITIALIZED:
            await self.transition_to_account(self._auth_file, force_refresh=False)

        return await self._gate.wait_ready(timeout_s=timeout_s)

    async def ensure_context(self) -> CDPPage:
        """Ensure active verified CDPPage is available, auto-reconnecting if dropped."""
        page = self.page
        if page is not None and not page.is_closed():
            if await page.is_alive(timeout_s=1.0):
                return page
            log.warning("浏览器页面无响应，重新连接...")
        await self._close_internal()
        res = await self._ensure_browser_cdp()
        return res or (await self.ensure_ready()).page

    async def ensure_browser_cdp_client(self) -> CDPClient:
        """Ensure the CDP client is connected and return it (build surface entry)."""
        await self._ensure_browser_cdp()
        assert self._cdp_client is not None
        return self._cdp_client

    async def is_alive(self, timeout_s: float = 1.5) -> bool:
        if self._proc is not None and not self._proc.is_alive():
            return False
        page = self.page
        if page is None or page.is_closed():
            return False
        return await page.is_alive(timeout_s=timeout_s)

    async def check_idle_timeout(self) -> bool:
        idle_timeout = getattr(settings, "browser_idle_timeout", 0)
        if idle_timeout <= 0:
            return False
        if self._in_flight <= 0 and self._proc is not None:
            idle_duration = time.time() - self._last_activity_time
            if idle_duration >= idle_timeout:
                log.info(
                    "浏览器已空闲 %.1f 秒 (阈值=%d 秒)，回收进程释放内存",
                    idle_duration,
                    idle_timeout,
                )
                await self.close()
                return True
        return False

    async def switch_auth(self, auth_file: str | None) -> None:
        """Switch auth using blue-green transition without killing in-flight streams."""
        await self.transition_to_account(auth_file, force_refresh=True)

    async def transition_to_account(
        self, auth_file: str | None, force_refresh: bool = False
    ) -> SessionContext:
        """Execute atomic session transition to target auth file."""
        async with self._transition_lock:
            if (
                not force_refresh
                and self._gate.is_ready()
                and self._gate.active_context is not None
                and self._gate.active_context.auth_file == auth_file
            ):
                return self._gate.active_context

            self._gate.set_preparing()
            old_context = self._gate.active_context
            target_auth_user = get_auth_user_from_meta(auth_file)
            self._auth_file = auth_file
            self._auth_user = target_auth_user
            self._profile_dir = derive_profile_dir(auth_file)

            try:
                # Real browser mode
                if self._proc is None or not self._proc.is_alive():
                    await self._close_internal()
                    await self._ensure_browser_cdp()

                assert self._cdp_client is not None

                # Blue-Green: always create a dedicated new tab when transitioning from an existing account
                if old_context is not None:
                    new_page = await self._cdp_client.create_new_page(block_assets=True)
                else:
                    new_page = await self._cdp_client.connect_page(block_assets=True)
                new_page.set_liveness_checker(
                    lambda: self._proc is None or self._proc.is_alive()
                )

                await load_cookies_to_page(new_page, auth_file)
                await navigate_to_aistudio(
                    new_page,
                    auth_user=target_auth_user,
                    auth_file=auth_file,
                )

                async def _reload_cb(page: CDPPage) -> bool:
                    return await load_cookies_to_page(page, auth_file)

                snap_key, base_template = await bootstrap_botguard_and_template(
                    new_page,
                    reload_cookies_cb=_reload_cb,
                    is_proc_alive_cb=lambda: (
                        self._proc is None or self._proc.is_alive()
                    ),
                )

                new_context = SessionContext(
                    page=new_page,
                    snap_key=snap_key,
                    auth_user=target_auth_user,
                    auth_file=auth_file,
                    base_template=base_template,
                )

                if (
                    old_context is not None
                    and old_context.page.target_id != new_page.target_id
                    and not old_context.page.is_closed()
                ):
                    self._retiring_contexts.append(old_context)
                    drain_task = asyncio.create_task(
                        self._drain_retiring_context(
                            old_context, active_target_id=new_page.target_id
                        ),
                        name=f"drain_{old_context.auth_user}",
                    )
                    self._background_tasks.add(drain_task)
                    drain_task.add_done_callback(self._background_tasks.discard)
                self._gate.set_ready(new_context)
                log.info(
                    "会话蓝绿平滑热切成功: auth_file=%s, u/%s (活跃在途=%d)",
                    auth_file,
                    target_auth_user,
                    self._in_flight,
                )
                return new_context

            except Exception as exc:
                self._gate.set_failed(exc)
                log.error("会话切换失败: %s", exc)
                raise

    async def _drain_retiring_context(
        self,
        context: SessionContext,
        active_target_id: str | None = None,
        max_wait_s: float = 12.0,
    ) -> None:
        if context.in_flight > 0:
            with suppress(TimeoutError):
                await asyncio.wait_for(context.drain_event.wait(), timeout=max_wait_s)

        with suppress(Exception):
            if context in self._retiring_contexts:
                self._retiring_contexts.remove(context)
            if not context.page.is_closed():
                await context.page.close()
            if (
                self._cdp_client
                and context.page.target_id
                and context.page.target_id != active_target_id
            ):
                await self._cdp_client.close_target(context.page.target_id)

    async def ensure_botguard_service(self, force_refresh: bool = False) -> CDPPage:
        page = self.page
        curr_url = page.url if page else ""
        if "available-regions" in curr_url:
            raise RuntimeError(
                f"Google AI Studio 地区限制: 访问被重定向至 available-regions ({curr_url})"
            )
        if force_refresh or not self._gate.is_ready():
            await self.transition_to_account(
                self._auth_file, force_refresh=force_refresh
            )
        return (await self.ensure_ready()).page

    async def capture_template(self, model: str) -> dict[str, object]:
        if self._proc is not None and not self._proc.is_alive():
            raise RuntimeError("Browser process died during template capture")

        ctx = self.active_context
        base = dict(ctx.base_template if ctx else DEFAULT_BOOTSTRAP_TEMPLATE)
        normalized_model = model if model.startswith("models/") else f"models/{model}"
        try:
            body_val = base.get("body")
            if (
                isinstance(body_val, str)
                and body_val.startswith('["models/')
                and "," in body_val
            ):
                first_comma = body_val.find(",")
                base["body"] = f'["{normalized_model}"' + body_val[first_comma:]
            elif isinstance(body_val, str) and body_val.startswith("["):
                body_list = json.loads(body_val)
                if isinstance(body_list, list) and len(body_list) > 0:
                    body_list[0] = normalized_model
                    base["body"] = json.dumps(body_list, separators=(",", ":"))
        except Exception as e:
            log.debug("Failed to adapt model into base template body: %s", e)
        return base

    async def generate_snapshot(self, contents: list[AistudioContent]) -> str:
        ctx = await self.ensure_ready()
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
                    snapshot = await ctx.page.evaluate(
                        SNAPSHOT_GENERATE_JS, args=content_hash, timeout_s=10.0
                    )
                    if snapshot and isinstance(snapshot, str) and len(snapshot) > 0:
                        return snapshot
                except Exception as e:
                    log.debug("快照计算第 %d 次尝试失败: %s", attempt + 1, e)
                    if attempt < 2:
                        await ctx.page.wait_for_timeout(100)

        raise RuntimeError(
            f"Snapshot generation failed for content hash {content_hash[:8]}"
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
        hdrs_val = ctx.base_template.get("headers")
        captured_headers = headers or (
            {str(k): str(v) for k, v in hdrs_val.items()}
            if isinstance(hdrs_val, dict)
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
        hdrs_val = ctx.base_template.get("headers")
        captured_headers = headers or (
            {str(k): str(v) for k, v in hdrs_val.items()}
            if isinstance(hdrs_val, dict)
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
            await self.cleanup_stream_page(ctx.page)

    async def cleanup_stream_page(self, page: CDPPage | None = None) -> None:
        self._stream_cleanup_count += 1
        target_page = page or (
            self.active_context.page if self.active_context else None
        )
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

    async def import_cookies(
        self, cookie_string: str, auth_file: str | None = None
    ) -> int:
        from aistudio_api.infrastructure.account.cookie_refresher import (
            load_cookies_from_string,
        )

        pw_cookies = load_cookies_from_string(cookie_string)
        target_auth = auth_file or self._auth_file
        if not target_auth:
            return len(pw_cookies)

        await self._ensure_browser_cdp()
        assert self._cdp_client is not None
        page = await self._cdp_client.create_new_page(block_assets=False)
        try:
            await page.set_cookies(pw_cookies)
            with suppress(Exception):
                await bootstrap_google_session(page)
            browser_cookies = await page.get_cookies()
            final_cookies = browser_cookies or pw_cookies
            await save_browser_cookies(
                page, auth_file=target_auth, cookies=final_cookies
            )
            return len(final_cookies)
        finally:
            with suppress(Exception):
                await page.close()
                await self._cdp_client.close_target(page.target_id)

    async def extract_account_email(self, page: CDPPage | None = None) -> str | None:
        target = page or (self.active_context.page if self.active_context else None)
        return await extract_account_email(target)

    async def reload_current_account_cookies(self, page: CDPPage | None = None) -> bool:
        target = page or (self.active_context.page if self.active_context else None)
        return bool(target and await load_cookies_to_page(target, self._auth_file))

    async def _save_cookies(
        self,
        *,
        auth_file: str | None = None,
        cookies: list[dict[str, object]] | None = None,
    ) -> None:
        target = self.page
        if target is not None:
            await save_browser_cookies(
                target, auth_file=auth_file or self._auth_file, cookies=cookies
            )

    async def _ensure_browser_cdp(self) -> CDPPage | None:
        if self._proc is not None and self._proc.is_alive() and self._cdp_client:
            return None

        profile_dir = self._profile_dir
        if profile_dir:
            Path(profile_dir).mkdir(parents=True, exist_ok=True)

        self._proc = launch_chromium_process(
            port=self.port,
            user_data_dir=profile_dir,
            headless=settings.browser_headless,
        )

        self._cdp_client = CDPClient(port=self.port)
        await self._cdp_client.wait_until_ready()
        return await self._cdp_client.connect_page(block_assets=True)

    async def close(self) -> None:
        async with self._transition_lock:
            await self._close_internal()

    async def _close_internal(self) -> None:
        self._gate.reset()
        self._retiring_contexts.clear()

        if self._cdp_client is not None:
            with suppress(Exception):
                await self._cdp_client.close()
            self._cdp_client = None

        if self._proc is not None:
            with suppress(Exception):
                self._proc.terminate()
            self._proc = None


__all__ = [
    "BOTGUARD_BOOTSTRAP_PROMPT",
    "DEFAULT_BOOTSTRAP_TEMPLATE",
    "BrowserSession",
    "_is_login_page_url",
]
