"""解析浏览器 cookie（支持 JSON 数组、Netscape 格式与 Header KV），并提供 Google 多账号探活。"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

from aistudio_api.infrastructure.account.benefit_tier import (
    parse_benefit_tier_envelope,
)
from aistudio_api.infrastructure.gateway.public_web_constants import (
    AI_STUDIO_HOME_URL,
    AI_STUDIO_ORIGIN,
    BENEFIT_TIER_URL,
    EXT_BIN,
    GRPC_WEB_USER_AGENT,
    LIST_MODELS_URL,
    WEB_API_KEY,
    aistudio_new_chat_url,
)
from aistudio_api.infrastructure.utils.common import mask_email
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("cookie_parser")


def parse_benefit_tier_response(raw_data: object) -> str:
    """解析 GetAiStudioBenefitTier 响应数组为标准权益名称 (Free / Pro / Ultra / Plus)。

    解码与归一化统一委托给 :func:`parse_benefit_tier_envelope`；未知 code 在本
    调用面按 Free 处理（与历史行为一致）。
    """
    return parse_benefit_tier_envelope(raw_data).display_tier()


def parse_benefit_tier_strict(raw_data: object) -> str | None:
    """解析 RPC 响应；只有上游明确给出的等级才返回字符串，否则 ``None``。

    ``None`` covers malformed/unparseable bodies, non-list values, unknown
    numeric codes and network-level failures. It must never be turned into
    ``"Free"`` by callers, because that would overwrite a real Pro/Ultra tier.
    """
    return parse_benefit_tier_envelope(raw_data).strict_tier()


# Cookies that need httpOnly=False so JS can read them for SAPISIDHASH.
_AUTH_COOKIE_NAMES = {
    "SID",
    "APISID",
    "SAPISID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
}

_DOMAIN_OVERRIDES: dict[str, list[str]] = {
    "OSID": ["aistudio.google.com"],
    "__Secure-OSID": ["aistudio.google.com"],
    "__Secure-BUCKET": ["aistudio.google.com"],
    "OTZ": ["accounts.google.com"],
    "__Host-GAPS": ["accounts.google.com"],
    "__Host-1PLSID": ["accounts.google.com"],
    "__Host-3PLSID": ["accounts.google.com"],
    "LSID": ["accounts.google.com"],
    "SMSV": ["accounts.google.com"],
    "LSOLH": ["accounts.google.com"],
    "ACCOUNT_CHOOSER": ["accounts.google.com"],
    "__Secure-1PSIDTS": [".google.com", "aistudio.google.com", "accounts.google.com"],
    "__Secure-3PSIDTS": [".google.com", "aistudio.google.com", "accounts.google.com"],
}

# 外部导入时过滤的临时、遥测与设备绑定 Cookie，由浏览器在当前环境中自动协商生成。
DISCARDABLE_IMPORT_COOKIE_NAMES: frozenset[str] = frozenset(
    {
        "_ga",
        "_gid",
        "_gat",
        "_gcl_au",
        "1P_JAR",
        "DV",
        "AEC",
        "NID",
        "SIDCC",
        "__Secure-1PSIDCC",
        "__Secure-3PSIDCC",
        "OSID",
        "__Secure-OSID",
    }
)

ACCOUNT_HOST_ONLY_COOKIES: frozenset[str] = frozenset(
    {
        "LSID",
        "__Host-1PLSID",
        "__Host-3PLSID",
        "__Host-GAPS",
        "ACCOUNT_CHOOSER",
    }
)

_ALLOWED_HOST_COOKIES: frozenset[str] = frozenset(
    {
        "__Host-1PLSID",
        "__Host-3PLSID",
    }
)

# Minimal authentication cookie set accepted by Google RPC / SAPISIDHASH signing.
# This is only for outbound signed RPC requests; browser session cookies and DOM
# storage must keep the full jar (see parse_storage_state / build_google_cookie_list).
MINIMAL_COOKIE_WHITELIST: frozenset[str] = frozenset(
    {
        "SID",
        "HSID",
        "SSID",
        "APISID",
        "SAPISID",
        "__Secure-1PSID",
        "__Secure-3PSID",
        "__Secure-1PAPISID",
        "__Secure-3PAPISID",
        "__Secure-1PSIDTS",
        "__Secure-3PSIDTS",
        "SIDCC",
        "__Secure-1PSIDCC",
        "__Secure-3PSIDCC",
        "NID",
    }
)

ROTATE_COOKIE_WHITELIST: frozenset[str] = (
    MINIMAL_COOKIE_WHITELIST | ACCOUNT_HOST_ONLY_COOKIES
)

ROTATING_SESSION_COOKIE_NAMES: tuple[str, ...] = (
    "__Secure-1PSIDTS",
    "__Secure-3PSIDTS",
    "SIDCC",
    "__Secure-1PSIDCC",
    "__Secure-3PSIDCC",
)


def filter_minimal_cookies(
    cookies: dict[str, str], *, include_account_host: bool = False
) -> dict[str, str]:
    """过滤保留核心鉴权 Cookie；默认排除 accounts.google.com 专属 Cookie。"""
    allowed = (
        ROTATE_COOKIE_WHITELIST if include_account_host else MINIMAL_COOKIE_WHITELIST
    )
    return {k: v for k, v in cookies.items() if k in allowed and v}


def compute_session_anchor(cookies: dict[str, str]) -> str:
    """提取浏览器会话锚点（用于识别同一窗口下的多登子账号 u/0..u/N）。"""
    if not cookies:
        return ""
    return (
        cookies.get("SAPISID")
        or cookies.get("__Secure-3PAPISID")
        or cookies.get("__Secure-1PAPISID")
        or cookies.get("SID")
        or cookies.get("__Secure-3PSID")
        or ""
    )


def apply_set_cookies(
    cookies: dict[str, str],
    set_cookie_headers: list[str] | tuple[str, ...],
) -> list[str]:
    """将 HTTP 响应 Set-Cookie 头合并至 cookies 字典，返回发生变更的 Cookie 名称列表。"""
    updated: list[str] = []
    if not set_cookie_headers:
        return updated
    for raw in set_cookie_headers:
        if not raw or not isinstance(raw, str):
            continue
        parts = [s.strip() for s in raw.split(";")]
        if not parts or not parts[0]:
            continue
        nv, attrs = parts[0], parts[1:]
        eq = nv.find("=")
        if eq <= 0:
            continue
        name = nv[:eq].strip()
        val = nv[eq + 1 :].strip()
        if not name or not val or val == "deleted":
            continue
        if name not in ROTATE_COOKIE_WHITELIST:
            continue
        attr_str = "; ".join(attrs)
        if re.search(r"domain=[^;]*gstatic\.com", attr_str, re.IGNORECASE):
            continue
        if re.search(r"expires=[^;]*19[789]\d", attr_str, re.IGNORECASE):
            continue
        if re.search(r"max-age\s*=\s*(?:0|-\d+)", attr_str, re.IGNORECASE):
            continue
        if cookies.get(name) != val:
            cookies[name] = val
            updated.append(name)
    return updated


_STUDIO_SCOPED_COOKIES = frozenset({"OSID", "__Secure-OSID", "__Secure-BUCKET"})
_ACCOUNT_SCOPED_COOKIES = frozenset(
    {
        "OTZ",
        "__Host-GAPS",
        "__Host-1PLSID",
        "__Host-3PLSID",
        "LSID",
        "SMSV",
        "LSOLH",
        "ACCOUNT_CHOOSER",
    }
)


def _cookie_domain_rank(name: str, domain: object) -> int:
    """Prefer the canonical Google domain when exports contain regional copies."""
    normalized = str(domain or "").strip().lower()
    if not normalized:
        return 0
    if name in _STUDIO_SCOPED_COOKIES:
        order = {
            "aistudio.google.com": 0,
            ".google.com": 1,
            "accounts.google.com": 2,
        }
    elif name in _ACCOUNT_SCOPED_COOKIES:
        order = {
            "accounts.google.com": 0,
            ".google.com": 1,
            "aistudio.google.com": 2,
        }
    else:
        order = {
            ".google.com": 0,
            "aistudio.google.com": 1,
            "accounts.google.com": 2,
        }
    return order.get(normalized, 3)


def parse_raw_cookies(raw: object) -> dict[str, str]:
    """万能 Cookie 解析器，支持：
    1. JSON 数组（EditThisCookie / Cookie-Editor 导出：`[{"name": "...", "value": "..."}, ...]`）
    2. JSON 对象（`{"cookies": [...]}` 或 `{"cookies": {"k": "v"}}`）
    3. Netscape / curl 格式（7 列 Tab 分隔）
    4. HTTP 请求头格式（`Cookie: k1=v1; k2=v2` 或带换行的多行键值对）

    JSON 条目带 domain 时按域名优先级取规范副本，避免区域域名覆盖
    ``accounts.google.com`` / ``.google.com`` 的有效值。
    """
    parsed: dict[str, str] = {}
    best_rank: dict[str, int] = {}
    if not raw:
        return parsed

    def _take_json_cookie(item: object) -> bool:
        if not (
            isinstance(item, dict)
            and item.get("name")
            and item.get("value") is not None
        ):
            return False
        name = str(item["name"]).strip()
        value = str(item["value"]).strip()
        if not name or not value:
            return False
        rank = _cookie_domain_rank(name, item.get("domain"))
        if name not in parsed or rank <= best_rank[name]:
            parsed[name] = value
            best_rank[name] = rank
        return True

    text = raw.strip() if isinstance(raw, str) else json.dumps(raw)

    if re.match(r"^cookie:\s*", text, re.IGNORECASE):
        text = re.sub(r"^cookie:\s*", "", text, flags=re.IGNORECASE)

    # 1. 尝试 JSON 格式
    if text.startswith(("{", "[")):
        try:
            obj = json.loads(text)
            if isinstance(obj, list):
                for item in obj:
                    _take_json_cookie(item)
                if parsed:
                    return parsed
            elif isinstance(obj, dict):
                if isinstance(obj.get("cookie"), str):
                    text = obj["cookie"]
                elif isinstance(obj.get("cookies"), str):
                    text = obj["cookies"]
                elif isinstance(obj.get("cookies"), list):
                    for item in obj["cookies"]:
                        _take_json_cookie(item)
                    if parsed:
                        return parsed
                elif isinstance(obj.get("cookies"), dict):
                    for k, v in obj["cookies"].items():
                        if k and v is not None:
                            parsed[str(k).strip()] = str(v).strip()
                    return parsed
        except Exception:
            pass

    # 2. 按行与分号拆分解析
    lines = re.split(r"[\r\n;]+", text)
    ignored_keys = {
        "domain",
        "path",
        "expires",
        "samesite",
        "secure",
        "httponly",
        "priority",
        "hostonly",
    }

    for line in lines:
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue

        # Netscape 7 列 Tab 分隔
        if "\t" in trimmed:
            cols = trimmed.split("\t")
            if len(cols) >= 7:
                k = cols[5].strip() if len(cols) > 5 else ""
                v = cols[6].strip() if len(cols) > 6 else ""
                if k and v and k.lower() not in ignored_keys:
                    parsed[k] = v
                continue

        # 标准 KV：name=value
        if "=" in trimmed:
            k, _, v = trimmed.partition("=")
            k = k.strip()
            v = v.strip()
            k = re.sub(r"^cookie:\s*", "", k, flags=re.IGNORECASE)
            if (v.startswith('"') and v.endswith('"')) or (
                v.startswith("'") and v.endswith("'")
            ):
                v = v[1:-1]
            if k and v and k.lower() not in ignored_keys:
                parsed[k] = v

    return parsed


def calculate_sapisid_hash(
    cookies: dict[str, str], origin: str = AI_STUDIO_ORIGIN
) -> str:
    """计算 Google SAPISIDHASH 鉴权头部。"""
    sapisid = cookies.get("SAPISID") or cookies.get("__Secure-1PAPISID") or ""
    sapisid1p = cookies.get("__Secure-1PAPISID") or sapisid
    sapisid3p = cookies.get("__Secure-3PAPISID") or sapisid
    if not sapisid:
        return ""

    ts = int(time.time())

    def calc_hash(val: str) -> str:
        return hashlib.sha1(f"{ts} {val} {origin}".encode()).hexdigest()

    h1 = f"SAPISIDHASH {ts}_{calc_hash(sapisid)}"
    h2 = f"SAPISID1PHASH {ts}_{calc_hash(sapisid1p)}"
    h3 = f"SAPISID3PHASH {ts}_{calc_hash(sapisid3p)}"
    return f"{h1} {h2} {h3}"


def build_google_cookie_list(
    pairs: Iterable[tuple[str, str]] | dict[str, str],
    *,
    allow_url_targets: bool = False,
    discard_transient: bool = False,
) -> list[dict[str, object]]:
    """Build Playwright/CDP compatible cookies from name/value pairs."""
    now = int(time.time())
    default_expires = now + 86400 * 180  # 180 天后过期

    seen: set[tuple[str, str, str]] = set()
    cookies: list[dict[str, object]] = []

    def _add_cookie(name: str, value: str, target: str) -> None:
        is_host_target = not target.startswith(".")

        if name.startswith("__Host-") and not allow_url_targets:
            return

        target_kind = "url" if allow_url_targets and is_host_target else "domain"
        target_value = f"https://{target}/" if target_kind == "url" else target
        key = (name, target_kind, target_value)
        if key in seen:
            return
        seen.add(key)

        cookie: dict[str, object] = {
            "name": name,
            "value": value,
            "secure": True,
            "httpOnly": name not in _AUTH_COOKIE_NAMES,
            "sameSite": "None",
            "expires": default_expires,
        }
        if target_kind == "url":
            cookie["url"] = target_value
        else:
            cookie["domain"] = target
            cookie["path"] = "/"
        cookies.append(cookie)

    item_list: list[tuple[str, str]] = (
        [(str(k), str(v)) for k, v in pairs.items()]
        if isinstance(pairs, dict)
        else [(str(k), str(v)) for k, v in pairs]
    )
    for name, value in item_list:
        if discard_transient and (
            name in DISCARDABLE_IMPORT_COOKIE_NAMES
            or name.startswith(("_ga_", "_gcl_"))
        ):
            continue
        targets = _DOMAIN_OVERRIDES.get(name)
        if not targets:
            targets = [".google.com"]
        for target in targets:
            _add_cookie(name, value, target)

    return cookies


def parse_storage_state(
    raw: object, *, discard_transient: bool = False
) -> dict[str, object] | None:
    """识别 Playwright storage-state（cookies 数组带 domain），原样保留跨域值。

    普通 ``name=value`` 文本/数组会被压平成单域字典，导致同名 cookie 的
    ``accounts.google.com`` 值覆盖 ``.google.com`` 值，Google RPC 会 401。

    ``discard_transient=True`` 用于外部导入，丢弃遥测/易失效/旧环境绑定
    Cookie；浏览器注入与回写保持默认 False，保留 accounts/OSID 等 Cookie。
    """
    obj: object = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text.startswith(("{", "[")):
            return None
        try:
            obj = json.loads(text)
        except Exception:
            return None

    origins: object = []
    if isinstance(obj, list):
        items = obj
    elif isinstance(obj, dict) and isinstance(obj.get("cookies"), list):
        items = obj["cookies"]
        origins = obj.get("origins") or []
    else:
        return None
    if not any(isinstance(item, dict) and item.get("domain") for item in items):
        return None

    now = time.time()
    cleaned: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = item.get("value")
        if not name or value is None:
            continue
        if discard_transient and (
            name in DISCARDABLE_IMPORT_COOKIE_NAMES
            or name.startswith(("_ga_", "_gcl_"))
        ):
            continue
        domain = str(item.get("domain") or "").lower()
        if not (domain == "google.com" or domain.endswith(".google.com")):
            continue
        expires = item.get("expires")
        if isinstance(expires, (int, float)) and 0 < expires < now:
            continue
        path = str(item.get("path") or "/")
        key = (name, domain, path)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(
            {
                "name": name,
                "value": str(value),
                "domain": domain,
                "path": path,
                "expires": expires if isinstance(expires, (int, float)) else -1,
                "httpOnly": bool(item.get("httpOnly", False)),
                "secure": bool(item.get("secure", True)),
                "sameSite": str(item.get("sameSite") or "None"),
            }
        )
    if not cleaned:
        return None
    return {"cookies": cleaned, "origins": origins}


_GOOGLE_RPC_DOMAINS = {"google.com", ".google.com"}


def cookies_for_google_rpc(raw: object) -> dict[str, str]:
    """取对 ``clients6.google.com`` 有效的 cookie（只认 google.com/.google.com）。"""
    state = parse_storage_state(raw)
    if state is not None:
        cookies = state.get("cookies")
        if not isinstance(cookies, list):
            return {}
        return {
            str(cookie["name"]): str(cookie["value"])
            for cookie in cookies
            if isinstance(cookie, dict)
            and str(cookie.get("domain") or "").lower() in _GOOGLE_RPC_DOMAINS
        }
    return parse_raw_cookies(raw)


def load_cookies_from_auth_file(auth_file: str | Path | None) -> dict[str, str]:
    """从 auth.json 中读取扁平化 Cookie 键值对。"""
    if not auth_file:
        return {}
    path = Path(auth_file)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        cookies = data.get("cookies")
        if isinstance(cookies, list):
            res: dict[str, str] = {}
            for item in cookies:
                if (
                    isinstance(item, dict)
                    and item.get("name")
                    and item.get("value") is not None
                ):
                    res[str(item["name"])] = str(item["value"])
            return res
        if isinstance(cookies, dict):
            return {str(k): str(v) for k, v in cookies.items() if k}
    except Exception as e:
        logger.warning("读取 Cookie 文件失败 (%s): %s", auth_file, e)
    return {}


def parse_cookie_string(
    raw: object, *, discard_transient: bool = True
) -> dict[str, object]:
    """将任意格式 cookie 解析为 storage state dict，包含 cookies 和 origins 字段。"""
    state = parse_storage_state(raw, discard_transient=discard_transient)
    if state is not None:
        return state
    cookie_dict = parse_raw_cookies(raw)
    return {
        "cookies": build_google_cookie_list(
            cookie_dict, allow_url_targets=False, discard_transient=discard_transient
        ),
        "origins": [],
    }


def parse_emails_from_ogs_html(
    html_text: str, default_auth_user: str = "0"
) -> dict[str, str]:
    """从 Google OGS Widget 或 AI Studio HTML 中提取 u/{index} 对应的真实邮箱字典。"""
    mapping: dict[str, str] = {}
    if not html_text:
        return mapping
    m_primary = re.search(r'"oPEP7c"\s*:\s*"([^"@\s]+@[^"\s]+)"', html_text)
    if m_primary:
        mapping[str(default_auth_user)] = m_primary.group(1).strip().lower()
    for m in re.finditer(
        r'data-au="(\d+)"\s+data-email="([^"@\s]+@[^"\s]+)"', html_text
    ):
        mapping[m.group(1)] = m.group(2).strip().lower()
    for m in re.finditer(
        r'data-email="([^"@\s]+@[^"\s]+)"\s+data-au="(\d+)"', html_text
    ):
        mapping[m.group(2)] = m.group(1).strip().lower()
    return mapping


async def probe_account_emails_map(
    raw_or_dict: object,
    auth_user: str = "0",
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, str]:
    """通过 Google OneGoogleWidget / AI Studio 页面探测全部已登录子账号的邮箱映射。"""
    cookie_dict = (
        raw_or_dict
        if isinstance(raw_or_dict, dict)
        and all(isinstance(v, str) for v in raw_or_dict.values())
        else cookies_for_google_rpc(raw_or_dict)
    )
    if not cookie_dict:
        return {}

    import httpx

    from aistudio_api.config import settings

    u_index = str(auth_user or "0")
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "Cookie": cookie_header,
    }

    async def _do_fetch(http_client: httpx.AsyncClient) -> dict[str, str]:
        resp = await http_client.get(
            f"https://ogs.google.com/u/{u_index}/widget/account",
            headers=headers,
        )
        if resp.status_code == 200:
            emails = parse_emails_from_ogs_html(resp.text, u_index)
            if emails:
                return emails
        resp_ai = await http_client.get(
            aistudio_new_chat_url(auth_user=u_index, temporary=False),
            headers=headers,
        )
        if resp_ai.status_code == 200:
            return parse_emails_from_ogs_html(resp_ai.text, u_index)
        return {}

    try:
        if client is not None:
            return await _do_fetch(client)
        async with httpx.AsyncClient(
            timeout=8.0, proxy=settings.proxy_url, follow_redirects=True
        ) as new_client:
            return await _do_fetch(new_client)
    except Exception as e:
        logger.debug("通过 HTTP 探测子账号 u/%s 邮箱失败: %s", u_index, e)
    return {}


async def probe_account_email(
    raw_or_dict: object,
    auth_user: str = "0",
) -> str | None:
    """探测单个指定 auth_user 的登录邮箱。"""
    u_index = str(auth_user or "0")
    emails_map = await probe_account_emails_map(raw_or_dict, auth_user=u_index)
    if u_index in emails_map:
        return emails_map[u_index]
    if u_index != "0":
        root_map = await probe_account_emails_map(raw_or_dict, auth_user="0")
        if u_index in root_map:
            return root_map[u_index]
    return None


async def probe_account_benefit_tier(
    raw_or_dict: object,
    auth_user: str = "0",
) -> str | None:
    """通过 GetAiStudioBenefitTier RPC 探测指定子账号的订阅权益等级。

    只有上游明确返回可解析的等级时才返回字符串；无 Cookie、网络/代理异常、
    非 200 或响应体不可解析时返回 ``None``，调用方不得将其降级为 Free。
    """
    cookie_dict = (
        raw_or_dict
        if isinstance(raw_or_dict, dict)
        and all(isinstance(v, str) for v in raw_or_dict.values())
        else cookies_for_google_rpc(raw_or_dict)
    )
    if not cookie_dict:
        return None

    import httpx

    from aistudio_api.config import settings

    u_index = str(auth_user or "0")
    auth_header = calculate_sapisid_hash(cookie_dict)
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
    headers = {
        "Content-Type": "application/json+protobuf",
        "X-User-Agent": GRPC_WEB_USER_AGENT,
        "X-Goog-Api-Key": WEB_API_KEY,
        "X-Goog-AuthUser": u_index,
        "X-Goog-Ext-519733851-bin": EXT_BIN,
        "X-AIStudio-Visit-Id": f"v1_{uuid.uuid4()}",
        "Origin": AI_STUDIO_ORIGIN,
        "Referer": AI_STUDIO_HOME_URL,
        "Cookie": cookie_header,
    }
    if auth_header:
        headers["Authorization"] = auth_header

    tier_url = BENEFIT_TIER_URL
    try:
        async with httpx.AsyncClient(timeout=8.0, proxy=settings.proxy_url) as client:
            resp = await client.post(tier_url, headers=headers, content="[]")
            if resp.status_code != 200:
                logger.debug(
                    "探测子账号 u/%s 权益等级返回 HTTP %d", u_index, resp.status_code
                )
                return None
            return parse_benefit_tier_strict(resp.text)
    except Exception as e:
        logger.debug("探测子账号 u/%s 权益等级失败: %s", u_index, e)
        return None


DEFAULT_BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"


async def rotate_google_cookies(
    cookies: dict[str, str],
    *,
    client: httpx.AsyncClient | None = None,
    user_agent: str | None = None,
    timeout: float = 15.0,
) -> dict[str, object]:
    """调用 accounts.google.com/RotateCookies 刷新短效滚动凭据 (__Secure-1PSIDTS / SIDCC)。

    若调用成功，原地更新传入的 cookies 字典并返回 {"ok": True, "status": 200, "updated": [...]}。
    """
    if not cookies:
        return {"ok": False, "status": 0, "updated": []}

    has_rotation_seed = any(
        cookies.get(k)
        for k in (
            "LSID",
            "__Host-1PLSID",
            "__Host-3PLSID",
            "__Secure-1PSIDTS",
            "__Secure-3PSIDTS",
        )
    )
    if not has_rotation_seed:
        return {"ok": False, "status": 0, "updated": [], "skipped": "missing_seed"}

    import httpx

    from aistudio_api.config import settings

    rotate_cookies_map = filter_minimal_cookies(cookies, include_account_host=True)
    cookie_header = "; ".join(f"{k}={v}" for k, v in rotate_cookies_map.items())
    if not cookie_header:
        return {"ok": False, "status": 0, "updated": []}

    effective_ua = user_agent or DEFAULT_BROWSER_USER_AGENT

    headers = {
        "Cookie": cookie_header,
        "User-Agent": effective_ua,
        "Content-Type": "application/json",
        "Origin": "https://accounts.google.com",
        "Referer": "https://accounts.google.com/RotateCookiesPage",
    }
    body = '[null,"-1234567890",0]'

    async def _do_rotate(http_client: httpx.AsyncClient) -> dict[str, object]:
        resp = await http_client.post(
            "https://accounts.google.com/RotateCookies",
            headers=headers,
            content=body,
        )
        set_cookies = resp.headers.get_list("set-cookie")
        updated = apply_set_cookies(cookies, set_cookies)
        return {
            "ok": resp.status_code == 200,
            "status": resp.status_code,
            "updated": updated,
        }

    try:
        if client is not None:
            return await _do_rotate(client)
        async with httpx.AsyncClient(
            timeout=timeout, proxy=settings.proxy_url
        ) as new_client:
            return await _do_rotate(new_client)
    except Exception as e:
        logger.debug("请求 RotateCookies 失败: %s", e)
        return {"ok": False, "status": 0, "updated": [], "error": str(e)}


async def probe_google_accounts_infinite(
    raw_or_dict: object,
    *,
    max_fails_in_a_row: int = 1,
) -> list[dict[str, object]]:
    """一路向下探测多账号登录索引（u/0, u/1, u/2...），直到探测失败为止。

    Returns:
        有效账号列表，例如 [{"auth_user": "0", "status_code": 200, "name": "Google Account (u/0)"}, ...]
    """
    cookie_dict = cookies_for_google_rpc(raw_or_dict)
    if not cookie_dict:
        return []

    import httpx

    from aistudio_api.config import settings

    cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
    valid_accounts: list[dict[str, object]] = []

    auth_user_idx = 0
    consecutive_failures = 0
    async with httpx.AsyncClient(timeout=10.0, proxy=settings.proxy_url) as client:
        emails_map = await probe_account_emails_map(
            cookie_dict, auth_user="0", client=client
        )
        while True:
            u_index = str(auth_user_idx)
            auth_header = calculate_sapisid_hash(cookie_dict)
            headers = {
                "Content-Type": "application/json+protobuf",
                "X-User-Agent": GRPC_WEB_USER_AGENT,
                "X-Goog-Api-Key": WEB_API_KEY,
                "X-Goog-AuthUser": u_index,
                "X-Goog-Ext-519733851-bin": EXT_BIN,
                "X-AIStudio-Visit-Id": f"v1_{uuid.uuid4()}",
                "Origin": AI_STUDIO_ORIGIN,
                "Referer": AI_STUDIO_HOME_URL,
                "Cookie": cookie_header,
            }
            if auth_header:
                headers["Authorization"] = auth_header

            url = LIST_MODELS_URL
            try:
                resp = await client.post(url, headers=headers, content="[]")
                # 200 (OK), 400 (Bad Request on param but authed), 403 (Forbidden/Permission/Quota) 均表示此账号 Session 存在
                # 401 表示该 auth_user 未登录或 Session 无效
                if resp.status_code in (200, 400, 403):
                    consecutive_failures = 0
                    detected_tier: str | None = None
                    tier_url = BENEFIT_TIER_URL
                    try:
                        tier_resp = await client.post(
                            tier_url, headers=headers, content="[]"
                        )
                        if tier_resp.status_code == 200:
                            detected_tier = parse_benefit_tier_strict(tier_resp.text)
                    except Exception:
                        pass
                    detected_email = emails_map.get(u_index)
                    if not detected_email:
                        try:
                            extra_map = await probe_account_emails_map(
                                cookie_dict, auth_user=u_index, client=client
                            )
                            emails_map.update(extra_map)
                            detected_email = emails_map.get(u_index)
                        except Exception:
                            pass
                    valid_accounts.append(
                        {
                            "auth_user": u_index,
                            "status_code": resp.status_code,
                            "name": f"Google Account (u/{u_index})",
                            "email": detected_email,
                            "tier": detected_tier,
                        }
                    )
                    logger.info(
                        "探活子账号 u/%s 成功: 状态码=%d, 邮箱=%s, 等级=%s",
                        u_index,
                        resp.status_code,
                        mask_email(detected_email) if detected_email else "无",
                        detected_tier,
                    )
                elif resp.status_code == 401:
                    consecutive_failures += 1
                    logger.debug("子账号 u/%s 未授权 (HTTP 401)", u_index)
                else:
                    consecutive_failures += 1
                    logger.debug(
                        "子账号 u/%s 探活返回 HTTP %d", u_index, resp.status_code
                    )
            except Exception as e:
                consecutive_failures += 1
                logger.debug("子账号 u/%s 探活请求异常: %s", u_index, e)

            if consecutive_failures >= max_fails_in_a_row:
                break

            auth_user_idx += 1

    return valid_accounts
