"""Hook-first request capture workflow."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from aistudio_api.config import DEFAULT_TEXT_MODEL
from aistudio_api.infrastructure.gateway.session_protocol import RequestSession
from aistudio_api.infrastructure.gateway.wire_codec import modify_body
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("capture")


@dataclass
class CapturedRequest:
    url: str
    headers: dict[str, str]
    body: str
    model: str = ""
    snapshot: str = ""


class RequestCaptureService:
    """Single-page hook flow modeled after camoufox-api."""

    def __init__(self, session: RequestSession):
        self._session = session
        self._templates: dict[str, CapturedRequest] = {}
        self._active_ctx_id: int | None = None
        self._lock = asyncio.Lock()

    def clear_templates(self) -> bool:
        """清空捕获的请求模板（账号切换或强制刷新时调用）。

        Returns True only when there was something to clear; repeated calls on
        an already-empty cache stay silent so one 403/switch path cannot emit
        duplicate INFO lines.
        """
        if not self._templates:
            return False
        self._templates.clear()
        logger.info("已清空请求模板缓存")
        return True

    async def refresh_auth_state(self) -> bool:
        """Drop the session's cached cookie/signature state and refetch it."""
        try:
            return bool(await self._session.refresh_auth_state())
        except Exception as exc:
            logger.warning("刷新鉴权态失败: %s", exc)
            return False

    async def capture(
        self,
        prompt: str,
        model: str = DEFAULT_TEXT_MODEL,
        images: list[str | tuple[str, str]] | None = None,
        contents: list[AistudioContent] | None = None,
        system_instruction: str | None = None,
        system_instruction_content: AistudioContent | None = None,
        tools: list[list] | None = None,
        tool_config: list[object] | None = None,
        safety_settings: list[list] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        generation_config_overrides: dict | None = None,
        sanitize_plain_text: bool = True,
        force_refresh: bool = False,
    ) -> CapturedRequest | None:
        if force_refresh:
            self._templates.pop(model, None)
        template = await self._ensure_template(model)
        rewritten_contents = contents
        snapshot_contents = rewritten_contents or [
            AistudioContent.from_user_prompt(prompt=prompt, images=images)
        ]

        # 签名是一次性的票据（根据请求内容穿进 snapshot 计算），每次请求实时生成，不可复用缓存
        snapshot = await self._session.generate_snapshot(snapshot_contents)

        body = modify_body(
            template.body,
            model=model,
            prompt=prompt,
            contents=rewritten_contents,
            system_instruction=system_instruction,
            system_instruction_content=system_instruction_content,
            tools=tools,
            tool_config=tool_config,
            safety_settings=safety_settings,
            images=images,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_tokens=max_tokens,
            generation_config_overrides=generation_config_overrides,
            sanitize_plain_text=sanitize_plain_text,
            snapshot=snapshot,
        )
        captured = CapturedRequest(
            url=template.url,
            headers=dict(template.headers),
            body=body,
            model=model,
            snapshot=snapshot,
        )
        logger.info(
            "捕获请求模板成功: model=%s, 快照长度=%d, 请求体长度=%d",
            captured.model,
            len(captured.snapshot),
            len(captured.body),
        )
        return captured

    async def _ensure_template(self, model: str) -> CapturedRequest:
        ctx = await self._session.ensure_ready()
        ctx_id = ctx.context_id
        if self._active_ctx_id != ctx_id:
            self._templates.clear()
            self._active_ctx_id = ctx_id

        if model in self._templates:
            return self._templates[model]

        async with self._lock:
            if model in self._templates:
                return self._templates[model]

            captured = await self._session.capture_template(model)
            headers_dict = captured.get("headers")
            headers = {
                str(k): str(v)
                for k, v in (
                    headers_dict.items() if isinstance(headers_dict, dict) else []
                )
            }
            template = CapturedRequest(
                url=str(captured.get("url") or ""),
                headers=headers,
                body=str(captured.get("body") or ""),
                model=model,
            )
            self._templates[model] = template
            logger.info("请求模板已就绪并缓存: model=%s", model)
            return template
