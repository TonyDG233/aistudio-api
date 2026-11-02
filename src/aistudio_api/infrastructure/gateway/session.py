"""Shared browser session management for gateway operations.

D-05/D-10/D-11: the control session owns exactly one account, one Page and one
surface at a time. Rebind drains the old Page once (bounded), closes it, and only
then creates the replacement page. No blue-green window and no background drain
task exists here.
"""

from __future__ import annotations

import asyncio
import gc
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from hashlib import sha256
from pathlib import Path

from aistudio_api.config import resolve_fingerprint_spoof_enabled, settings
from aistudio_api.infrastructure.browser.browser_engine import (
    ChromiumProcess,
    launch_chromium_process,
)
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
from aistudio_api.infrastructure.gateway.public_web_constants import (
    AI_STUDIO_HOME_URL,
    DEFAULT_CHAT_MODEL,
    GENERATE_CONTENT_URL,
    GRPC_WEB_USER_AGENT,
    WEB_API_KEY,
)
from aistudio_api.infrastructure.gateway.session_auth import (
    bootstrap_google_session,
    derive_profile_dir,
    extract_account_email,
    extract_account_tier,
    get_auth_user_from_meta,
    get_tier_from_meta,
    is_aistudio_page_url,
    is_login_page_url,
    load_cookies_to_page,
    navigate_to_aistudio,
    save_browser_cookies,
)
from aistudio_api.infrastructure.gateway.session_state import SessionContext
from aistudio_api.infrastructure.gateway.transport import (
    XHRStreamTransport,
    refresh_page_auth_state,
)
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("session")

_is_login_page_url = is_login_page_url
_is_aistudio_page_url = is_aistudio_page_url

# 旧 Page 只允许一次有界等待；超时后强制关闭（D-10）。
DRAIN_TIMEOUT_S = 12.0


def build_snapshot_hash_parts(
    contents: list[AistudioContent], *, include_code: bool | None = None
) -> list[str]:
    """Build snapshot hash parts.

    D-09: executable_code / code_execution_result participate in the hash by
    default. 若出现严重路由异常（例如同一输入被路由到错误模型/工具面），
    优先怀疑该 hash 并可用 ``hash_include_code=false`` 回退到上游行为。
    """
    if include_code is None:
        include_code = bool(getattr(settings, "hash_include_code", True))
    hash_parts: list[str] = []
    for content in contents:
        for part in content.parts:
            if part.text is not None:
                hash_parts.append(str(part.text))
            elif part.inline_data is not None:
                hash_parts.append(str(part.inline_data[1]))
            elif part.file_id is not None:
                hash_parts.append(str(part.file_id))
            elif include_code and part.executable_code is not None:
                hash_parts.append(str(part.executable_code[1]))
            elif include_code and part.code_execution_result is not None:
                hash_parts.append(str(part.code_execution_result[1]))
            else:
                hash_parts.append("")
    return hash_parts


DEFAULT_BOOTSTRAP_TEMPLATE: dict[str, object] = {
    "url": GENERATE_CONTENT_URL,
    "headers": {
        "Content-Type": "application/json+protobuf",
        "X-User-Agent": GRPC_WEB_USER_AGENT,
        "X-Goog-Api-Key": WEB_API_KEY,
        "X-Goog-AuthUser": "0",
        "Referer": AI_STUDIO_HOME_URL,
    },
    "body": f'["models/{DEFAULT_CHAT_MODEL}"]',
}


class BrowserSession:
    """Coordinates one browser process and one active Page at a time."""

    def __init__(self, port: int):
        self.port = port
        self._auth_file: str | None = settings.auth_file or None
        self._auth_user: str | None = None
        self._tier: str | None = None
        self._profile_dir = derive_profile_dir(self._auth_file)
        self._proc: ChromiumProcess | None = None
        self._cdp_client: CDPClient | None = None

        self._active_context: SessionContext | None = None
        self._ready_event = asyncio.Event()
        self._preparing = False
        self._last_error: Exception | None = None
        self._drain_timeout_s = DRAIN_TIMEOUT_S
        self._transition_lock = asyncio.Lock()
        self._snapshot_lock = asyncio.Lock()
        self._transport = XHRStreamTransport()
        self._in_flight: int = 0
        self._stream_cleanup_count: int = 0

    @property
    def is_ready(self) -> bool:
        return self._active_context is not None and self._active_context.is_usable()

    @property
    def active_context(self) -> SessionContext | None:
        return self._active_context

    @property
    def page(self) -> CDPPage | None:
        return self._active_context.page if self._active_context else None

    @property
    def snap_key(self) -> str | None:
        return self._active_context.snap_key if self._active_context else None

    @property
    def base_template(self) -> dict[str, object] | None:
        return self._active_context.base_template if self._active_context else None

    def set_ready_context(self, context: SessionContext) -> None:
        """Set active ready context (used by transitions and test fixtures)."""
        self._active_context = context
        self._preparing = False
        self._last_error = None
        self._ready_event.set()

    def get_current_auth_user(self) -> str:
        """获取当前活跃账号的 auth_user 编号（0, 1, 2...）。"""
        if self._auth_user is not None:
            return self._auth_user
        if self._active_context is not None:
            return self._active_context.auth_user
        return get_auth_user_from_meta(self._auth_file)

    def get_current_tier(self) -> str:
        """获取当前活跃账号的订阅权益等级 (Free / Pro / Ultra / Plus)。"""
        if self._active_context is not None and self._active_context.tier:
            return self._active_context.tier
        if self._tier is not None:
            return self._tier
        return get_tier_from_meta(self._auth_file)

    @asynccontextmanager
    async def request_scope(self):
        """Account for one active request on the current page.

        New requests wait for an in-progress rebind instead of touching the
        retiring page; existing requests keep the old context until they finish.
        """
        ctx = self._active_context
        if self._preparing:
            ctx = await self._wait_ready_context(60.0)
        elif ctx is not None and not ctx.is_usable():
            ctx = None
        if ctx is not None:
            self._in_flight += 1
            ctx.incr_in_flight()
        try:
            yield ctx
        finally:
            if ctx is not None:
                self._in_flight = max(0, self._in_flight - 1)
                ctx.decr_in_flight()

    async def _wait_ready_context(self, timeout_s: float) -> SessionContext:
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout_s)
        except TimeoutError as err:
            raise TimeoutError(
                f"Timed out waiting for session readiness after {timeout_s}s"
            ) from err

        if self._last_error is not None:
            raise RuntimeError(
                f"Session transition failed: {self._last_error}"
            ) from self._last_error
        ctx = self._active_context
        if ctx is None or not ctx.is_usable():
            raise RuntimeError("Session active page closed unexpectedly")
        return ctx

    async def ensure_ready(self, timeout_s: float = 120.0) -> SessionContext:
        """Return the single ready context, waiting for an in-progress rebind."""
        if self.is_ready and self._active_context is not None:
            return self._active_context

        if self._last_error is not None:
            raise RuntimeError(f"Session transition failed: {self._last_error}")

        if not self._preparing:
            # Serialized inside transition_to_account; concurrent callers reuse it.
            return await self.transition_to_account(
                self._auth_file, force_refresh=False
            )

        # An already-running request may reach this point after the rebind started;
        # let it finish on the old page. The bounded drain in the transition waits
        # for it and force-closes on timeout.
        ctx = self._active_context
        if ctx is not None and ctx.is_usable():
            return ctx
        return await self._wait_ready_context(timeout_s)

    async def switch_auth(self, auth_file: str | None) -> None:
        """Switch auth with a bounded single-page rebind (no blue-green)."""
        await self.transition_to_account(auth_file, force_refresh=True)

    async def transition_to_account(
        self, auth_file: str | None, force_refresh: bool = False
    ) -> SessionContext:
        """Execute one bounded single-page transition to the target auth file."""
        async with self._transition_lock:
            if (
                not force_refresh
                and self.is_ready
                and self._active_context is not None
                and self._active_context.auth_file == auth_file
            ):
                return self._active_context
            if not force_refresh and self._last_error is not None:
                raise RuntimeError(
                    f"Session transition failed: {self._last_error}"
                ) from self._last_error

            old_context = self._active_context
            self._preparing = True
            self._ready_event.clear()
            self._last_error = None
            target_auth_user = get_auth_user_from_meta(auth_file)
            target_tier = get_tier_from_meta(auth_file)
            self._auth_file = auth_file
            self._auth_user = target_auth_user
            self._tier = target_tier
            self._profile_dir = derive_profile_dir(auth_file)
            new_page: CDPPage | None = None
            try:
                # 旧页先有界 drain 并关闭；期间不创建新页（D-05/D-10/D-11）。
                if old_context is not None:
                    await self._drain_and_close_context(old_context)
                self._active_context = None

                if self._proc is None or not self._proc.is_alive():
                    await self._stop_browser()
                    # P-02: 只恢复进程/CDP，不提前 connect_page；否则随后的
                    # create_new_page 会留下第二个 Page（D-11）。
                    await self._ensure_browser_cdp(connect_page=False)

                assert self._cdp_client is not None

                if old_context is not None:
                    new_page = await self._cdp_client.create_new_page(block_assets=True)
                else:
                    new_page = await self._cdp_client.connect_page(block_assets=True)
                new_page.account_auth_file = auth_file

                # P3-F: the control session uses the auth file identity (parent
                # directory + file name) as the fingerprint identity because no
                # account_id is available here, so a hot account switch also
                # swaps the injected profile.
                if resolve_fingerprint_spoof_enabled():
                    profile = build_fingerprint_profile(auth_file=auth_file)
                    await apply_fingerprint(new_page, profile, enabled=True)

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
                    auth_user=target_auth_user,
                )

                target_tier = get_tier_from_meta(auth_file)
                self._tier = target_tier
                new_context = SessionContext(
                    page=new_page,
                    snap_key=snap_key,
                    auth_user=target_auth_user,
                    auth_file=auth_file,
                    base_template=base_template,
                    tier=target_tier,
                )
                self._active_context = new_context
                self._preparing = False
                self._ready_event.set()
                log.info(
                    "会话切换成功: 文件=%s, auth_user=%s, 在途请求数=%d",
                    auth_file,
                    target_auth_user,
                    self._in_flight,
                )
                return new_context

            except Exception as exc:
                if new_page is not None:
                    with suppress(Exception):
                        if not new_page.is_closed():
                            await new_page.close()
                    if self._cdp_client is not None and new_page.target_id:
                        with suppress(Exception):
                            await self._cdp_client.close_target(new_page.target_id)
                self._active_context = None
                self._preparing = False
                self._last_error = exc
                self._ready_event.set()
                log.error("会话切换失败: 目标文件=%s, 错误=%s", auth_file, exc)
                raise

    async def _drain_and_close_context(self, context: SessionContext) -> None:
        """Wait once (bounded) for the old page, then force-close it."""
        if context.in_flight > 0 and not context.page.is_closed():
            deadline = asyncio.get_running_loop().time() + self._drain_timeout_s
            while context.in_flight > 0 and not context.page.is_closed():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    log.warning(
                        "旧页 drain 超时 %.1fs (in_flight=%d)，强制关闭",
                        self._drain_timeout_s,
                        context.in_flight,
                    )
                    break
                try:
                    await asyncio.wait_for(
                        context.drain_event.wait(), timeout=min(remaining, 2.0)
                    )
                    break
                except TimeoutError:
                    continue

        # D03-026: 关闭前回写当前账号 cookie，避免最后一次会话状态丢失。
        with suppress(Exception):
            await save_browser_cookies(context.page, auth_file=context.auth_file)
        with suppress(Exception):
            if not context.page.is_closed():
                await context.page.close()
        if self._cdp_client is not None and context.page.target_id:
            with suppress(Exception):
                await self._cdp_client.close_target(context.page.target_id)

    async def ensure_botguard_service(self, force_refresh: bool = False) -> CDPPage:
        page = self.page
        curr_url = page.url if page else ""
        if "available-regions" in curr_url:
            raise RuntimeError(
                f"Google AI Studio 地区限制: 访问被重定向至 available-regions ({curr_url})"
            )
        if force_refresh or not self.is_ready:
            await self.transition_to_account(
                self._auth_file, force_refresh=force_refresh
            )
        return (await self.ensure_ready()).page

    async def capture_template(self, model: str) -> dict[str, object]:
        if self._proc is not None and not self._proc.is_alive():
            raise RuntimeError("Browser process died during template capture")

        ctx = self.active_context
        if ctx is None or not ctx.base_template:
            ctx = await self.ensure_ready()
        base = dict(ctx.base_template)
        raw_body = base.get("body")
        if not isinstance(raw_body, str) or not raw_body:
            raise RuntimeError("template body missing from ready context")
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
            log.debug("适配模型名称至模板失败: %s", e)
        return base

    async def generate_snapshot(self, contents: list[AistudioContent]) -> str:
        ctx = await self.ensure_ready()
        prompt = " ".join(build_snapshot_hash_parts(contents))
        content_hash = sha256(prompt.encode("utf-8")).hexdigest()
        args = build_snapshot_args(prompt=prompt, content_hash=content_hash)

        async with self._snapshot_lock:
            for attempt in range(5):
                try:
                    raw_res = await ctx.page.evaluate(
                        SNAPSHOT_GENERATE_JS, args=args, timeout_s=25.0
                    )
                    snapshot = ""
                    if isinstance(raw_res, dict):
                        snapshot = str(raw_res.get("snapshot") or "")
                    elif isinstance(raw_res, str):
                        snapshot = raw_res
                    if snapshot:
                        return snapshot
                except Exception as e:
                    log.debug("快照计算第 %d 次尝试失败: %s", attempt + 1, e)
                if attempt < 4:
                    await ctx.page.wait_for_timeout(250)

        raise RuntimeError(
            f"Snapshot generation failed for content hash {content_hash[:8]}"
        )

    async def refresh_auth_state(self) -> bool:
        """Invalidate and refetch this page's cached cookie/signature state."""
        page = self.page
        if page is None or page.is_closed():
            log.warning("刷新鉴权态失败: 当前没有可用页面")
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
        hdrs_val = ctx.base_template.get("headers")
        captured_headers = headers or (
            {str(k): str(v) for k, v in hdrs_val.items()}
            if isinstance(hdrs_val, dict)
            else {}
        )
        ctx.incr_in_flight()
        try:
            async for event in self._transport.send_streaming_request(
                ctx.page,
                url=captured_url,
                headers=captured_headers,
                body=body,
                auth_user=ctx.auth_user,
                tier=ctx.tier,
            ):
                yield event
        finally:
            ctx.decr_in_flight()
            await self.cleanup_stream_page(ctx.page)

    async def cleanup_stream_page(self, page: CDPPage | None = None) -> None:
        self._stream_cleanup_count += 1
        target_page = page or (
            self._active_context.page if self._active_context else None
        )
        if (
            target_page is not None
            and not target_page.is_closed()
            and self._in_flight <= 0
        ):
            with suppress(Exception):
                await target_page.evaluate(DOM_GC_CLEANUP_JS)
            if self._stream_cleanup_count % 10 == 0:
                with suppress(Exception):
                    await target_page.collect_garbage()
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

        await self._ensure_browser_cdp(connect_page=False)
        assert self._cdp_client is not None
        # D03-025: 控制面 cookie 导入使用临时页，完成后立即关闭。
        page = await self._cdp_client.create_new_page(block_assets=False)
        try:
            # P3-F: the temp import page navigates to Google/AI Studio, so it
            # needs the imported account's fingerprint before that navigation
            # (same switch and auth-file identity as transition_to_account).
            if resolve_fingerprint_spoof_enabled():
                profile = build_fingerprint_profile(auth_file=target_auth)
                await apply_fingerprint(page, profile, enabled=True)

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
        target = page or (self._active_context.page if self._active_context else None)
        return await extract_account_email(target)

    async def extract_account_tier(self, page: CDPPage | None = None) -> str | None:
        target = page or (self._active_context.page if self._active_context else None)
        auth_user = self.get_current_auth_user()
        return await extract_account_tier(target, auth_user=auth_user)

    async def reload_current_account_cookies(self, page: CDPPage | None = None) -> bool:
        target = page or (self._active_context.page if self._active_context else None)
        return bool(target and await load_cookies_to_page(target, self._auth_file))

    async def save_current_cookies(self) -> bool:
        """D03-026: 关闭前把当前活跃页面的 cookies 回写到当前账号 auth.json。"""
        target = self.page
        if target is None or target.is_closed() or not self._auth_file:
            return False
        return await save_browser_cookies(target, auth_file=self._auth_file)

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

    async def ensure_cdp_client(self) -> CDPClient:
        """Return the browser-level CDP client without opening a control page."""
        await self._ensure_browser_cdp(connect_page=False)
        if self._cdp_client is None:
            raise RuntimeError("CDP client unavailable")
        return self._cdp_client

    async def _ensure_browser_cdp(self, *, connect_page: bool = True) -> CDPPage | None:
        """Ensure browser process/CDP exists.

        ``connect_page=False`` only guarantees the process and CDP endpoint; the
        caller then creates exactly one page for the new session context.
        """
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
        if not connect_page:
            return None
        return await self._cdp_client.connect_page(block_assets=True)

    async def close(self) -> None:
        async with self._transition_lock:
            with suppress(Exception):
                await self.save_current_cookies()
            await self._close_internal()

    async def _close_internal(self) -> None:
        active_ctx = self._active_context
        self._active_context = None
        self._preparing = False
        self._last_error = None
        self._ready_event.clear()
        if active_ctx is not None:
            with suppress(Exception):
                if not active_ctx.page.is_closed():
                    await active_ctx.page.close()
        await self._stop_browser()

    async def _stop_browser(self) -> None:
        if self._cdp_client is not None:
            with suppress(Exception):
                await self._cdp_client.close()
            self._cdp_client = None

        if self._proc is not None:
            with suppress(Exception):
                self._proc.terminate()
            self._proc = None


__all__ = [
    "DEFAULT_BOOTSTRAP_TEMPLATE",
    "DRAIN_TIMEOUT_S",
    "BrowserSession",
    "_is_aistudio_page_url",
    "_is_login_page_url",
    "build_snapshot_hash_parts",
]
