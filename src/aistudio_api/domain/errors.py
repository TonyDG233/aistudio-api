"""Domain errors for AI Studio interactions."""

from __future__ import annotations

import json


class AistudioError(Exception):
    pass


_ACCOUNT_VERIFICATION_MARKERS: tuple[str, ...] = (
    "aistudio.google.com/verify-account",
    "aistudio.google.com/verify?",
    "must be verified before it can continue using the gemini api",
    "must be verified before it can use the gemini api",
    "account verification required to restore access",
    "free_tier_verification_required",
)


def is_account_verification_required_message(text: object) -> bool:
    """识别 Google 移动设备账号验证（verify-account）风控报错。"""
    if not isinstance(text, str) or not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _ACCOUNT_VERIFICATION_MARKERS)


class AuthError(AistudioError):
    """认证/鉴权失败。

    ``verification_required`` 为 True 表示上游要求完成 Google 移动设备账号
    验证（verify-account 风控）；此类账号必须立即隔离切号，禁止同号重签重试。
    """

    def __init__(self, message: str = "") -> None:
        self.verification_required = is_account_verification_required_message(message)
        super().__init__(message)


class SessionExpiredError(AuthError):
    """明确被 Google 重定向至登录页（Cookie 真正失效）。"""


class UsageLimitExceeded(AistudioError):
    pass


class TierRequiredError(AistudioError):
    """付费模型 tier 不足（D-04/D-06）。

    This is an application-layer domain error; it never carries FastAPI types.
    The API adapter maps it to HTTP 403 and preserves the four frozen fields
    ``tier/model/required_tier/current_tier``.
    """

    status_code = 403
    code = "tier_required"

    def __init__(
        self,
        *,
        model: str,
        tier: str,
        required_tier: str,
        current_tier: str,
        message: str | None = None,
    ) -> None:
        self.model = model
        self.tier = tier
        self.required_tier = required_tier
        self.current_tier = current_tier
        super().__init__(
            message
            or (
                f"模型 '{model}' 需要 {required_tier} 及以上订阅，"
                f"当前账号为 {current_tier}。"
            )
        )

    def as_fields(self) -> dict[str, str]:
        return {
            "tier": self.tier,
            "model": self.model,
            "required_tier": self.required_tier,
            "current_tier": self.current_tier,
        }


class RoutingError(AistudioError):
    """Rejected request routing (capacity or exhausted switching)."""

    status_code = 503
    code = "surface_unavailable"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        surface: str | None = None,
        advice: str | None = None,
    ) -> None:
        self.surface = surface
        self.advice = advice or "请稍后重试，或前往管理页检查账号/实例状态。"
        if code:
            self.code = code
        super().__init__(message)

    def as_fields(self) -> dict[str, str]:
        fields = {"type": self.code, "reason": self.code}
        if self.surface:
            fields["surface"] = self.surface
        if self.advice:
            fields["advice"] = self.advice
        return fields


class EngineSwitchingError(RoutingError):
    """A transactional engine hot-switch is in progress; retry later.

    ``retry_after`` is surfaced as the HTTP ``Retry-After`` header so clients
    and the web console back off instead of spinning against the drain window.
    """

    status_code = 503
    code = "engine_switching"

    def __init__(self, message: str, *, retry_after: int = 1) -> None:
        self.retry_after = max(1, int(retry_after))
        super().__init__(message, code=self.code)


class SurfaceCapacityError(RoutingError):
    """No instance capacity in the model's lane/surface."""

    def __init__(
        self,
        *,
        surface: str,
        message: str | None = None,
        advice: str | None = None,
    ) -> None:
        code = (
            "surface_instance_busy" if surface == "build" else "surface_quota_exceeded"
        )
        super().__init__(
            message
            or (
                f"渠道 '{surface}' 实例容量已满，当前请求未分配实例；"
                "请稍后重试或前往管理页检查实例。"
            ),
            code=code,
            surface=surface,
            advice=advice,
        )


class SurfaceSwitchExhaustedError(RoutingError):
    """429 switch budget/candidates exhausted (must be 503, never 429)."""

    def __init__(
        self,
        *,
        surface: str,
        message: str | None = None,
        advice: str | None = None,
    ) -> None:
        super().__init__(
            message
            or (
                "所有合格实例均达到配额上限，本轮切换已耗尽；"
                "请等待额度刷新（美西午夜）或前往管理页检查账号。"
            ),
            code="surface_switch_exhausted",
            surface=surface,
            advice=advice,
        )


class SurfaceUnavailableError(RoutingError):
    """The requested surface is not implemented/enabled yet."""

    def __init__(
        self,
        *,
        surface: str,
        message: str | None = None,
        advice: str | None = None,
    ) -> None:
        super().__init__(
            message or f"渠道 '{surface}' 当前不可用。",
            code="surface_unavailable",
            surface=surface,
            advice=advice,
        )


class RequestError(AistudioError):
    def __init__(self, status: int, message: str = ""):
        self.status = status
        super().__init__(f"HTTP {status}: {message}" if message else f"HTTP {status}")


class BrowserTransportError(RuntimeError):
    """In-page fetch/XHR network transport failure before upstream HTTP completion."""

    net_evidence: list[dict[str, object]] | None = None


GRPC_CODE_TO_HTTP: dict[int, int] = {
    1: 499,  # CANCELLED
    2: 500,  # UNKNOWN
    3: 400,  # INVALID_ARGUMENT
    4: 504,  # DEADLINE_EXCEEDED
    5: 404,  # NOT_FOUND
    6: 409,  # ALREADY_EXISTS
    7: 403,  # PERMISSION_DENIED
    8: 429,  # RESOURCE_EXHAUSTED
    9: 400,  # FAILED_PRECONDITION
    10: 409,  # ABORTED
    11: 400,  # OUT_OF_RANGE
    12: 501,  # UNIMPLEMENTED
    13: 500,  # INTERNAL
    14: 503,  # UNAVAILABLE
    15: 500,  # DATA_LOSS
    16: 401,  # UNAUTHENTICATED
}


def parse_upstream_grpc_error(raw_response: str) -> tuple[int | None, str]:
    """Parse Google JSPB/JSON error structures into (grpc_code, message)."""
    if not raw_response:
        return None, ""
    text = raw_response.strip()

    normalized = text
    if normalized.startswith("[,"):
        normalized = "[null," + normalized[2:]

    try:
        data = json.loads(normalized)
        if isinstance(data, list):

            def _find_grpc(obj: object) -> tuple[int | None, str] | None:
                if isinstance(obj, list):
                    if (
                        len(obj) >= 2
                        and isinstance(obj[0], int)
                        and isinstance(obj[1], str)
                        and obj[1]
                    ):
                        return obj[0], obj[1]
                    for item in obj:
                        res = _find_grpc(item)
                        if res is not None:
                            return res
                elif isinstance(obj, dict):
                    err = obj.get("error")
                    if isinstance(err, dict) and "message" in err:
                        code = err.get("code")
                        return (
                            int(code) if isinstance(code, (int, float)) else None
                        ), str(err["message"])
                return None

            found = _find_grpc(data)
            if found is not None:
                return found
    except Exception:
        pass

    return None, text


def classify_error(status: int, body: str) -> AistudioError:
    grpc_code, msg = parse_upstream_grpc_error(body)
    clean_msg = msg or body[:200]
    # 该分支有意置于 429/401/403 判断之前：一旦报文携带项目/账号级移动设备
    # 验证特征（含结构化解析截断后仅原始 body 仍保留特征的情况），统一升级为
    # 账号级 session_expired 熔断隔离，直至人工扫码验证或重置锁定。
    if is_account_verification_required_message(
        clean_msg
    ) or is_account_verification_required_message(body):
        return AuthError(f"禁止访问(需验证账号): {clean_msg}")
    if grpc_code == 8 or status == 429:
        return UsageLimitExceeded(f"配额用完: {clean_msg}")
    if grpc_code == 16 or status == 401:
        return AuthError(f"认证失败: {clean_msg}")
    if grpc_code == 7 or status == 403:
        return AuthError(f"禁止访问: {clean_msg}")
    effective_status = GRPC_CODE_TO_HTTP.get(grpc_code, status) if grpc_code else status
    return RequestError(effective_status, clean_msg)


def is_transient_rpc_error(exc: Exception) -> bool:
    """识别 Google 上游偶发且完全随机的 RPC 路由 404 (Ambiguous request for service)。"""
    if isinstance(exc, RequestError) and exc.status == 404:
        err = str(exc).lower()
        return "ambiguous request for service" in err and "generatecontent" in err
    return False


def is_browser_session_hang_error(exc: Exception) -> bool:
    """识别由于 CDP 断开、模板捕获超时、页面传输中断或 BotGuard 握手未就绪引起的会话异常。"""
    if isinstance(exc, BrowserTransportError):
        return True
    if isinstance(exc, (RuntimeError, TimeoutError)):
        err = str(exc).lower()
        return any(
            k in err
            for k in (
                "template capture",
                "botguard",
                "timeout",
                "cdp",
                "closed",
                "aborted",
                "snapshot",
                "hook install",
            )
        )
    return False
