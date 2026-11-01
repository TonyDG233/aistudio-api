"""动态模型发现与元数据获取服务。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aistudio_api.infrastructure.browser.cdp_client import CDPPage

from aistudio_api.domain.model_access import (
    is_paid_model as _is_paid_model,
    set_paid_models,
    tier_header_value,
)
from aistudio_api.infrastructure.account.cookie_parser import (
    calculate_sapisid_hash,
)
from aistudio_api.infrastructure.gateway.public_web_constants import (
    AI_STUDIO_HOME_URL,
    AI_STUDIO_ORIGIN,
    EXT_BIN,
    GRPC_WEB_USER_AGENT,
    LIST_MODELS_URL,
    WEB_API_KEY,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("model_discovery")


# ListModels field 65 (index 64) 主能力码语义映射
MODEL_CAPABILITY_NAMES: dict[int, str] = {
    1: "chat_model",
    9: "code_execution",
    10: "function_declarations",
    12: "google_search",
    13: "url_context",
    20: "video_route",
    21: "image_route",
    25: "thinking",
    26: "live_route",
    35: "thinking_budget",
    37: "speech_route",
    43: "media_resolution",
    46: "speech_translation",
    47: "aspect_ratio",
    48: "code_assistant",
    49: "output_resolution",
    52: "thinking_level",
    53: "music_route",
    54: "image_search",
    58: "google_maps",
    59: "interaction_route",
    85: "speech_metadata",
    91: "output_continuation",
}


def _extract_int_list(raw: object) -> list[int]:
    if not isinstance(raw, list):
        return []
    res: list[int] = []
    for item in raw:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            res.append(item)
        elif isinstance(item, float) and item.is_integer():
            res.append(int(item))
    return res


def _extract_model_aliases(raw: object) -> list[str]:
    if not isinstance(raw, list) or not raw:
        return []
    if isinstance(raw[0], str):
        return [
            s.removeprefix("models/") for s in raw if isinstance(s, str) and s.strip()
        ]
    aliases: list[str] = []
    for item in raw:
        if (
            isinstance(item, list)
            and item
            and isinstance(item[0], str)
            and item[0].strip()
        ):
            aliases.append(item[0].removeprefix("models/"))
    return aliases


def _session_context(session: object | None) -> object | None:
    return getattr(session, "active_context", None) if session is not None else None


def _session_auth_file(session: object | None) -> str | None:
    if session is None:
        return None
    value = getattr(session, "_auth_file", None)
    if isinstance(value, str) and value:
        return value
    context = _session_context(session)
    if context is not None:
        value = getattr(context, "auth_file", None)
        if isinstance(value, str) and value:
            return value
    return None


def _session_auth_user(session: object | None) -> str:
    if session is None:
        return "0"
    context = _session_context(session)
    if context is not None:
        value = getattr(context, "auth_user", None)
        if isinstance(value, str) and value:
            return value
    getter = getattr(session, "get_current_auth_user", None)
    if callable(getter):
        try:
            value = getter()
            if isinstance(value, str) and value:
                return value
        except Exception:
            pass
    return "0"


def _session_tier(session: object | None) -> str:
    if session is None:
        return "Free"
    context = _session_context(session)
    if context is not None:
        value = getattr(context, "tier", None)
        if isinstance(value, str) and value:
            return value
    getter = getattr(session, "get_current_tier", None)
    if callable(getter):
        try:
            value = getter()
            if isinstance(value, str) and value:
                return value
        except Exception:
            pass
    return "Free"


def _cookies_from_auth_file(auth_file: str | None) -> dict[str, str]:
    if not auth_file:
        return {}
    path = Path(auth_file)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    raw_cookies = data.get("cookies") or []
    return {
        str(c.get("name") or ""): str(c.get("value") or "")
        for c in raw_cookies
        if isinstance(c, dict) and c.get("name")
    }



# 本地保底默认模型列表（仅冷启动/网络未就绪时使用；live 数据永远优先）。
# 与主分支 16 条保底条目及 paid 标记保持一致，禁止把 fallback 冒充 live。
_FALLBACK_MODELS = [
    {
        "id": "gemini-3.8-flash",
        "displayName": "Gemini 3.8 Flash",
        "description": "Latest Gemini 3.8 Flash model",
        "paid": False,
    },
    {
        "id": "gemini-4-argon",
        "displayName": "Gemini 4 Argon",
        "description": "Our most capable model",
        "paid": True,
    },
    {
        "id": "gemini-3-flash-preview",
        "displayName": "Gemini 3 Flash Preview",
        "description": "Fast frontier model",
        "paid": True,
    },
    {
        "id": "gemini-3.7-flash",
        "displayName": "Gemini 3.7 Flash",
        "description": "Gemini 3.7 Flash default text model",
        "paid": False,
    },
    {
        "id": "gemini-3.5-flash-lite",
        "displayName": "Gemini 3.5 Flash Lite",
        "description": "Ultra fast and lightweight model",
        "paid": False,
    },
    {
        "id": "gemini-3.1-pro-preview",
        "displayName": "Gemini 3.1 Pro Preview",
        "description": "Pro capabilities with complex reasoning",
        "paid": False,
    },
    {
        "id": "gemini-3.6-flash",
        "displayName": "Gemini 3.6 Flash",
        "description": "High throughput flash model",
        "paid": True,
    },
    {
        "id": "gemini-3.5-flash",
        "displayName": "Gemini 3.5 Flash",
        "description": "Balanced high-performance flash model",
        "paid": False,
    },
    {
        "id": "gemini-3.1-flash-image",
        "displayName": "Nano Banana 2",
        "description": "Image generation and multimodal editing",
        "paid": True,
    },
    {
        "id": "gemini-3-pro-image",
        "displayName": "Nano Banana Pro",
        "description": "High-fidelity multimodal generation",
        "paid": True,
    },
    {
        "id": "gemma-4-31b-it",
        "displayName": "Gemma 4 31B IT",
        "description": "Open weights flagship text model",
        "paid": False,
    },
    {
        "id": "gemma-4-26b-a4b-it",
        "displayName": "Gemma 4 26B A4B IT",
        "description": "Open weights instruction tuned model",
        "paid": False,
    },
    {
        "id": "veo-3.1-generate-preview",
        "displayName": "Veo 3.1",
        "description": "Video generation preview model",
        "paid": True,
    },
    {
        "id": "gemini-pro-latest",
        "displayName": "Gemini Pro Latest",
        "description": "Latest stable Gemini Pro alias",
        "paid": False,
    },
    {
        "id": "gemini-flash-latest",
        "displayName": "Gemini Flash Latest",
        "description": "Latest stable Gemini Flash alias",
        "paid": False,
    },
    {
        "id": "gemini-flash-lite-latest",
        "displayName": "Gemini Flash-Lite Latest",
        "description": "Latest stable Gemini Flash Lite alias",
        "paid": False,
    },
]

LIVE_SOURCES = frozenset({"live_page", "live_http"})

# A "truth" record is a real discovery result (live fetch or persisted live
# snapshot), never the static cold-start fallback.  Routing uses this marker to
# tell an authoritative live Free result apart from `_FALLBACK_MODELS`.
DISCOVERY_TRUTH_SOURCES = frozenset({"live_page", "live_http", "snapshot"})
_DISCOVERY_SOURCE_KEY = "_discovery_source"


def mark_discovery_source(
    models: list[dict[str, object]], source: str
) -> list[dict[str, object]]:
    """Attach the provenance marker to every model entry (in place)."""
    for item in models:
        item[_DISCOVERY_SOURCE_KEY] = source
    return models


def discovery_entry_source(entry: object) -> str | None:
    if not isinstance(entry, Mapping):
        return None
    raw = entry.get(_DISCOVERY_SOURCE_KEY)
    return raw if isinstance(raw, str) else None


def discovery_entry_is_truth(entry: object) -> bool:
    """True only for live/snapshot records, never for static fallback."""
    return discovery_entry_source(entry) in DISCOVERY_TRUTH_SOURCES


def discovery_entry_paid_truth(entry: object) -> bool | None:
    """Return live Paid truth for a real record, or ``None`` when unknown.

    ``True`` = live Paid (``paid is True`` or accessModes 3/4); ``False`` = an
    explicit live ``paid is False`` with no Paid access mode; ``None`` means the
    entry is absent, fallback-only, or does not carry an explicit Paid marker.
    """
    if not discovery_entry_is_truth(entry):
        return None
    assert isinstance(entry, Mapping)
    raw_modes = entry.get("accessModes")
    modes = (
        {int(mode) for mode in raw_modes if isinstance(mode, int)}
        if isinstance(raw_modes, list)
        else set()
    )
    if entry.get("paid") is True or 3 in modes or 4 in modes:
        return True
    if entry.get("paid") is False and 3 not in modes and 4 not in modes:
        return False
    return None


class ModelDiscoveryService:
    """动态获取 Google AI Studio 支持的模型列表（带缓存与保底）。"""

    def __init__(self, cache_ttl_seconds: int = 600) -> None:
        self._cache_ttl = cache_ttl_seconds
        self._cached_models: list[dict[str, object]] = []
        self._last_fetched_at: float = 0.0
        self._lock = asyncio.Lock()
        # ``last_source`` must describe where the *returned* models came from.
        # It is never set to a live value unless a live fetch actually parsed.
        self.last_source: str = "fallback"
        self.last_error: str | None = None
        self._load_snapshot()

    @property
    def cache_ttl(self) -> int:
        return self._cache_ttl

    def _snapshot_path(self) -> Path:
        from aistudio_api.config import settings

        return Path(settings.model_cache_file)

    def _load_snapshot(self) -> None:
        try:
            path = self._snapshot_path()
            if not path.is_file():
                return
            payload = json.loads(path.read_text(encoding="utf-8"))
            models = payload.get("models") if isinstance(payload, dict) else None
            if not isinstance(models, list):
                return
            loaded = [dict(item) for item in models if isinstance(item, dict)]
            fetched_at = (
                payload.get("fetched_at") if isinstance(payload, dict) else None
            )
            self._cached_models = mark_discovery_source(loaded, "snapshot")
            self._last_fetched_at = (
                float(fetched_at) if isinstance(fetched_at, (int, float)) else 0.0
            )
            self.last_source = "snapshot"
            set_paid_models(self._cached_models)
            logger.info("从快照加载了 %d 个模型 (source=snapshot)", len(loaded))
        except Exception as e:
            logger.warning("读取模型快照失败: %s", e)

    def _save_snapshot(self, models: list[dict[str, object]]) -> None:
        if not models:
            return
        try:
            from aistudio_api.infrastructure.utils.common import atomic_write_json

            atomic_write_json(
                self._snapshot_path(),
                {
                    "version": 1,
                    "fetched_at": time.time(),
                    "models": models,
                },
            )
        except Exception as e:
            logger.warning("写入模型快照失败: %s", e)

    async def get_models(
        self,
        *,
        force_refresh: bool = False,
        cookies: dict[str, str] | None = None,
        auth_user: str = "0",
        session: Any | None = None,
        sessions: Any | None = None,
    ) -> list[dict[str, object]]:
        """获取可用模型列表。"""
        now = time.time()
        if (
            not force_refresh
            and self._cached_models
            and (now - self._last_fetched_at < self._cache_ttl)
        ):
            return self._cached_models

        async with self._lock:
            # 双重检查
            if (
                not force_refresh
                and self._cached_models
                and (time.time() - self._last_fetched_at < self._cache_ttl)
            ):
                return self._cached_models

            # The attempt error is kept for honest logging when we fall back.
            self.last_error = None

            # 1. 优先通过 READY 池会话（paid 先、Free 后）发 XHR 拉 ListModels
            candidates: list[Any] = []
            if sessions is not None:
                try:
                    candidates.extend(
                        item for item in sessions if item is not None
                    )
                except TypeError:
                    candidates.append(sessions)
            elif session is not None:
                candidates.append(session)

            session_cookies: dict[str, str] = {}
            for candidate in candidates:
                try:
                    page = getattr(candidate, "page", None) or getattr(
                        candidate, "_page", None
                    )
                    if page is None:
                        raise RuntimeError("session has no page")
                    if page.is_closed():
                        raise RuntimeError("session page is closed")
                    auth_file = _session_auth_file(candidate)
                    candidate_cookies = _cookies_from_auth_file(auth_file)
                    if not session_cookies:
                        session_cookies = candidate_cookies
                    auth_header = (
                        calculate_sapisid_hash(candidate_cookies)
                        if candidate_cookies
                        else None
                    )
                    models = await self._fetch_via_page(
                        page,
                        authorization=auth_header,
                        auth_user=_session_auth_user(candidate),
                        tier=_session_tier(candidate),
                    )
                    self._accept_live(models, source="live_page")
                    return models
                except Exception as e:
                    self.last_error = f"live_page: {e}"
                    logger.warning(
                        "通过页面 XHR 拉取模型列表失败 (session=%s): %s",
                        type(candidate).__name__,
                        e,
                    )
            if not candidates:
                self.last_error = "no discovery session available"

            # 2. 次选通过直接 HTTP 协议拉取（使用池会话对应账号的 Cookie）
            effective_cookies = cookies or session_cookies
            effective_auth_user = auth_user
            effective_tier = "Free"
            auth_source = session or (candidates[0] if candidates else None)
            auth_file = _session_auth_file(auth_source)
            if auth_source is not None:
                effective_auth_user = _session_auth_user(auth_source)
                effective_tier = _session_tier(auth_source)
            if not auth_file:
                try:
                    from aistudio_api.infrastructure.account.account_store import (
                        AccountStore,
                    )

                    store = AccountStore()
                    active = store.get_active_account()
                    if active:
                        auth_path = store.get_auth_path_optional(
                            active.id, require_exists=True
                        )
                        auth_file = str(auth_path) if auth_path else None
                        effective_auth_user = active.auth_user or "0"
                        effective_tier = getattr(active, "tier", "Free") or "Free"
                except Exception:
                    pass
            if not effective_cookies:
                effective_cookies = _cookies_from_auth_file(auth_file)

            if effective_cookies:
                try:
                    models = await self._fetch_via_http(
                        effective_cookies,
                        effective_auth_user,
                        tier=effective_tier,
                    )
                    self._accept_live(models, source="live_http")
                    return models
                except Exception as e:
                    self.last_error = f"live_http: {e}"
                    logger.warning("通过 HTTP 协议拉取模型列表失败: %s", e)

            # 3. 保底返回本地默认模型列表（仅冷启动；已有快照则保留快照来源）
            if not self._cached_models:
                self._cached_models = mark_discovery_source(
                    [dict(m) for m in _FALLBACK_MODELS], "fallback"
                )
                set_paid_models(self._cached_models)
                self._last_fetched_at = time.time()
                self.last_source = "fallback"
                logger.warning(
                    "模型发现降级为本地 fallback (source=fallback, error=%s)",
                    self.last_error or "unknown",
                )
            else:
                logger.warning(
                    "模型发现保持 %s 缓存 (source=%s, error=%s)",
                    len(self._cached_models),
                    self.last_source,
                    self.last_error or "unknown",
                )

            return self._cached_models

    def _accept_live(self, models: list[dict[str, object]], *, source: str) -> None:
        """Commit a live parse result and persist it with paid metadata."""
        if not models:
            raise RuntimeError("live discovery returned no models")
        self._cached_models = mark_discovery_source(
            [dict(item) for item in models], source
        )
        set_paid_models(self._cached_models)
        self._save_snapshot(self._cached_models)
        self._last_fetched_at = time.time()
        self.last_source = source
        self.last_error = None
        logger.info("模型发现更新: source=%s count=%d", source, len(models))

    async def get_model_by_id(
        self,
        model_id: str,
        session: Any | None = None,
        sessions: Any | None = None,
    ) -> Mapping[str, object] | None:
        """根据 model_id 或模型别名获取单个模型元数据（含保底匹配）。"""
        discovered = await self.get_models(session=session, sessions=sessions)
        clean_target = model_id.removeprefix("models/")
        for model_info in discovered:
            model_raw_id = str(model_info.get("id") or model_info.get("name") or "")
            model_clean_id = model_raw_id.removeprefix("models/")
            if model_clean_id == clean_target:
                return model_info
            raw_aliases = model_info.get("aliases")
            if isinstance(raw_aliases, list) and clean_target in raw_aliases:
                return model_info
        for model_info in _FALLBACK_MODELS:
            model_raw_id = str(model_info.get("id") or model_info.get("name") or "")
            model_clean_id = model_raw_id.removeprefix("models/")
            if model_clean_id == clean_target:
                return model_info
        return None

    def get_cached_model_info(self, model_id: str) -> Mapping[str, object] | None:
        """同步查询已缓存的模型元数据（不触发网络请求）。"""
        clean_target = model_id.removeprefix("models/").lower()
        for model_info in self._cached_models:
            model_raw_id = str(model_info.get("id") or model_info.get("name") or "")
            if model_raw_id.removeprefix("models/").lower() == clean_target:
                return model_info
            raw_aliases = model_info.get("aliases")
            if isinstance(raw_aliases, list) and any(
                str(a).lower() == clean_target for a in raw_aliases
            ):
                return model_info
        return None

    def is_model_allowed_for_tier(self, model_id: str, tier: str | None) -> bool:
        """根据 paid_model_rules 与缓存 accessModes 判断指定订阅等级是否可访问该模型。"""
        from aistudio_api.domain.model_access import tier_satisfies_required

        req_tiers = self.get_required_tiers(model_id)
        if req_tiers:
            return tier_satisfies_required(tier or "Free", req_tiers)
        return True

    def is_paid_model(self, model_id: str) -> bool:
        """判定目标模型是否为付费模型（唯一来源：domain.model_access）。"""
        return _is_paid_model(model_id)

    def get_required_tier(self, model_id: str) -> str:
        """获取目标模型所需的最低账号层级（兼容现有单层级调用）。"""
        from aistudio_api.domain.model_access import minimum_required_tier

        tiers = self.get_required_tiers(model_id)
        return minimum_required_tier(tiers) or "Free"

    def get_required_tiers(self, model_id: str) -> list[str]:
        """Resolve allowed tiers: live truth -> config rules -> accessModes/paid.

        A real (live/snapshot) ``paid is False`` record is authoritative and is
        returned as Free before any static substring rule applies, so a static
        ``image`` style rule can never override live Free truth.  Fallback or
        unknown models still use the static ``paid_model_rules`` first.
        """
        clean_m = model_id.removeprefix("models/").lower()

        info = self.get_cached_model_info(clean_m)
        truth = discovery_entry_paid_truth(info)
        if truth is False:
            return []

        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_configured_paid_model_rules,
        )

        rules = get_configured_paid_model_rules()
        for rule_cfg in rules.values():
            if not isinstance(rule_cfg, dict):
                continue
            contains = [
                str(c).lower()
                for c in (rule_cfg.get("contains") or [])
                if isinstance(c, str)
            ]
            prefixes = [
                str(p).lower()
                for p in (rule_cfg.get("prefixes") or [])
                if isinstance(p, str)
            ]
            exact = [
                str(e).removeprefix("models/").lower()
                for e in (rule_cfg.get("exact") or [])
                if isinstance(e, str)
            ]
            if (
                (exact and clean_m in exact)
                or any(clean_m.startswith(p) for p in prefixes)
                or any(c in clean_m for c in contains)
            ):
                req = rule_cfg.get("required_tiers")
                if isinstance(req, list) and req:
                    return [str(t) for t in req]

        if info:
            modes = info.get("accessModes")
            if isinstance(modes, list):
                if 4 in modes and 3 not in modes:
                    return ["Ultra", "Ultra_20X"]
                if 3 in modes:
                    return ["Pro", "Ultra", "Ultra_20X"]
            if bool(info.get("paid")):
                return ["Pro", "Ultra", "Ultra_20X"]

        if _is_paid_model(clean_m):
            return ["Pro", "Ultra", "Ultra_20X"]
        return []

    async def _fetch_via_page(
        self,
        page: CDPPage,
        *,
        authorization: str | None = None,
        auth_user: str = "0",
        tier: str = "Free",
    ) -> list[dict[str, object]]:
        """在 CDP Page 环境中直接执行 XHR 拉取 ListModels（自动带齐浏览器完整凭据）。"""
        script = """
        (opts) => {
            return new Promise((resolve) => {
                const xhr = new XMLHttpRequest();
                xhr.open('POST', opts.url);
                xhr.setRequestHeader('Content-Type', 'application/json+protobuf');
                xhr.setRequestHeader('X-User-Agent', opts.userAgent);
                xhr.setRequestHeader('X-Goog-Api-Key', opts.apiKey);
                xhr.setRequestHeader('X-Goog-Ext-519733851-bin', opts.extBin);
                if (opts && opts.authUser) {
                    xhr.setRequestHeader('X-Goog-AuthUser', String(opts.authUser));
                }
                if (opts && opts.authorization) {
                    xhr.setRequestHeader('Authorization', String(opts.authorization));
                }
                if (opts && opts.tierHeader) {
                    xhr.setRequestHeader('X-AIStudio-G1-Tier', String(opts.tierHeader));
                }
                xhr.withCredentials = true;
                xhr.timeout = 10000;
                xhr.onload = () => {
                    if (xhr.status === 200) {
                        try {
                            resolve({ok: true, data: JSON.parse(xhr.responseText)});
                        } catch(e) {
                            resolve({ok: false, error: String(e)});
                        }
                    } else {
                        resolve({ok: false, status: xhr.status});
                    }
                };
                xhr.onerror = () => resolve({ok: false, error: 'network_error'});
                xhr.ontimeout = () => resolve({ok: false, error: 'timeout'});
                xhr.send('[]');
            });
        }
        """
        res = await page.evaluate(
            script,
            args={
                "authorization": authorization,
                "authUser": auth_user or "0",
                "tierHeader": tier_header_value(tier),
                "url": LIST_MODELS_URL,
                "apiKey": WEB_API_KEY,
                "userAgent": GRPC_WEB_USER_AGENT,
                "extBin": EXT_BIN,
            },
            timeout_s=12.0,
        )
        if not isinstance(res, dict):
            raise RuntimeError("ListModels page evaluate returned non-object")
        if not res.get("ok"):
            status = res.get("status")
            error = res.get("error")
            raise RuntimeError(
                f"ListModels XHR failed (status={status}, error={error})"
            )
        raw_data = res.get("data")
        models = self._parse_raw_models(raw_data)
        if not models:
            raise RuntimeError("ListModels XHR returned no parseable models")
        return models

    async def _fetch_via_http(
        self,
        cookies: dict[str, str],
        auth_user: str,
        *,
        tier: str = "Free",
    ) -> list[dict[str, object]]:
        """通过 HTTP 客户端携带 SAPISIDHASH 拉取。"""
        import httpx

        auth_header = calculate_sapisid_hash(cookies)
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers = {
            "Content-Type": "application/json+protobuf",
            "X-User-Agent": GRPC_WEB_USER_AGENT,
            "X-Goog-Api-Key": WEB_API_KEY,
            "X-Goog-AuthUser": auth_user or "0",
            "X-Goog-Ext-519733851-bin": EXT_BIN,
            "X-AIStudio-Visit-Id": f"v1_{uuid.uuid4()}",
            "Origin": AI_STUDIO_ORIGIN,
            "Referer": AI_STUDIO_HOME_URL,
            "Cookie": cookie_header,
        }
        if auth_header:
            headers["Authorization"] = auth_header
        tier_header = tier_header_value(tier)
        if tier_header:
            headers["X-AIStudio-G1-Tier"] = tier_header

        from aistudio_api.config import settings

        url = LIST_MODELS_URL
        async with httpx.AsyncClient(timeout=10.0, proxy=settings.proxy_url) as client:
            resp = await client.post(url, headers=headers, content="[]")
            if resp.status_code != 200:
                raise RuntimeError(f"ListModels HTTP status {resp.status_code}")
            models = self._parse_raw_models(resp.json())
            if not models:
                raise RuntimeError("ListModels HTTP returned no parseable models")
            return models

    def _parse_raw_models(self, raw_data: object) -> list[dict[str, object]]:
        """解析 Google ListModels 的原始 protobuf 数组结构。"""
        if (
            not isinstance(raw_data, list)
            or not raw_data
            or not isinstance(raw_data[0], list)
        ):
            return []

        models: list[dict[str, object]] = []
        for item in raw_data[0]:
            if not isinstance(item, list) or not item or not isinstance(item[0], str):
                continue
            raw_id = item[0]  # e.g. "models/gemini-3.7-flash"
            model_id = raw_id.replace("models/", "")
            version = (
                str(item[2]).strip()
                if len(item) > 2 and isinstance(item[2], str) and item[2].strip()
                else "001"
            )
            display_name = (
                item[3]
                if len(item) > 3 and isinstance(item[3], str) and item[3].strip()
                else model_id
            )
            description = item[4] if len(item) > 4 and isinstance(item[4], str) else ""
            input_token_limit = (
                item[5] if len(item) > 5 and isinstance(item[5], (int, float)) else 0
            )
            output_token_limit = (
                item[6] if len(item) > 6 and isinstance(item[6], (int, float)) else 0
            )
            methods = item[7] if len(item) > 7 and isinstance(item[7], list) else []
            supported_methods = [str(m) for m in methods if isinstance(m, str)]
            temperature = (
                float(item[8])
                if len(item) > 8
                and isinstance(item[8], (int, float))
                and not isinstance(item[8], bool)
                else 1.0
            )
            top_p = (
                float(item[9])
                if len(item) > 9
                and isinstance(item[9], (int, float))
                and not isinstance(item[9], bool)
                else 0.95
            )
            top_k = (
                int(item[10])
                if len(item) > 10
                and isinstance(item[10], (int, float))
                and not isinstance(item[10], bool)
                else 64
            )
            aliases = _extract_model_aliases(item[56]) if len(item) > 56 else []
            capability_codes = _extract_int_list(item[64]) if len(item) > 64 else []
            capabilities = [
                MODEL_CAPABILITY_NAMES[c]
                for c in capability_codes
                if c in MODEL_CAPABILITY_NAMES
            ]
            default_thinking_level: int | None = None
            thinking_levels: list[int] = []
            if len(item) > 71 and isinstance(item[71], list):
                t_cfg = item[71]
                if (
                    len(t_cfg) > 5
                    and isinstance(t_cfg[5], int)
                    and not isinstance(t_cfg[5], bool)
                ):
                    default_thinking_level = t_cfg[5]
                if len(t_cfg) > 6:
                    thinking_levels = _extract_int_list(t_cfg[6])
            # ListModels item[77] maps to protobuf field 78: 2 = Paid (Pro-only).
            access_tier = (
                item[77] if len(item) > 77 and isinstance(item[77], int) else None
            )
            access_modes = _extract_int_list(item[82]) if len(item) > 82 else []

            # 过滤支持内容生成的模型
            if not any("generateContent" in str(m) for m in methods) and not any(
                "image" in model_id or "veo" in model_id or "lyria" in model_id
                for _ in [0]
            ):
                continue

            is_image = (
                21 in capability_codes or "image" in model_id or "imagen" in model_id
            )
            is_video = 20 in capability_codes or "veo" in model_id
            is_audio = (
                37 in capability_codes
                or 53 in capability_codes
                or "lyria" in model_id
                or "tts" in model_id
            )

            category = "flagship"
            if is_image:
                category = "image"
            elif is_video:
                category = "video"
            elif is_audio:
                category = "audio"
            elif "lite" in model_id or "8b" in model_id or "flash" in model_id:
                category = "flash"
            elif "gemma" in model_id:
                category = "gemma"

            models.append(
                {
                    "id": model_id,
                    "name": raw_id,
                    "version": version,
                    "displayName": display_name,
                    "description": description,
                    "category": category,
                    "inputTokenLimit": int(input_token_limit),
                    "outputTokenLimit": int(output_token_limit),
                    "supportedGenerationMethods": supported_methods
                    or [
                        "generateContent",
                        "countTokens",
                        "createCachedContent",
                    ],
                    "temperature": temperature,
                    "topP": top_p,
                    "topK": top_k,
                    "aliases": aliases,
                    "capabilities": capabilities,
                    "capabilityCodes": capability_codes,
                    "defaultThinkingLevel": default_thinking_level,
                    "thinkingLevels": thinking_levels,
                    "paid": access_tier == 2 or 3 in access_modes or 4 in access_modes,
                    "access_tier": access_tier,
                    "accessModes": access_modes,
                    "is_image_model": is_image,
                    "is_video_model": is_video,
                    "is_audio_model": is_audio,
                }
            )

        return models


# 单例实例
model_discovery = ModelDiscoveryService()
