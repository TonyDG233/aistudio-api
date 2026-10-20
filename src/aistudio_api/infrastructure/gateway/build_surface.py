"""Build-surface protocol layer (WP4b): page readiness, task dispatch, Holder decode.

The AI Studio *Build* surface drives generation through the page-hosted signer
(`window.__buildSigner`, installed by `infrastructure/browser/js/build_signer.js`).
This module owns the Python half of that contract:

- readiness probing (`ensure_ready`): create the host page, inject cookies and the
  signer script, navigate to ``/apps/<app_id>`` and poll ``window.__buildSigner``
  until token material is captured;
- task dispatch + frame回流 (`stream_generate`): route the CDP binding
  ``__aistudio_stream_push__`` by ``rid`` exactly like ``transport.py``, execute the
  page-side task, and turn Holder frames back into the same event tuples the chat
  surface emits (``body`` / ``thinking`` / ``tool_calls`` / ``images`` / ``usage``
  / ``finish_reason`` / ``response_id`` / ``thought_signature`` …).

Holder decoding is evidence-driven (see `docs/dev/PRE-P1-EXPERIMENTS-20261018.md` E1 and
`docs/ark-reference/build-channel/evidence-20261017/`): ``ProxyStreamedCall`` returns
``[[["{\\"candidates\\":…}"]]]`` — 2–3 nested arrays whose leaf strings are escaped
Gemini REST chunks. We unwrap to the leaf, treat a REST dict via ``_rest_to_wire``
(the validated E1 adapter) and, for robustness, a JSPB position array via a port of
Ark's ``convChunk``, then feed the existing ``wire_parser``.

Failure semantics: **zero silent fallback** — every failure path raises
`BuildSurfaceError` carrying the stage (``ensure_ready`` / ``stream``), a specific
reason code, and a concrete remediation hint.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections.abc import AsyncGenerator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aistudio_api.infrastructure.browser import (
    cdp_client as _cdp_client,
    scripts as _browser_scripts,
)
from aistudio_api.infrastructure.gateway.session_auth import load_cookies_to_page
from aistudio_api.infrastructure.gateway.wire_parser import (
    WIRE_CANDIDATE_FINISH_MESSAGE,
    WIRE_CANDIDATE_FINISH_REASON,
    WIRE_CANDIDATE_SAFETY_RATINGS,
    WIRE_CHUNK_CANDIDATES_INDEX,
    WIRE_CHUNK_USAGE_INDEX,
    WIRE_PART_FUNCTION_CALL_INDEX,
    WIRE_PART_FUNCTION_RESPONSE_INDEX,
    WIRE_PART_INLINE_DATA_INDEX,
    WIRE_PART_TEXT_INDEX,
    WIRE_PART_THOUGHT_FLAG_INDEX,
    WIRE_PART_THOUGHT_SIGNATURE_INDEX,
    WIRE_RESPONSE_ID_INDEX,
    parse_chunk_usage,
    parse_response_chunk,
)
from aistudio_api.infrastructure.utils.logger import get_logger

if TYPE_CHECKING:
    from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage

logger = get_logger("build_surface")

# Ark同款默认 app id（docs/ark-reference/build-channel/evidence-20261017/）。
DEFAULT_BUILD_APP_ID = "3b85b4ed-f945-422c-ba25-395d1feef528"
# spikeP 实证配方：必须带 showAssistant/showPreview 查询串，宿主助手才会 boot 并发出
# __warmup__ 引导 RPC（被页面签名器吞没捕获），否则 tb/收割头永远不会出现。
BUILD_SURFACE_URL_TEMPLATE = (
    "https://aistudio.google.com/apps/{app_id}?showAssistant=true&showPreview=true"
)
BUILD_SURFACE_READY_TIMEOUT_S = 120.0

# 宿主引导期间的过场弹窗点击器 + 可信输入过门（均逐字对齐 spikeP.py 实证做法）。
INTERSTITIAL_CLICK_JS = """() => {
    const words = ["Continue to the app", "Skip", "Launch"];
    const norm = (e) => (e.innerText || e.textContent || "").trim();
    const els = Array.from(document.querySelectorAll(
        'button, a, div[role="button"]'));
    for (const w of words) {
        const exact = els.find((e) => norm(e) === w);
        if (exact) { exact.click(); return w; }
    }
    const clickable = Array.from(document.querySelectorAll(
        'button, [role="button"]'));
    for (const w of words) {
        const wl = w.toLowerCase();
        const hit = clickable.find((e) => {
            const t = norm(e).toLowerCase();
            return t && t.length <= 40 && t.includes(wl);
        });
        if (hit) { hit.click(); return w + "~"; }
    }
    return null;
}"""

ACTIVE_RECT_JS = """() => {
    const el = document.activeElement;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {x: r.x + r.width / 2, y: r.y + r.height / 2};
}"""

SIGNER_STATE_EXPR = "() => (window.__buildSigner && window.__buildSigner.state) || null"

# spikeP 实证复刻：应答预览/宿主链路的 requestAuthIndex 握手（否则 boot 静默等待）。
AUTH_RESPONDER_JS_TEMPLATE = """(() => {
  if (window !== window.top) return;
  if (window.__buildAuthResponder) return;
  window.__buildAuthResponder = true;
  window.addEventListener("message", (ev) => {
    try {
      const d = ev.data;
      if (d && d.type === "requestAuthIndex" && ev.source) {
        ev.source.postMessage(
          { type: "authIndexResponse", authIndex: __AUTH_INDEX__ }, "*"
        );
      }
    } catch (e) {}
  });
})();"""

# spikeP 实证复刻：app 内嵌客户端（scripts/client/build.js）会连 ws://127.0.0.1:9998
# 完成启动握手；此接收器只接受连接（消息丢弃，与 spikeP ws_handler 同款），
# 让 app 的启动链完整跑完（宿主 warmup 触发的前置条件）。
_app_ws_sink_state: object | None = None
_APP_WS_CONNS: list[object] = []
_APP_WS_CONNS_BY_REALM: dict[str, list[object]] = {}


def _ws_realm_id(websocket: object) -> str:
    """Extract ``?realm=`` from the 9998 handshake path (default realm = "")."""
    try:
        request = getattr(websocket, "request", None)
        path = str(getattr(request, "path", "") or "")
    except Exception:
        return ""
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(path).query)
    values = query.get("realm") or []
    return str(values[0]) if values else ""


def _realm_connections(realm_id: str | None) -> list[object]:
    key = realm_id or ""
    if key:
        return _APP_WS_CONNS_BY_REALM.setdefault(key, [])
    return _APP_WS_CONNS


async def _app_ws_sink_handler(websocket: object, *_args: object) -> None:
    realm_id = _ws_realm_id(websocket)
    connections = _realm_connections(realm_id)
    connections.append(websocket)
    if realm_id:
        _APP_WS_CONNS.append(websocket)
    logger.info(
        "app 内嵌客户端已连接 9998（realm=%s, 第 %d 个）",
        realm_id or "default",
        len(connections),
    )
    try:
        async for _ in websocket:  # type: ignore[attr-defined]
            pass
    except Exception:
        pass
    finally:
        with suppress(Exception):
            connections.remove(websocket)
        if realm_id:
            with suppress(Exception):
                _APP_WS_CONNS.remove(websocket)
        logger.info(
            "app 内嵌客户端断开 9998（realm=%s, 余 %d 个）",
            realm_id or "default",
            len(connections),
        )


async def _ensure_app_ws_sink() -> None:
    global _app_ws_sink_state
    if _app_ws_sink_state is not None:
        return
    try:
        import websockets

        _app_ws_sink_state = await websockets.serve(
            _app_ws_sink_handler, "127.0.0.1", 9998
        )
        logger.info("Build 面 9998 WS 接收器已启动（app 内嵌客户端握手）")
    except OSError:
        _app_ws_sink_state = True  # 端口已被占：视为已有服务端，不再重试
        logger.info("端口 9998 已被占用，跳过 WS 接收器启动")
    except Exception:
        _app_ws_sink_state = True
        logger.warning("WS 接收器启动失败（不影响主流程，但 app 握手可能受限）")


# 与聊天面传输层一致的 binding 名（复用既有 `__aistudio_stream_push__` 通道）。
STREAM_PUSH_BINDING = "__aistudio_stream_push__"

_APP_ID_RE = re.compile(r"/apps/([0-9a-fA-F][0-9a-fA-F-]{7,})")

# Mirrors application/api_service_gemini.WIRE_TO_GEMINI_FINISH_REASON (kept local to
# avoid an infrastructure→application import). ``stop`` maps REST⇄wire exactly.
_WIRE_TO_FINISH_NAME: dict[int, str] = {
    0: "FINISH_REASON_UNSPECIFIED",
    1: "STOP",
    2: "MAX_TOKENS",
    3: "SAFETY",
    4: "RECITATION",
    5: "LANGUAGE",
    6: "OTHER",
    7: "BLOCKLIST",
    8: "PROHIBITED_CONTENT",
    9: "SPII",
    10: "MALFORMED_FUNCTION_CALL",
    11: "IMAGE_SAFETY",
    12: "UNEXPECTED_TOOL_CALL",
    13: "TOO_MANY_TOOL_CALLS",
    14: "IMAGE_PROHIBITED_CONTENT",
    15: "NO_IMAGE",
    16: "IMAGE_RECITATION",
    17: "IMAGE_OTHER",
}
_FINISH_NAME_TO_WIRE: dict[str, int] = {
    name: code for code, name in _WIRE_TO_FINISH_NAME.items()
}

# REST usage → wire index (chunk[2]) mapping, aligned with wire_parser §7.3.
_USAGE_FIELD_INDEX: dict[str, int] = {
    "promptTokenCount": 0,
    "candidatesTokenCount": 1,
    "totalTokenCount": 2,
    "cachedContentTokenCount": 3,
    "promptTokensDetails": 4,
    "thoughtsTokenCount": 9,
}

# Page-side readiness snapshot (public signer API only — no internal field names).
BUILD_SIGNER_STATE_JS = """() => {
    const S = window.__buildSigner;
    if (!S) return null;
    let harvest = null;
    try { harvest = (typeof S.harvest === "function") ? S.harvest() : null; } catch (e) { harvest = null; }
    let lastError = "";
    try { lastError = (typeof S.lastError === "function") ? String(S.lastError() || "") : ""; } catch (e) {}
    return { state: String(S.state || ""), harvest: harvest, lastError: lastError };
}"""

# E8 three-stage diagnosis (mirrors spikeP.py DETECT_JS, reprinted with guidance).
BUILD_SURFACE_DETECT_JS = """() => {
    const M = window.default_MakerSuite;
    const S = window.__buildSigner;
    if (!M) return { code: "MAKERSUITE_MISSING",
        message: "window.default_MakerSuite 不存在：页面未加载完成或上游结构变更" };
    const tc = typeof M.Cn, tg = typeof M.GB;
    if (tc !== "function" || tg !== "function")
        return { code: "SIGNER_FUNCS_DRIFT",
            message: "签名函数漂移: M.Cn=" + tc + " M.GB=" + tg };
    let harvest = null;
    try { harvest = (S && typeof S.harvest === "function") ? S.harvest() : null; } catch (e) { harvest = null; }
    const h = (harvest && typeof harvest === "object") ? harvest : null;
    const tbPresent = !!(h && (h.tbPresent || h.tb));
    const headers = (h && h.headers && typeof h.headers === "object") ? h.headers : null;
    const hasKey = !!(headers && headers["x-goog-api-key"]);
    if (!tbPresent || !hasKey)
        return { code: "TB_MISSING",
            message: "tb/收割头缺失：tbPresent=" + tbPresent + " x-goog-api-key=" + hasKey };
    return { code: "UNKNOWN",
        message: "签名器未在超时内就绪，且三段式探测未命中断言" };
}"""

_DETECT_GUIDANCE: dict[str, str] = {
    "MAKERSUITE_MISSING": "等待页面就绪后重试；若持续缺失按上游结构变更处理（更新 build_signer.js hook 目标）",
    "SIGNER_FUNCS_DRIFT": "上游签名函数名漂移；更新 hook 目标名单（M.Cn/M.GB 的新名）",
    "TB_MISSING": "重跑 warmup / 检查 XHR hook 注入与真人活动门；确认 tb 与收割头 x-goog-api-key 均已捕获",
    "SIGNER_SCRIPT_MISSING": "build_signer.js 尚未就绪（S1a 未合入或脚本为空）；确认 scripts.BUILD_SIGNER_JS 存在",
    "UNKNOWN": "抓取页面诊断快照（state/lastError）后上报，按 R4 Holder/上游变更处理",
}

# Dispatch expression: fire-and-forget so frames can arrive progressively via binding.
BUILD_SIGNER_EXECUTE_JS = (
    "(args) => window.__buildSigner.execute(args.taskId, args.model, args.jsonBody)"
)


class BuildSurfaceError(RuntimeError):
    """Explicit build-surface failure with stage, reason code and remediation."""

    def __init__(self, stage: str, code: str, detail: str, guidance: str = "") -> None:
        self.stage = stage
        self.code = code
        self.detail = detail
        self.guidance = guidance
        message = f"[build_surface:{stage}] {code}: {detail}"
        if guidance:
            message = f"{message} | 处置建议: {guidance}"
        super().__init__(message)


@dataclass
class BuildSurfaceState:
    """Mutable lifecycle state for one build-surface page/account binding."""

    page: CDPPage | None = None
    auth_file: str | None = None
    account_id: str | None = None
    auth_user: str = "0"
    app_id: str = ""
    ready: bool = False
    ready_state: str = ""
    harvest: dict[str, object] | None = None
    last_error: str = ""
    # rid → asyncio.Queue, the generator task table (one entry per in-flight task).
    task_queues: dict[str, asyncio.Queue[dict[str, object]]] = field(
        default_factory=dict
    )

    @property
    def in_flight_count(self) -> int:
        return len(self.task_queues)


def resolve_build_app_id() -> str:
    """Resolve the Build app id: config ``channels.build.app_url`` → env → Ark default."""
    candidates: list[str] = []
    with suppress(Exception):
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_features_config,
        )

        channels = get_features_config().get("channels")
        if isinstance(channels, dict):
            build = channels.get("build")
            if isinstance(build, dict):
                candidates.append(str(build.get("app_url") or ""))
    candidates.append(os.getenv("AISTUDIO_APP_URL") or "")
    with suppress(Exception):
        from aistudio_api.config import settings

        candidates.append(str(getattr(settings, "aistudio_app_url", "") or ""))
    for raw in candidates:
        match = _APP_ID_RE.search(raw)
        if match:
            return match.group(1)
        text = raw.strip()
        if text and re.fullmatch(r"[0-9a-fA-F][0-9a-fA-F-]{7,}", text):
            return text
    return DEFAULT_BUILD_APP_ID


def _load_build_blocked_patterns() -> list[str]:
    """Read the curated build-surface blocklist from cdp_client (S1a-owned)."""
    # TODO(S1a): once cdp_client.BUILD_SURFACE_BLOCKED_URL_PATTERNS lands this returns
    # the whitelist-aware subset; until then an empty list keeps the host bundle intact
    # (R6: never block the Build main package with the generic BLOCKED_URL_PATTERNS).
    raw = getattr(_cdp_client, "BUILD_SURFACE_BLOCKED_URL_PATTERNS", None)
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []


def _load_build_signer_js() -> str | None:
    """Read the signer script from scripts.py (S1a-owned, may be absent yet)."""
    # TODO(S1a): scripts.BUILD_SIGNER_JS is being added in parallel; until it exists
    # ensure_ready fails explicitly with SIGNER_SCRIPT_MISSING (never a silent no-op).
    raw = getattr(_browser_scripts, "BUILD_SIGNER_JS", None)
    if isinstance(raw, str) and raw.strip():
        return raw
    return None


def _sample(value: object, limit: int = 90) -> str:
    text = " ".join(str(value).split())
    return text[:limit]


# ---------------------------------------------------------------------------
# Holder decoding: 2–3 level nested arrays → leaves → REST chunk dicts.
# ---------------------------------------------------------------------------


def _double_parse(text: str) -> object | None:
    """Parse a holder leaf: ``'{"candidates":…}'`` → dict; escaped literal → dict."""
    try:
        value: object = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
    return value


def _collect_leaves(node: object, chunks: list[object], dropped: list[str]) -> None:
    if isinstance(node, str):
        parsed = _double_parse(node)
        if parsed is None:
            dropped.append(_sample(node))
        elif isinstance(parsed, (dict, list)):
            chunks.append(parsed)
        else:
            dropped.append(_sample(node))
    elif isinstance(node, dict):
        chunks.append(node)
    elif isinstance(node, list):
        for child in node:
            _collect_leaves(child, chunks, dropped)
    else:
        dropped.append(_sample(node))


def decode_holder_frame(raw_frame: str) -> tuple[list[object], list[str]]:
    """Unwrap a Holder frame into leaf objects.

    Accepts a direct leaf string, an escaped string literal, or the full nested
    array form (``[[[ "…" ]]]``). Returns ``(leaves, dropped_samples)``; undecodable
    leaves are reported instead of being silently discarded.
    """
    text = raw_frame.strip()
    if not text:
        return [], []
    try:
        root: object = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # A bare leaf that is not valid JSON on its own: keep it for diagnosis.
        return [], [_sample(raw_frame)]
    chunks: list[object] = []
    dropped: list[str] = []
    _collect_leaves(root, chunks, dropped)
    return chunks, dropped


# ---------------------------------------------------------------------------
# leaf → wire array (E1 adapter + Ark convChunk compatibility branch).
# ---------------------------------------------------------------------------


def _is_rpc_error_record(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], int)
        and not isinstance(value[0], bool)
        and value[0] != 0
        and isinstance(value[1], str)
    )


def _rest_part_to_wire(part: dict[str, object]) -> list[object]:
    wire: list[object] = [None] * (WIRE_PART_THOUGHT_SIGNATURE_INDEX + 1)
    text = part.get("text")
    if isinstance(text, str):
        wire[WIRE_PART_TEXT_INDEX] = text
    if part.get("thought") is True:
        wire[WIRE_PART_THOUGHT_FLAG_INDEX] = True
    inline = part.get("inlineData") or part.get("inline_data")
    if isinstance(inline, dict) and isinstance(inline.get("data"), str):
        mime = inline.get("mimeType") or inline.get("mime_type") or "image/jpeg"
        wire[WIRE_PART_INLINE_DATA_INDEX] = [str(mime), inline.get("data")]
    function_call = part.get("functionCall") or part.get("function_call")
    if isinstance(function_call, dict):
        wire[WIRE_PART_FUNCTION_CALL_INDEX] = [
            function_call.get("name"),
            function_call.get("args"),
            function_call.get("id") or function_call.get("callId"),
        ]
    function_response = part.get("functionResponse") or part.get("function_response")
    if isinstance(function_response, dict):
        wire[WIRE_PART_FUNCTION_RESPONSE_INDEX] = [
            function_response.get("name"),
            function_response.get("response"),
            function_response.get("id") or function_response.get("callId"),
        ]
    signature = part.get("thoughtSignature") or part.get("thought_signature")
    if isinstance(signature, str) and signature:
        wire[WIRE_PART_THOUGHT_SIGNATURE_INDEX] = signature
    return wire


def _rest_candidate_to_wire(candidate: dict[str, object]) -> list[object]:
    raw_content = candidate.get("content")
    content: dict[str, object] = raw_content if isinstance(raw_content, dict) else {}
    raw_parts = content.get("parts")
    wire_parts: list[object] = [
        _rest_part_to_wire(part)
        for part in (raw_parts if isinstance(raw_parts, list) else [])
        if isinstance(part, dict)
    ]
    role = str(content.get("role") or "model")
    wire: list[object] = [None] * (WIRE_CANDIDATE_SAFETY_RATINGS + 1)
    wire[0] = [wire_parts, role]
    finish = candidate.get("finishReason")
    wire[WIRE_CANDIDATE_FINISH_REASON] = (
        _FINISH_NAME_TO_WIRE.get(finish.upper(), 0) if isinstance(finish, str) else None
    )
    message = candidate.get("finishMessage")
    wire[WIRE_CANDIDATE_FINISH_MESSAGE] = (
        str(message) if isinstance(message, str) else None
    )
    ratings = candidate.get("safetyRatings")
    wire[WIRE_CANDIDATE_SAFETY_RATINGS] = ratings if isinstance(ratings, list) else None
    return wire


def _rest_usage_to_wire(usage: dict[str, object]) -> list[object]:
    wire: list[object] = [None] * (max(_USAGE_FIELD_INDEX.values()) + 1)
    for key, index in _USAGE_FIELD_INDEX.items():
        if key in usage:
            wire[index] = usage[key]
    return wire


def _rest_to_wire(chunk: dict[str, object]) -> list[object]:
    """REST chunk dict → wire array consumed by ``wire_parser`` (E1 adapter)."""
    wire: list[object] = [None] * 8
    raw_candidates = chunk.get("candidates")
    if isinstance(raw_candidates, list):
        wire_candidates = [
            _rest_candidate_to_wire(candidate)
            for candidate in raw_candidates
            if isinstance(candidate, dict)
        ]
        if wire_candidates:
            wire[WIRE_CHUNK_CANDIDATES_INDEX] = wire_candidates
    usage = chunk.get("usageMetadata")
    if isinstance(usage, dict):
        wire[WIRE_CHUNK_USAGE_INDEX] = _rest_usage_to_wire(usage)
    response_id = chunk.get("responseId")
    if isinstance(response_id, str) and response_id:
        wire[WIRE_RESPONSE_ID_INDEX] = response_id
    return wire


def _decode_wire_value(value: object) -> object:
    if not isinstance(value, list):
        return value
    if not value:
        return []
    if len(value) > 1 and value[1] is not None:
        return value[1]
    if len(value) > 2 and value[2] is not None:
        return value[2]
    if len(value) > 3 and value[3] is not None:
        return bool(value[3])
    if len(value) > 4 and value[4] is not None:
        return _decode_wire_struct(value[4])
    if len(value) > 5 and value[5] is not None:
        nested = value[5]
        items = nested[0] if isinstance(nested, list) and nested else []
        return (
            [_decode_wire_value(item) for item in items]
            if isinstance(items, list)
            else []
        )
    return None


def _decode_wire_struct(raw_struct: object) -> dict[str, object]:
    if isinstance(raw_struct, dict):
        return raw_struct
    if not isinstance(raw_struct, list) or not raw_struct:
        return {}
    entries = raw_struct[0]
    if not isinstance(entries, list):
        return {}
    result: dict[str, object] = {}
    for entry in entries:
        if isinstance(entry, list) and len(entry) >= 2 and isinstance(entry[0], str):
            result[entry[0]] = _decode_wire_value(entry[1])
    return result


def _jspb_candidate_to_rest(candidate: object, index: int) -> dict[str, object] | None:
    if not (
        isinstance(candidate, list)
        and candidate
        and isinstance(candidate[0], list)
        and candidate[0]
        and isinstance(candidate[0][0], list)
    ):
        return None
    parts: list[dict[str, object]] = []
    for raw_part in candidate[0][0]:
        if not isinstance(raw_part, list):
            continue
        entry: dict[str, object] = {}
        if len(raw_part) > 1 and isinstance(raw_part[1], str):
            entry["text"] = raw_part[1]
        if (
            len(raw_part) > 2
            and isinstance(raw_part[2], list)
            and len(raw_part[2]) >= 2
        ):
            entry["inlineData"] = {
                "mimeType": raw_part[2][0],
                "data": raw_part[2][1],
            }
        if (
            len(raw_part) > 10
            and isinstance(raw_part[10], list)
            and len(raw_part[10]) >= 2
        ):
            function_call: dict[str, object] = {
                "name": raw_part[10][0],
                "args": _decode_wire_struct(raw_part[10][1]),
            }
            if len(raw_part[10]) > 2 and raw_part[10][2]:
                function_call["id"] = raw_part[10][2]
            entry["functionCall"] = function_call
        if len(raw_part) > 12 and (raw_part[12] is True or raw_part[12] == 1):
            entry["thought"] = True
        if len(raw_part) > 14 and raw_part[14]:
            entry["thoughtSignature"] = raw_part[14]
        if entry:
            parts.append(entry)
    result: dict[str, object] = {
        "content": {"parts": parts, "role": "model"},
        "index": index,
    }
    if (
        len(candidate) > 1
        and isinstance(candidate[1], int)
        and not isinstance(candidate[1], bool)
    ):
        result["finishReason"] = _WIRE_TO_FINISH_NAME.get(candidate[1], "OTHER")
    return result


def _jspb_to_rest(element: list[object]) -> dict[str, object]:
    """Port of Ark ``MakerSuiteCodec.convChunk`` (JSPB element → REST chunk dict)."""
    out: dict[str, object] = {}
    if element and isinstance(element[0], list):
        candidates: list[dict[str, object]] = []
        for index, raw_candidate in enumerate(element[0]):
            converted = _jspb_candidate_to_rest(raw_candidate, index)
            if converted is not None:
                candidates.append(converted)
        if candidates:
            out["candidates"] = candidates
    if len(element) > 2 and isinstance(element[2], list):
        raw_usage = element[2]
        usage: dict[str, object] = {}
        for key, index in _USAGE_FIELD_INDEX.items():
            if index == _USAGE_FIELD_INDEX["promptTokensDetails"]:
                continue
            if len(raw_usage) > index:
                value = raw_usage[index]
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage[key] = value
        if usage:
            out["usageMetadata"] = usage
    if len(element) > 7 and isinstance(element[7], str):
        out["responseId"] = element[7]
    return out


def conv_chunk(leaf: object) -> list[object] | None:
    """Holder leaf → wire array; raises on an explicit upstream error envelope."""
    if isinstance(leaf, dict):
        error = leaf.get("error")
        if isinstance(error, (dict, str)):
            message = (
                str(error.get("message")) if isinstance(error, dict) else str(error)
            )
            raise BuildSurfaceError(
                "stream",
                "UPSTREAM_ERROR",
                message or _sample(leaf, 300),
                "上游返回错误体；检查请求体形态与账号状态",
            )
        if (
            leaf.get("candidates") is not None
            or leaf.get("usageMetadata") is not None
            or leaf.get("responseId") is not None
        ):
            return _rest_to_wire(leaf)
        return None
    if isinstance(leaf, list):
        if _is_rpc_error_record(leaf):
            raise BuildSurfaceError(
                "stream",
                "UPSTREAM_RPC_ERROR",
                f"rpc_code={leaf[0]} message={leaf[1]}",
                "上游 RPC 错误记录（HTTP 200 内嵌）；（3)=INVALID_ARGUMENT (8)=RESOURCE_EXHAUSTED",
            )
        rest = _jspb_to_rest(leaf)
        if rest:
            return _rest_to_wire(rest)
        return None
    return None


class BuildSurface:
    """Python protocol layer for the AI Studio Build surface."""

    def __init__(
        self,
        cdp_client: CDPClient,
        *,
        resolve_app_id: Callable[[], object] | None = None,
        context_id: str | None = None,
        realm_id: str | None = None,
    ) -> None:
        self._cdp = cdp_client
        self._resolve_app_id: Callable[[], object] | None = resolve_app_id
        self._context_id = context_id
        self._realm_id = realm_id
        self.state = BuildSurfaceState()

    def _connections(self) -> list[object]:
        return _realm_connections(self._realm_id)

    async def _install_realm_ws_tag(self, page: CDPPage) -> None:
        """Tag the app's 9998 WebSocket with ``?realm=`` so the sink can demux it."""
        if not self._realm_id or getattr(page, "has_realm_ws_tag", False):
            return
        setattr(page, "has_realm_ws_tag", True)  # noqa: B010 - duck-typed flag
        realm_json = json.dumps(self._realm_id)
        source = f"""(() => {{
  const RealWS = window.WebSocket;
  if (!RealWS || RealWS.__realmTagged) return;
  function Wrapped(url, protocols) {{
    try {{
      if (typeof url === "string" && url.indexOf(":9998") !== -1 && url.indexOf("realm=") === -1) {{
        url += (url.indexOf("?") === -1 ? "?" : "&") + "realm=" + encodeURIComponent({realm_json});
      }}
    }} catch (e) {{}}
    return protocols === undefined ? new RealWS(url) : new RealWS(url, protocols);
  }}
  Wrapped.prototype = RealWS.prototype;
  Object.assign(Wrapped, RealWS);
  Wrapped.__realmTagged = true;
  window.WebSocket = Wrapped;
}})();"""
        try:
            await page.cdp.send(
                "Page.addScriptToEvaluateOnNewDocument", {"source": source}
            )
        except Exception as exc:
            logger.warning("[build] realm WS 标记注入失败: %s", exc)

    @property
    def app_id(self) -> str:
        if self._resolve_app_id is not None:
            return str(self._resolve_app_id())
        return resolve_build_app_id()

    def _ensure_page_binding(self, page: CDPPage) -> None:
        """Register the rid-demux binding listener once per page (transport.py pattern)."""
        if getattr(page, "has_build_surface_binding", False):
            return
        setattr(page, "has_build_surface_binding", True)  # noqa: B010 - duck-typed flag

        def on_stream_push(payload_str: str) -> None:
            try:
                data = json.loads(payload_str)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.debug("build surface 推送数据解析失败: %s", exc)
                return
            if not isinstance(data, dict):
                return
            rid = str(data.get("rid") or "")
            queue = self.state.task_queues.get(rid)
            if queue is None:
                return
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                logger.warning("build surface 反压: 队列已满 (rid=%s)", rid)

        if hasattr(page, "on_binding"):
            page.on_binding(STREAM_PUSH_BINDING, on_stream_push)
        elif hasattr(page, "cdp") and hasattr(page.cdp, "on"):

            def listener(params: dict[str, object]) -> None:
                if params.get("name") == STREAM_PUSH_BINDING:
                    on_stream_push(str(params.get("payload") or ""))

            page.cdp.on("Runtime.bindingCalled", listener)

    async def ensure_ready(
        self,
        auth_file: str | None,
        auth_user: str = "0",
        *,
        account_id: str | None = None,
        ready_timeout_s: float = BUILD_SURFACE_READY_TIMEOUT_S,
    ) -> BuildSurfaceState:
        """Create/attach the host page and wait for the signer to capture token material."""
        state = self.state
        same_auth = state.auth_file == auth_file
        state.app_id = self.app_id

        # 复用快路径（2026-10-18 通信优化）：页面存活 + 已就绪 + 账号未变时免去
        # 重导航/重过门/重暖机；WS 断开则先试原地恢复（Ark 轻量重连思路），
        # 恢复失败再走完整重建。旧实现每个请求全额重走 ensure_ready。
        connections = self._connections()
        if (
            same_auth
            and state.page is not None
            and not state.page.is_closed()
            and state.ready
            and not state.last_error
            and (connections or await self._recover_warmup(state.page))
        ):
            state.auth_user = auth_user
            state.account_id = account_id
            logger.info(
                "[build] ensure_ready 复用已就绪页面（realm=%s, WS 连接=%d）",
                self._realm_id or "default",
                len(connections),
            )
            return state

        state.auth_file = auth_file
        state.auth_user = auth_user
        state.account_id = account_id
        state.ready = False

        page = state.page
        created = False
        if page is None or page.is_closed():
            await _ensure_app_ws_sink()
            if self._context_id:
                page = await self._cdp.create_page_in_context(
                    self._context_id, block_assets=True
                )
            else:
                page = await self._cdp.create_new_page(block_assets=True)
            await self._install_realm_ws_tag(page)
            state.page = page
            created = True

        try:
            patterns = _load_build_blocked_patterns()
            if patterns:
                await page.set_blocked_urls(patterns)

            if not auth_file:
                raise BuildSurfaceError(
                    "ensure_ready",
                    "COOKIES_MISSING",
                    "auth_file 为空，无法向 Build 宿主页注入账号 Cookie",
                    "传入有效 auth.json 路径（含 cookies 数组）后重试",
                )
            injected = await load_cookies_to_page(page, auth_file)
            if not injected:
                raise BuildSurfaceError(
                    "ensure_ready",
                    "COOKIES_MISSING",
                    f"从 auth_file 载入 Cookie 失败: {auth_file}",
                    "确认 auth.json 存在且含 cookies；必要时重新导入账号",
                )

            signer_js = _load_build_signer_js()
            if not signer_js:
                raise BuildSurfaceError(
                    "ensure_ready",
                    "SIGNER_SCRIPT_MISSING",
                    "scripts.BUILD_SIGNER_JS 缺失或为空，无法安装页面签名器",
                    _DETECT_GUIDANCE["SIGNER_SCRIPT_MISSING"],
                )
            auth_index = auth_user if str(auth_user).isdigit() else "0"
            responder_js = AUTH_RESPONDER_JS_TEMPLATE.replace(
                "__AUTH_INDEX__", str(auth_index)
            )
            await page.cdp.send(
                "Page.addScriptToEvaluateOnNewDocument", {"source": responder_js}
            )
            await page.cdp.send(
                "Page.addScriptToEvaluateOnNewDocument", {"source": signer_js}
            )

            try:
                # 后台标签页会被浏览器节流并跳过 boot 期网络预热；宿主 warmup 需要前台可见性。
                await page.cdp.send("Page.bringToFront")
            except Exception as exc:
                logger.warning("Page.bringToFront 失败（后台节流风险）: %s", exc)
            with suppress(Exception):
                # headless/无窗口焦点时 document.hasFocus()=false，宿主助手 boot 会被
                # focus 门挡下而不发 warmup（spikeP 实证复刻：Emulation.setFocusEmulationEnabled）。
                await page.cdp.send(
                    "Emulation.setFocusEmulationEnabled", {"enabled": True}
                )
            url = BUILD_SURFACE_URL_TEMPLATE.format(app_id=state.app_id)
            try:
                await page.goto(url, timeout_s=180.0, wait_until="domcontentloaded")
                # Ark 对齐（BrowserManager.js:838）：domcontentloaded 后宿主仍在异步
                # 挂载 preview iframe；先稳定 2s 再过门，显著降低首发唤醒落空的概率。
                await asyncio.sleep(2.0)
            except Exception as exc:
                raise BuildSurfaceError(
                    "ensure_ready",
                    "NAVIGATION_FAILED",
                    f"导航到 {url} 失败: {exc}",
                    "检查网络/代理与 app_id；确认账号可访问该 Build 应用",
                ) from exc

            await self._dismiss_interstitials(page)
            # 唤醒-连接重试环（2026-10-18 超时根因修复）：宿主 SPA 在 domcontentloaded 之后
            # 才异步挂载 preview iframe；若只发一次过门而 iframe 尚未就绪，唤醒事件会丢失，
            # 旧实现随即空等 100s（实测 22:29 案例：建页→100s 超时→第二发过门后才连接）。
            # 现在改为每 ≤15s 重发一次可信输入，连接即退出，最坏耗时也被限制在同一量级。
            app_connected = False
            loop = asyncio.get_running_loop()
            connect_timeout = min(100.0, max(15.0, ready_timeout_s))
            connect_deadline = loop.time() + connect_timeout
            attempt = 0
            while loop.time() < connect_deadline:
                attempt += 1
                await self._dismiss_interstitials(page, max_rounds=2)
                await self._pass_human_gate(page)
                remaining = max(1.0, connect_deadline - loop.time())
                app_connected = await self._wait_app_connect(
                    timeout_s=min(15.0, remaining)
                )
                if app_connected:
                    logger.info("[build] app 内嵌客户端已连接（过门轮次=%d）", attempt)
                    break
                logger.info(
                    "[build] 过门第 %d 轮未连接 9998，继续重试（signer=%s）",
                    attempt,
                    await self._probe_signer_state_safe(page),
                )
            if not app_connected:
                raise BuildSurfaceError(
                    "ensure_ready",
                    "APP_NOT_CONNECTED",
                    f"app 内嵌客户端未在 {connect_timeout:.0f}s 内连接 9998",
                    "检查 Build 应用是否可打开、账号是否可访问，以及代理/网络是否拦截 9998",
                )
            # gate#2：落在 app 连接之后，作为"助手就绪后的首次可信交互"触发宿主 warmup 门。
            await self._pass_human_gate(page)
            # 主动 warmup（装置实证机制，spikeQ3/Q4）：经 9998 WS 向 app 内嵌客户端下发
            # __warmup__ 规格 → 宿主发起一次被吞没的 RPC → tb 捕获 + 收割头就位（零上游流量）。
            await self._trigger_warmup()
            await self._wait_for_ready(page, ready_timeout_s=ready_timeout_s)
        except BaseException:
            if created and state.page is page:
                # A freshly created page that never became ready is not reusable.
                with suppress(Exception):
                    await page.close()
                state.page = None
            raise
        return state

    async def _dismiss_interstitials(
        self, page: CDPPage, *, max_rounds: int = 12
    ) -> None:
        """Click through host interstitial dialogs until the signer script is live."""
        for _ in range(max_rounds):
            with suppress(Exception):
                signer_state = await page.evaluate(SIGNER_STATE_EXPR)
                if isinstance(signer_state, str) and signer_state not in (
                    "",
                    "installing",
                ):
                    return
                clicked = await page.evaluate(INTERSTITIAL_CLICK_JS)
                if clicked is not None:
                    await asyncio.sleep(1.0)
                    continue
            await asyncio.sleep(1.2)

    async def _wait_app_connect(self, timeout_s: float = 100.0) -> bool:
        """Wait until the app's embedded client completes its 9998 handshake."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        connections = self._connections()
        while loop.time() < deadline:
            if connections:
                return True
            await asyncio.sleep(0.5)
        return False

    async def _probe_signer_state_safe(self, page: CDPPage) -> str:
        """Best-effort read of the page signer state for connect-wait diagnostics."""
        with suppress(Exception):
            snapshot = await page.evaluate(BUILD_SIGNER_STATE_JS)
            if isinstance(snapshot, dict):
                return str(snapshot.get("state") or "")
        return "unknown"

    async def _recover_warmup(self, page: CDPPage) -> bool:
        """页面存活但 9998 断开时的原地恢复：重过门→等连接→重发 warmup→等就绪。

        对齐 Ark 的"轻量重连"思路（不重导航）：仅当连接与暖机均恢复时复用页面，
        否则返回 False，交由完整重建路径（重导航）处理。
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 60.0
        connections = self._connections()
        while loop.time() < deadline and not connections:
            await self._dismiss_interstitials(page, max_rounds=2)
            await self._pass_human_gate(page)
            remaining = max(1.0, deadline - loop.time())
            if await self._wait_app_connect(timeout_s=min(15.0, remaining)):
                break
        if not connections:
            logger.warning(
                "[build] 原地恢复失败：realm=%s 的 9998 未在 60s 内重连，转完整重建",
                self._realm_id or "default",
            )
            return False
        await self._trigger_warmup()
        try:
            await self._wait_for_ready(page, ready_timeout_s=60.0)
        except BuildSurfaceError:
            logger.warning("[build] 原地重暖机失败，转完整重建")
            return False
        logger.info("[build] 原地恢复成功：WS 重连 + 签名器重新就绪")
        return True

    async def _pass_human_gate(self, page: CDPPage) -> None:
        """Best-effort trusted-input pass (mouse sweep + real click + Escape).

        Recipe proven in spikeP/E-series: >=45 CDP mouseMoved events plus one real
        mousePressed/Released click set ``userActivation.hasBeenActive`` on the host.
        """
        with suppress(Exception):
            for index in range(45):
                await page.cdp.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseMoved",
                        "x": 100 + (index * 9) % 420,
                        "y": 120 + (index * 7) % 360,
                    },
                )
                await asyncio.sleep(0.012)
            rect = await page.evaluate(ACTIVE_RECT_JS)
            if isinstance(rect, dict) and rect.get("x") and rect.get("y"):
                cx, cy = float(rect["x"]), float(rect["y"])
                await page.cdp.send(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": cx, "y": cy},
                )
                await page.cdp.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mousePressed",
                        "x": cx,
                        "y": cy,
                        "button": "left",
                        "buttons": 1,
                        "clickCount": 1,
                    },
                )
                await asyncio.sleep(0.05)
                await page.cdp.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseReleased",
                        "x": cx,
                        "y": cy,
                        "button": "left",
                        "buttons": 0,
                        "clickCount": 1,
                    },
                )
                await asyncio.sleep(0.5)
            for _ in range(8):
                for event_type in ("keyDown", "keyUp"):
                    await page.cdp.send(
                        "Input.dispatchKeyEvent",
                        {
                            "type": event_type,
                            "key": "Escape",
                            "code": "Escape",
                            "windowsVirtualKeyCode": 27,
                            "nativeVirtualKeyCode": 27,
                        },
                    )
                await asyncio.sleep(0.03)

    async def _trigger_warmup(self) -> bool:
        """Dispatch the warmup spec over the app WS (proven spikeQ3/Q4 mechanism).

        The host's embedded client fires a ``__warmup__`` RPC only when the
        server-side WS tells it to; the signer's XHR hook swallows that RPC
        (zero upstream traffic) while the signing/harvest hooks capture ``tb``
        and the credential headers. A passive sink captures nothing — that is
        exactly why every pre-fix service attempt stalled at ``harvesting``.
        """
        connections = self._connections()
        if not connections:
            return False
        spec = {
            "method": "POST",
            "path": "/v1beta/models/__warmup__:streamGenerateContent",
            "query_params": {"alt": "sse"},
            "request_attempt_id": "build-surface-warmup",
            "request_id": "build-surface-warmup",
            "streaming_mode": "real",
        }
        try:
            await connections[-1].send(json.dumps(spec))  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("warmup 规格下发失败: %s", exc)
            return False
        logger.info(
            "已向 app 内嵌客户端下发 warmup 规格（触发宿主 __warmup__ RPC，零上游流量）"
        )
        return True

    async def _wait_for_ready(self, page: CDPPage, *, ready_timeout_s: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + ready_timeout_s
        last_state = ""
        warmup_retries = 0
        next_retry_at = loop.time() + 20.0
        while loop.time() < deadline:
            if warmup_retries < 3 and loop.time() >= next_retry_at:
                # 兜底重发 warmup 规格（首发见 ensure_ready；宿主偶发吞掉第一发）。
                warmup_retries += 1
                next_retry_at = loop.time() + 20.0
                await self._trigger_warmup()
            if page.is_closed():
                raise BuildSurfaceError(
                    "ensure_ready",
                    "PAGE_CLOSED",
                    "Build 宿主页在就绪轮询期间被关闭",
                    "重试 ensure_ready；若反复关闭检查渲染进程崩溃日志",
                )
            snapshot: object = None
            with suppress(Exception):
                snapshot = await page.evaluate(BUILD_SIGNER_STATE_JS)
            if isinstance(snapshot, dict):
                last_state = str(snapshot.get("state") or "")
                if last_state == "ready":
                    self.state.ready = True
                    self.state.ready_state = last_state
                    harvest = snapshot.get("harvest")
                    self.state.harvest = harvest if isinstance(harvest, dict) else None
                    self.state.last_error = ""
                    headers = (
                        harvest.get("headers") if isinstance(harvest, dict) else None
                    )
                    logger.info(
                        "[build] 签名器就绪: harvest 头=%s",
                        sorted(headers) if isinstance(headers, dict) else "n/a",
                    )
                    return
                if last_state == "failed":
                    last_error = str(snapshot.get("lastError") or "unknown")
                    self.state.last_error = last_error
                    raise BuildSurfaceError(
                        "ensure_ready",
                        "SIGNER_FAILED",
                        f"window.__buildSigner.state=failed lastError={last_error}",
                        "查看 lastError 详情；按失败类型重试或更新 build_signer.js",
                    )
            await asyncio.sleep(0.5)

        code, message = await self._probe_diagnosis(page)
        raise BuildSurfaceError(
            "ensure_ready",
            code,
            f"签名器未在 {ready_timeout_s:.0f}s 内就绪 (last_state={last_state or 'unknown'}): {message}",
            _DETECT_GUIDANCE.get(code, _DETECT_GUIDANCE["UNKNOWN"]),
        )

    async def _probe_diagnosis(self, page: CDPPage) -> tuple[str, str]:
        with suppress(Exception):
            probe = await page.evaluate(BUILD_SURFACE_DETECT_JS)
            if isinstance(probe, dict):
                return (
                    str(probe.get("code") or "UNKNOWN"),
                    str(probe.get("message") or ""),
                )
        return "UNKNOWN", "页面诊断探测失败（evaluate 异常）"

    async def stream_generate(
        self,
        *,
        model: str,
        json_body: str | dict[str, object],
        timeout_s: float = 180.0,
    ) -> AsyncGenerator[tuple[str, object], None]:
        """Dispatch one build-surface generation and yield chat-surface-shaped events."""
        state = self.state
        page = state.page
        if page is None or page.is_closed():
            raise BuildSurfaceError(
                "stream",
                "PAGE_NOT_READY",
                "Build 宿主页不存在或已关闭",
                "先调用 ensure_ready 建立就绪页面",
            )
        if not state.ready:
            raise BuildSurfaceError(
                "stream",
                "SURFACE_NOT_READY",
                f"Build 面尚未就绪 (ready_state={state.ready_state or 'unknown'})",
                "先调用 ensure_ready 并确认返回 ready=True",
            )

        task_id = uuid.uuid4().hex[:12]
        json_text = (
            json_body
            if isinstance(json_body, str)
            else json.dumps(json_body, ensure_ascii=False, separators=(",", ":"))
        )

        self._ensure_page_binding(page)
        if hasattr(page, "add_binding"):
            with suppress(Exception):
                await page.add_binding(STREAM_PUSH_BINDING)
        elif hasattr(page, "cdp") and hasattr(page.cdp, "send"):
            with suppress(Exception):
                await page.cdp.send("Runtime.addBinding", {"name": STREAM_PUSH_BINDING})

        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        state.task_queues[task_id] = queue

        latest_usage: dict[str, object] | None = None
        latest_finish_reason: int | None = None
        decoded_frames = 0
        dropped_samples: list[str] = []
        terminal = False

        try:
            try:
                # Fire-and-forget: frames回流 through the binding while execute() runs.
                await page.evaluate(
                    BUILD_SIGNER_EXECUTE_JS,
                    {"taskId": task_id, "model": model, "jsonBody": json_text},
                    await_promise=False,
                )
            except Exception as exc:
                raise BuildSurfaceError(
                    "stream",
                    "DISPATCH_FAILED",
                    f"window.__buildSigner.execute 分发失败: {exc}",
                    "确认 ensure_ready 已完成且页面未导航离开",
                ) from exc

            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_s
            while not terminal:
                if page.is_closed():
                    raise BuildSurfaceError(
                        "stream",
                        "PAGE_CLOSED",
                        "Build 宿主页在流式过程中被关闭",
                        "重试 ensure_ready 后重新发起请求",
                    )
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=min(remaining, 1.0)
                    )
                except TimeoutError:
                    if page.is_closed():
                        raise BuildSurfaceError(
                            "stream",
                            "PAGE_CLOSED",
                            "Build 宿主页在流式过程中被关闭",
                            "重试 ensure_ready 后重新发起请求",
                        ) from None
                    continue

                event_type = str(event.get("type") or "")
                if event_type == "status":
                    status = int(str(event.get("status") or 0))
                    yield ("status", status)
                    deadline = loop.time() + timeout_s
                elif event_type == "chunk":
                    text = str(event.get("text") or "")
                    if not text:
                        continue
                    deadline = loop.time() + timeout_s
                    leaves, dropped = decode_holder_frame(text)
                    dropped_samples.extend(dropped)
                    for leaf in leaves:
                        wire = conv_chunk(leaf)
                        if wire is None:
                            dropped_samples.append(_sample(leaf))
                            continue
                        decoded_frames += 1
                        usage = parse_chunk_usage(wire)
                        if usage:
                            latest_usage = usage
                        response_id = (
                            wire[WIRE_RESPONSE_ID_INDEX]
                            if len(wire) > WIRE_RESPONSE_ID_INDEX
                            else None
                        )
                        if isinstance(response_id, str) and response_id:
                            yield ("response_id", response_id)
                        candidate = parse_response_chunk(wire)
                        if candidate.finish_reason is not None:
                            latest_finish_reason = candidate.finish_reason
                        if candidate.thinking:
                            yield ("thinking", candidate.thinking)
                        if candidate.reasoning_images:
                            yield ("reasoning_images", candidate.reasoning_images)
                        if candidate.function_calls:
                            yield ("tool_calls", candidate.function_calls)
                        if candidate.images:
                            yield ("images", candidate.images)
                        if candidate.text:
                            yield ("body", candidate.text)
                        if candidate.thought_signature:
                            yield ("thought_signature", candidate.thought_signature)
                elif event_type == "error":
                    message = str(event.get("message") or "unknown build surface error")
                    raise BuildSurfaceError(
                        "stream",
                        "SIGNER_ERROR",
                        message,
                        "查看 build_signer.js 上报的错误；必要时重跑 ensure_ready",
                    )
                elif event_type in ("done", "aborted"):
                    while not queue.empty():
                        trailing = queue.get_nowait()
                        if str(trailing.get("type") or "") == "chunk":
                            text = str(trailing.get("text") or "")
                            if text:
                                dropped_samples.extend(decode_holder_frame(text)[1])
                    terminal = True
                    break

            if not terminal:
                raise BuildSurfaceError(
                    "stream",
                    "TIMEOUT",
                    f"Build 面流式超时 ({timeout_s:.0f}s, frames={decoded_frames})",
                    "增大 timeout_s 或检查 warmup/账号状态；确认上游未挂起",
                )
            if decoded_frames == 0:
                detail = "上游未产出可解析帧"
                if dropped_samples:
                    detail = f"{detail}；丢弃样本={dropped_samples[:3]}"
                raise BuildSurfaceError(
                    "stream",
                    "NO_CONVERTIBLE_FRAMES",
                    detail,
                    "抓取 Holder 原始帧核对格式（R4：Holder 结构可能已变更）",
                )
            if dropped_samples:
                logger.warning(
                    "build surface 丢弃 %d 个非 chunk 帧 (样本=%s)",
                    len(dropped_samples),
                    dropped_samples[:3],
                )
        finally:
            state.task_queues.pop(task_id, None)

        if latest_finish_reason is not None:
            yield ("finish_reason", latest_finish_reason)
        yield ("usage", latest_usage)
        yield ("done", None)

    async def abort_stream(self, task_id: str, reason: str = "stream_aborted") -> bool:
        """Abort a specific in-flight task by rid without touching other streams."""
        queue = self.state.task_queues.get(task_id)
        if queue is None:
            return False
        try:
            queue.put_nowait({"type": "error", "message": reason})
        except asyncio.QueueFull:
            logger.warning("build surface 中止失败: 队列已满 (rid=%s)", task_id)
            return False
        return True


__all__ = [
    "BUILD_SIGNER_STATE_JS",
    "BUILD_SURFACE_DETECT_JS",
    "BUILD_SURFACE_READY_TIMEOUT_S",
    "BUILD_SURFACE_URL_TEMPLATE",
    "DEFAULT_BUILD_APP_ID",
    "BuildSurface",
    "BuildSurfaceError",
    "BuildSurfaceState",
    "conv_chunk",
    "decode_holder_frame",
    "resolve_build_app_id",
]
