"""Browser-backed AI Studio client facade."""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Any, ClassVar

from aistudio_api.config import (
    DEFAULT_BROWSER_PORT,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_TEXT_MODEL,
    settings,
)
from aistudio_api.domain.errors import RequestError, classify_error
from aistudio_api.domain.models import Candidate, ModelOutput
from aistudio_api.infrastructure.gateway.build_surface import BuildSurface
from aistudio_api.infrastructure.gateway.capture import (
    CapturedRequest,
    RequestCaptureService,
)
from aistudio_api.infrastructure.gateway.instance_pool import InstancePool
from aistudio_api.infrastructure.gateway.model_defaults import (
    get_features_config,
    resolve_model_defaults,
)
from aistudio_api.infrastructure.gateway.replay import RequestReplayService
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.streaming import StreamingGateway
from aistudio_api.infrastructure.gateway.wire_codec import (
    build_image_generation_search_tool,
    build_tools_from_names,
)
from aistudio_api.infrastructure.gateway.wire_parser import (
    parse_image_output,
    parse_text_output,
)
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("client")


class AIStudioClient:
    IMAGE_SIZE_TO_OUTPUT_RESOLUTION: ClassVar[dict[str, list[str]]] = {
        # 1:1
        "512x512": ["1:1", "512"],
        "1024x1024": ["1:1", "1K"],
        "2048x2048": ["1:1", "2K"],
        "4096x4096": ["1:1", "4K"],
        # 16:9
        "1792x1024": ["16:9", "1K"],
        # 9:16
        "1024x1792": ["9:16", "1K"],
        # 4:3
        "1365x1024": ["4:3", "1K"],
        # 3:4
        "1024x1365": ["3:4", "1K"],
        # 3:2
        "1536x1024": ["3:2", "1K"],
        # 2:3
        "1024x1536": ["2:3", "1K"],
    }

    def __init__(self, port: int = DEFAULT_BROWSER_PORT):
        self.port = port
        self._captured: CapturedRequest | None = None
        self._session = BrowserSession(port=port)
        self._capture_service = RequestCaptureService(self._session)
        self._replay_service = RequestReplayService(session=self._session)

        self._streaming_gateway = StreamingGateway(session=self._session)
        self._build_surface: BuildSurface | None = None
        self._realm_capture: dict[str, RequestCaptureService] = {}
        self._realm_streaming: dict[str, StreamingGateway] = {}
        self._realm_replay: dict[str, RequestReplayService] = {}
        self._instance_pool = InstancePool(
            self._ensure_browser_cdp_client,
            features_provider=get_features_config,
            on_slot_retired=self._forget_slot_services,
        )

    @property
    def session(self) -> BrowserSession:
        """Expose active browser session facade."""
        return self._session

    @property
    def instance_pool(self) -> InstancePool:
        """Unified request-context pool (v3); control session is not a slot."""
        return self._instance_pool

    async def _ensure_browser_cdp_client(self):
        return await self._session.ensure_browser_cdp_client()

    def _forget_slot_services(self, realm_id: str) -> None:
        self._realm_capture.pop(realm_id, None)
        self._realm_streaming.pop(realm_id, None)
        self._realm_replay.pop(realm_id, None)

    @property
    def build_in_flight_count(self) -> int:
        """Build 面在途任务数（无 build surface 时为 0）。"""
        surface = self._build_surface
        state = getattr(surface, "state", None)
        try:
            return int(getattr(state, "in_flight_count", 0) or 0)
        except Exception:
            return 0

    @property
    def in_flight_count(self) -> int:
        """Control session + request pool aggregate in-flight count."""
        realm_in_flight = 0
        for capture in self._realm_capture.values():
            session = getattr(capture, "_session", None)
            try:
                realm_in_flight += int(getattr(session, "in_flight_count", 0) or 0)
            except Exception:
                continue
        pool_in_flight = sum(slot.in_flight for slot in self._instance_pool.slots())
        realm_in_flight = max(realm_in_flight, pool_in_flight)
        return (
            self._session.in_flight_count + self.build_in_flight_count + realm_in_flight
        )

    def _ensure_realm_services(
        self, lease
    ) -> tuple[RequestCaptureService, StreamingGateway]:
        adapter: Any = getattr(lease, "adapter", None)
        session = getattr(adapter, "session", None)
        if session is None:
            return self._capture_service, self._streaming_gateway
        realm_id = str(getattr(adapter, "realm_id", ""))
        capture = self._realm_capture.get(realm_id)
        if capture is None:
            capture = RequestCaptureService(session)
            self._realm_capture[realm_id] = capture
        stream = self._realm_streaming.get(realm_id)
        if stream is None:
            stream = StreamingGateway(session=session)
            self._realm_streaming[realm_id] = stream
        return capture, stream

    def _replay_for(self, lease) -> RequestReplayService:
        adapter = getattr(lease, "adapter", None)
        session = getattr(adapter, "session", None)
        if session is None:
            return self._replay_service
        realm_id = str(getattr(adapter, "realm_id", ""))
        service = self._realm_replay.get(realm_id)
        if service is None:
            service = RequestReplayService(session=session)
            self._realm_replay[realm_id] = service
        return service

    async def _ensure_lease_build_surface(self, lease) -> BuildSurface:
        adapter = getattr(lease, "adapter", None)
        if adapter is None:
            return await self._ensure_build_surface()
        cdp = await self._session.ensure_browser_cdp_client()
        return await adapter.ensure_build_surface(cdp)

    @property
    def is_busy(self) -> bool:
        return self.in_flight_count > 0

    async def save_current_cookies(self) -> bool:
        """把当前账号 cookies 回写 auth.json。"""
        return await self._session.save_current_cookies()

    async def save_pool_cookies(self) -> int:
        """Write cookies for every live request slot to its account auth file."""
        saved = 0
        for slot in self._instance_pool.slots():
            session = slot.session
            if session is None or not slot.chat_ready():
                continue
            with suppress(Exception):
                if await session.save_current_cookies():
                    saved += 1
        return saved

    async def warmup(self) -> None:
        """预热浏览器后端并加载 AI Studio 页面及捕获 BotGuard 服务。"""
        if self._session is not None:
            await self._session.ensure_botguard_service()
            logger.info("浏览器预热完成")

    async def switch_auth(self, auth_file: str | None) -> None:
        """切换账号的 auth 文件并清空模板缓存。"""
        with suppress(Exception):
            await self._session.save_current_cookies()
        self.clear_templates()
        await self._close_build_surface()
        if self._session is not None:
            await self._session.switch_auth(auth_file)

    def clear_templates(self) -> None:
        """清除捕获的请求模板。"""
        if getattr(self, "_capture_service", None) is not None:
            self._capture_service.clear_templates()

    async def _ensure_build_surface(self) -> BuildSurface:
        """Lazily create the Build surface bound to the current browser."""
        if self._build_surface is None:
            cdp = await self._session.ensure_browser_cdp_client()
            self._build_surface = BuildSurface(cdp)
        return self._build_surface

    async def _close_build_surface(self) -> None:
        """Close the Build surface page (best effort, never raises)."""
        surface = self._build_surface
        self._build_surface = None
        if surface is None:
            return
        page = getattr(getattr(surface, "state", None), "page", None)
        if page is not None and not page.is_closed():
            with suppress(Exception):
                await page.close()

    def _build_surface_page_alive(self) -> bool:
        """Whether the Build surface currently holds a live page (read-only)."""
        surface = self._build_surface
        if surface is None:
            return False
        page = getattr(getattr(surface, "state", None), "page", None)
        if page is None:
            return False
        try:
            return not page.is_closed()
        except Exception:
            return False

    def _count_live_page_instances(self) -> int:
        """Current live page-instance count: chat page (1) + build page (1) (read-only)."""
        count = 0
        ctx = self._session.active_context
        chat_page = ctx.page if ctx is not None else None
        if chat_page is not None:
            try:
                if not chat_page.is_closed():
                    count += 1
            except Exception:
                pass
        if self._build_surface_page_alive():
            count += 1
        return count

    def _active_account_identity(self) -> tuple[str | None, str | None]:
        """Return the active account's (tier, email), or (None, None) if unknown."""
        try:
            from aistudio_api.infrastructure.account.account_store import AccountStore

            account = AccountStore().get_active_account()
        except Exception:
            account = None
        if account is None:
            return None, None
        return getattr(account, "tier", None), getattr(account, "email", None)

    @staticmethod
    def _features_config() -> dict[str, object]:
        try:
            from aistudio_api.infrastructure.gateway.model_defaults import (
                get_features_config,
            )

            return dict(get_features_config())
        except Exception:
            return {}

    @staticmethod
    def _policy_to_request_error(exc: Exception) -> RequestError:
        """Map a SurfacePolicyError into a request-level error carrying code/advice."""
        status = (
            getattr(exc, "http_status", None)
            or getattr(exc, "status_code", None)
            or getattr(exc, "status", None)
            or 429
        )
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None) or str(exc)
        advice = getattr(exc, "advice", None)
        detail = str(message)
        if code:
            detail = f"[{code}] {detail}"
        if advice:
            detail = f"{detail} | 处置建议: {advice}"
        return RequestError(int(status), detail)

    def _enforce_build_instance_quota(self) -> None:
        """WP5: enforce the account group's page-instance quota before BuildSurface.ensure_ready.

        Uses the ``application.surface_orchestrator`` policy module
        (``match_group`` / ``check_instance_quota`` / ``SurfacePolicyError``):
        the active account's tier/email + ``features.groups`` resolve the group,
        and the live page-instance count (chat page + build page) decides whether a
        new instance would exceed ``max_page_instances``. Over-quota is raised as a
        request-level error carrying the policy ``code``/``message``/``advice`` —
        never swallowed, never silently routed.
        """
        try:
            from aistudio_api.application import surface_orchestrator as orchestrator
        except Exception:
            logger.warning("surface_orchestrator 尚未就绪，跳过 build 面实例配额检查")
            return
        match_group = getattr(orchestrator, "match_group", None)
        check = getattr(orchestrator, "check_instance_quota", None)
        policy_error = getattr(orchestrator, "SurfacePolicyError", None)
        if match_group is None or check is None or policy_error is None:
            logger.warning(
                "surface_orchestrator 契约不完整(match_group/"
                "check_instance_quota/SurfacePolicyError)，跳过 build 面配额检查"
            )
            return

        tier, email = self._active_account_identity()
        features = self._features_config()
        groups = features.get("groups") or []
        group = match_group(tier, email, groups)
        if isinstance(group, dict):
            group_name = str(group.get("name") or "unnamed")
            max_instances = group.get("max_page_instances")
        else:
            group_name = f"default:{tier or 'unknown'}"
            max_instances = None
        live_instances = self._count_live_page_instances()
        need_new = not self._build_surface_page_alive()
        logger.info(
            "[WP5] build 面配额检查: group=%s tier=%s max=%s live=%s need_new=%s",
            group_name,
            tier,
            max_instances,
            live_instances,
            need_new,
        )
        try:
            # check_instance_quota(group_name, max_instances, live_instances, surface, need_new)
            check(group_name, max_instances, live_instances, "build", need_new)
        except policy_error as exc:
            raise self._policy_to_request_error(exc) from exc

    def surface_instance_snapshot(self) -> dict[str, object]:
        """WP5 v3 queue/instance snapshot (read-only, never raises)."""
        snapshot: dict[str, object] = {
            "active_account": None,
            "contexts": [],
            "pages": {"chat": 0, "build": 0},
            "live_instances": 0,
            "context_count": 0,
            "planned_instances": 0,
            "budgets": {},
            "control_session": {"alive": False, "url": None},
        }
        try:
            account = None
            try:
                from aistudio_api.infrastructure.account.account_store import (
                    AccountStore,
                )

                account = AccountStore().get_active_account()
            except Exception:
                account = None
            if account is not None:
                snapshot["active_account"] = {
                    "id": account.id,
                    "name": getattr(account, "name", None),
                    "tier": getattr(account, "tier", None),
                    "email": getattr(account, "email", None),
                }

            session = self._session
            ctx = session.active_context if session is not None else None
            page = ctx.page if ctx is not None else None
            if page is not None:
                with suppress(Exception):
                    snapshot["control_session"] = {
                        "alive": not page.is_closed(),
                        "url": page.url,
                    }

            pool = self._instance_pool
            contexts = pool.snapshot()
            pages = pool.page_counts()
            snapshot["contexts"] = contexts
            snapshot["pages"] = pages
            snapshot["live_instances"] = sum(pages.values())
            snapshot["context_count"] = pool.live_contexts
            snapshot["budgets"] = pool.budget_snapshot()
            features = self._features_config()
            raw_slots = features.get("slots")
            slots = raw_slots if isinstance(raw_slots, list) else []
            snapshot["planned_instances"] = sum(
                1
                for slot in slots
                if isinstance(slot, dict) and bool(slot.get("enabled", True))
            )
        except Exception:
            pass
        return snapshot

    async def close(self) -> None:
        """关闭浏览器后端。"""
        await self._instance_pool.close()
        await self._close_build_surface()
        if self._session is not None:
            await self._session.close()

    def _dump_raw_exchange(
        self,
        *,
        kind: str,
        model: str,
        capture_prompt: str,
        modified_body: str,
        raw_response: str,
    ) -> None:
        if not settings.dump_raw_response:
            return

        out_dir = Path(settings.dump_raw_response_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_model = model.replace("/", "_")
        timestamp = __import__("time").strftime("%Y%m%d_%H%M%S")
        payload = {
            "kind": kind,
            "model": model,
            "capture_prompt": capture_prompt,
            "modified_body": json.loads(modified_body),
            "raw_response": raw_response,
        }
        path = out_dir / f"aistudio_{kind}_{safe_model}_{timestamp}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        logger.info("已落盘原始请求/响应: %s", path)

    async def capture_request(
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
        lease=None,
    ) -> CapturedRequest | None:
        capture, _ = self._ensure_realm_services(lease)
        return await capture.capture(
            prompt=prompt,
            model=model,
            images=images,
            contents=contents,
            system_instruction=system_instruction,
            system_instruction_content=system_instruction_content,
            tools=tools,
            tool_config=tool_config,
            safety_settings=safety_settings,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_tokens=max_tokens,
            generation_config_overrides=generation_config_overrides,
            sanitize_plain_text=sanitize_plain_text,
            force_refresh=force_refresh,
        )

    async def replay(self, body: str, timeout: int = 120) -> tuple[int, bytes]:
        return await self._replay_service.replay(
            self._captured, body=body, timeout=timeout
        )

    async def stream_generate_content(
        self,
        *,
        model: str = DEFAULT_TEXT_MODEL,
        capture_prompt: str,
        capture_images: list[str | tuple[str, str]] | None = None,
        contents: list[AistudioContent] | None = None,
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
        force_refresh_capture: bool = False,
        surface: str = "chat",
        rest_body: str | None = None,
        lease=None,
    ):
        adapter = getattr(lease, "adapter", None)
        session = getattr(adapter, "session", None) or self._session
        if surface == "build":
            if not rest_body:
                raise RequestError(
                    400, "build surface requires a REST body (rest_body)"
                )
            if lease is None:
                self._enforce_build_instance_quota()
            ctx = session.active_context
            if ctx is None:
                ctx = await session.ensure_ready()
            build_surface = await self._ensure_lease_build_surface(lease)
            await build_surface.ensure_ready(
                ctx.auth_file,
                ctx.auth_user,
                account_id=getattr(lease, "account_id", None),
            )
            async for event in build_surface.stream_generate(
                model=model, json_body=rest_body
            ):
                yield event
            return

        _, stream_gateway = self._ensure_realm_services(lease)
        async with session.request_scope():
            captured = await self.capture_request(
                prompt=capture_prompt,
                model=model,
                images=capture_images,
                contents=contents,
                system_instruction_content=system_instruction_content,
                tools=tools,
                tool_config=tool_config,
                safety_settings=safety_settings,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                max_tokens=max_tokens,
                generation_config_overrides=generation_config_overrides,
                sanitize_plain_text=sanitize_plain_text,
                force_refresh=force_refresh_capture,
                lease=lease,
            )
            if not captured:
                raise RequestError(0, "无法拦截请求")
            async for event in stream_gateway.stream_chat(
                captured=captured,
                model=model,
            ):
                yield event

    async def _collect_build_output(
        self,
        *,
        model: str,
        json_body: str,
        build_surface: BuildSurface,
    ) -> ModelOutput:
        """Collect the build event stream into a non-streaming ModelOutput."""
        candidate = Candidate()
        output = ModelOutput(model=model)
        async for event_type, payload in build_surface.stream_generate(
            model=model, json_body=json_body
        ):
            if event_type == "status":
                status = int(str(payload or 0))
                if status >= 400:
                    raise RequestError(status, f"build surface status {status}")
            elif event_type == "body" and payload:
                candidate.text += str(payload)
            elif event_type == "thinking" and payload:
                candidate.thinking += str(payload)
            elif event_type == "tool_calls" and isinstance(payload, list):
                candidate.function_calls.extend(payload)
            elif event_type == "images" and isinstance(payload, list):
                candidate.images.extend(payload)
            elif event_type == "reasoning_images" and isinstance(payload, list):
                candidate.reasoning_images.extend(payload)
            elif event_type == "thought_signature" and payload:
                candidate.thought_signature = str(payload)
            elif event_type == "response_id" and payload:
                output.response_id = str(payload)
            elif event_type == "finish_reason" and isinstance(payload, int):
                candidate.finish_reason = payload
            elif event_type == "usage" and isinstance(payload, dict):
                output.usage = payload
        output.candidates = [candidate]
        return output

    async def generate_content(
        self,
        *,
        model: str = DEFAULT_TEXT_MODEL,
        capture_prompt: str,
        capture_images: list[str | tuple[str, str]] | None = None,
        contents: list[AistudioContent] | None = None,
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
        surface: str = "chat",
        rest_body: str | None = None,
        lease=None,
    ) -> ModelOutput:
        adapter = getattr(lease, "adapter", None)
        session = getattr(adapter, "session", None) or self._session
        if surface == "build":
            if not rest_body:
                raise RequestError(
                    400, "build surface requires a REST body (rest_body)"
                )
            if lease is None:
                self._enforce_build_instance_quota()
            ctx = session.active_context
            if ctx is None:
                ctx = await session.ensure_ready()
            build_surface = await self._ensure_lease_build_surface(lease)
            await build_surface.ensure_ready(
                ctx.auth_file,
                ctx.auth_user,
                account_id=getattr(lease, "account_id", None),
            )
            return await self._collect_build_output(
                model=model,
                json_body=rest_body,
                build_surface=build_surface,
            )

        replay = self._replay_for(lease)
        async with session.request_scope():
            logger.info("拦截请求: %r", f"{capture_prompt[:20]}...")
            captured = await self.capture_request(
                prompt=capture_prompt,
                model=model,
                images=capture_images,
                contents=contents,
                system_instruction_content=system_instruction_content,
                tools=tools,
                tool_config=tool_config,
                safety_settings=safety_settings,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                max_tokens=max_tokens,
                generation_config_overrides=generation_config_overrides,
                sanitize_plain_text=sanitize_plain_text,
                lease=lease,
            )
            if not captured:
                raise RequestError(0, "无法拦截请求")

            modified_body = captured.body

            status, raw = await replay.replay(captured, body=modified_body)
            raw_text = raw.decode("utf-8", errors="replace")
            self._dump_raw_exchange(
                kind="generate_content",
                model=model,
                capture_prompt=capture_prompt,
                modified_body=modified_body,
                raw_response=raw_text,
            )
            if status != 200:
                raise classify_error(status, raw_text)
            output = parse_text_output(raw_text)
            output.model = model
            return output

    @classmethod
    def resolve_image_size(cls, size: str) -> list[str] | None:
        """将 OpenAI 风格的 size 映射为 AI Studio 的生图尺寸配置。"""
        return cls.IMAGE_SIZE_TO_OUTPUT_RESOLUTION.get(size)

    async def generate_image(
        self,
        prompt: str,
        model: str = DEFAULT_IMAGE_MODEL,
        save_path: str | None = None,
        size: str = "1024x1024",
        google_search: bool = False,
        image_search: bool = False,
        use_default_tools: bool = True,
        images: list[str | tuple[str, str]] | None = None,
        contents: list[AistudioContent] | None = None,
    ) -> ModelOutput:
        async with self._session.request_scope():
            logger.info(
                "生图请求: %r, images=%s",
                f"{prompt[:20]}...",
                len(images) if images else 0,
            )
            request_contents = contents or [
                AistudioContent.from_user_prompt(prompt=prompt, images=images)
            ]
            generation_config_overrides = None
            output_resolution = self.resolve_image_size(size)
            if output_resolution is not None:
                generation_config_overrides = {"output_resolution": output_resolution}
            model_defaults = resolve_model_defaults(model)
            resolved_tools: list[list[object]] | None = None
            if google_search or image_search:
                tool = build_image_generation_search_tool(
                    google_search=google_search,
                    image_search=image_search,
                )
                if tool is not None:
                    resolved_tools = [tool]
            elif use_default_tools and model_defaults.default_tools:
                resolved_tools = (
                    build_tools_from_names(
                        model_defaults.default_tools,
                        model=model,
                        is_image_model=model_defaults.is_image_model,
                    )
                    or None
                )

            captured = await self.capture_request(
                prompt=prompt,
                model=model,
                images=images,
                contents=request_contents,
                tools=resolved_tools,
                generation_config_overrides=generation_config_overrides,
            )
            if not captured:
                raise RequestError(0, "无法拦截请求")

            modified_body = captured.body
            status, raw = await self._replay_service.replay(
                captured, body=modified_body, timeout=120
            )
            raw_text = raw.decode("utf-8", errors="replace")
            self._dump_raw_exchange(
                kind="generate_image",
                model=model,
                capture_prompt=prompt,
                modified_body=modified_body,
                raw_response=raw_text,
            )
            if status != 200:
                raise classify_error(status, raw_text)
            output = parse_image_output(raw_text)
            output.model = model

            if output.images and save_path:
                img = output.images[0]
                ext = "jpg" if "jpeg" in img.mime else "png"
                path = (
                    Path(save_path)
                    if save_path.endswith(f".{ext}")
                    else Path(f"{save_path}.{ext}")
                )
                path.write_bytes(img.data)
                logger.info("图片已保存: %s (%s bytes)", path, img.size)

            return output


__all__ = ["AIStudioClient", "CapturedRequest"]
