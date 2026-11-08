"""BotGuard bootstrap recovery contracts (D03-021)."""

from __future__ import annotations

from typing import Any, cast

import pytest

from aistudio_api.infrastructure.browser.scripts import INSTALL_HOOKS_JS
from aistudio_api.infrastructure.gateway.botguard_bootstrap import (
    bootstrap_botguard_and_template,
)
from aistudio_api.infrastructure.gateway.public_web_constants import (
    AI_STUDIO_HOME_URL,
)


class FakeBotguardPage:
    def __init__(self) -> None:
        self.url = "https://aistudio.google.com/prompts/new_chat"
        self.reload_calls = 0
        self.goto_urls: list[str] = []
        self.login_armed = True

    def is_closed(self) -> bool:
        return False

    def on_request(self, callback):
        self._request_callback = callback
        return lambda: None

    async def title(self) -> str:
        return "AI Studio"

    async def wait_for_timeout(self, ms: float) -> None:
        return None

    async def wait_for_selector(self, selector: str, timeout_s: float = 15.0) -> bool:
        return True

    async def fill(self, selector: str, value: str) -> bool:
        return True

    async def goto(self, url: str) -> str:
        self.goto_urls.append(url)
        self.url = url
        return url

    async def evaluate(self, expression: str, args=None, **kwargs):
        if expression == INSTALL_HOOKS_JS:
            return "hooked:snapKey"
        if "textarea')?.value" in expression:
            return "original prompt"
        if "temporary chat" in expression:
            return False
        if "Code and Chat" in expression:
            return None
        if "const buttons = Array.from" in expression:
            if self.login_armed:
                # 触发 Run 后页面被重定向到登录页，模拟 Cookie 过期。
                self.url = "https://accounts.google.com/signin"
                self.login_armed = False
            return True
        if "__AISTUDIO__?.service" in expression:
            return True
        if "capturedTemplate" in expression:
            return {
                "url": "https://example.invalid/GenerateContent",
                "headers": {"X-Test": "1"},
                "body": '["models/gemini-test"]',
            }
        return None

    async def send_control_enter(self, selector: str = "textarea") -> bool:
        return True


@pytest.mark.asyncio
async def test_login_redirect_reloads_cookies_returns_to_aistudio_and_captures():
    """D03-021: login -> reload -> back to AI Studio -> continue polling -> success."""
    page = FakeBotguardPage()

    async def reload_cookies(target: FakeBotguardPage) -> bool:
        target.reload_calls += 1
        return True

    snap_key, template = await bootstrap_botguard_and_template(
        cast(Any, page),
        reload_cookies_cb=cast(Any, reload_cookies),
        max_wait_seconds=3,
    )

    assert page.reload_calls == 1
    assert page.goto_urls == [AI_STUDIO_HOME_URL]
    assert snap_key == "snapKey"
    assert template["body"] == '["models/gemini-test"]'
