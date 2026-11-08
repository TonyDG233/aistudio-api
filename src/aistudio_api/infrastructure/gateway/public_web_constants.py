"""Single source of AI Studio public web constants.

These values are the public browser-facing identifiers (web API key, ext-bin,
RPC endpoints, AI Studio URLs and gRPC-web user agent) that the web client
ships to every visitor. They are not credentials or secrets; keeping them in
one module prevents infrastructure call sites from drifting apart.
"""

from __future__ import annotations

import re

WEB_API_KEY = "AIzaSyDdP816MREB3SkjZO04QXbjsigfcI0GWOs"
WEB_API_KEY_PREFIX = WEB_API_KEY[:10]
WEB_API_KEY_RE = re.compile(r"AIzaSy[A-Za-z0-9_-]{25,45}")
EXT_BIN = "CAESAUwwATgEQABQBGICSlBwAHgBkAEAmAEB"
EXT_BIN_HEADER = "X-Goog-Ext-519733851-bin"

RPC_HOST = "alkalimakersuite-pa.clients6.google.com"
RPC_SERVICE_PATH = (
    "/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/"
)
RPC_BASE_URL = f"https://{RPC_HOST}{RPC_SERVICE_PATH}"


def rpc_url(method: str) -> str:
    """Return the full RPC URL for one MakerSuite service method."""
    return f"{RPC_BASE_URL}{method}"


GENERATE_CONTENT_URL = rpc_url("GenerateContent")
LIST_MODELS_URL = rpc_url("ListModels")
BENEFIT_TIER_URL = rpc_url("GetAiStudioBenefitTier")
COUNT_TOKENS_URL = rpc_url("CountTokens")

AI_STUDIO_HOST = "aistudio.google.com"
AI_STUDIO_ORIGIN = f"https://{AI_STUDIO_HOST}"
AI_STUDIO_HOME_URL = f"{AI_STUDIO_ORIGIN}/"
AI_STUDIO_NEW_CHAT_URL = f"{AI_STUDIO_ORIGIN}/prompts/new_chat"
AI_STUDIO_APP_NEW_CHAT_URL = f"{AI_STUDIO_ORIGIN}/app/prompts/new_chat"
AI_STUDIO_APPS_URL = f"{AI_STUDIO_ORIGIN}/apps"
GOOGLE_ACCOUNTS_HOST = "accounts.google.com"
GOOGLE_LOGIN_BOOTSTRAP_URL = (
    f"https://{GOOGLE_ACCOUNTS_HOST}/ServiceLogin?continue={AI_STUDIO_ORIGIN}"
)

GRPC_WEB_USER_AGENT = "grpc-web-javascript/0.1"
DEFAULT_CHAT_MODEL = "gemini-3.7-flash"
DEFAULT_APP_ID = "3b85b4ed-f945-422c-ba25-395d1feef528"

AI_STUDIO_URL = (
    f"{AI_STUDIO_NEW_CHAT_URL}?model={DEFAULT_CHAT_MODEL}&temporary=true"
)
AI_STUDIO_URL_FALLBACK = f"{AI_STUDIO_APP_NEW_CHAT_URL}?temporary=true"


def aistudio_new_chat_url(
    *,
    auth_user: str | None = None,
    model: str | None = None,
    app: bool = False,
    temporary: bool = True,
) -> str:
    """Build one AI Studio new-chat URL for an account index and surface."""
    base = (
        f"{AI_STUDIO_ORIGIN}/u/{auth_user}"
        if auth_user not in (None, "")
        else AI_STUDIO_ORIGIN
    )
    path = (
        f"{base}/app/prompts/new_chat" if app else f"{base}/prompts/new_chat"
    )
    params: list[str] = []
    if model and not app:
        params.append(f"model={model}")
    if temporary:
        params.append("temporary=true")
    return f"{path}?{'&'.join(params)}" if params else path


def aistudio_app_url(app_id: str, *, auth_user: str = "0") -> str:
    """Build the AI Studio apps page URL used for account probing."""
    return f"{AI_STUDIO_APPS_URL}/{app_id}?authuser={auth_user or '0'}"


__all__ = [
    "AI_STUDIO_APPS_URL",
    "AI_STUDIO_APP_NEW_CHAT_URL",
    "AI_STUDIO_HOME_URL",
    "AI_STUDIO_HOST",
    "AI_STUDIO_NEW_CHAT_URL",
    "AI_STUDIO_ORIGIN",
    "AI_STUDIO_URL",
    "AI_STUDIO_URL_FALLBACK",
    "BENEFIT_TIER_URL",
    "COUNT_TOKENS_URL",
    "DEFAULT_APP_ID",
    "DEFAULT_CHAT_MODEL",
    "EXT_BIN",
    "EXT_BIN_HEADER",
    "GENERATE_CONTENT_URL",
    "GOOGLE_ACCOUNTS_HOST",
    "GOOGLE_LOGIN_BOOTSTRAP_URL",
    "GRPC_WEB_USER_AGENT",
    "LIST_MODELS_URL",
    "RPC_BASE_URL",
    "RPC_HOST",
    "RPC_SERVICE_PATH",
    "WEB_API_KEY",
    "WEB_API_KEY_PREFIX",
    "WEB_API_KEY_RE",
    "aistudio_app_url",
    "aistudio_new_chat_url",
    "rpc_url",
]
