"""Environment-gated configuration for the external exit pool.

The special mode is only ever enabled through environment variables; there is
no YAML key and no UI.  Any missing or malformed value keeps the whole feature
off so the default distribution behaves exactly as before.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("exit-pool")

EXIT_MODE_ENV = "AISTUDIO_EXIT_MODE"
EXIT_ENDPOINT_ENV = "AISTUDIO_EXIT_ENDPOINT"
EXIT_TOKEN_ENV = "AISTUDIO_EXIT_TOKEN"
EXIT_HMAC_KEY_ENV = "AISTUDIO_EXIT_HMAC_KEY"
EXIT_ALLOWED_HOSTS_ENV = "AISTUDIO_EXIT_ALLOWED_HOSTS"
EXIT_HTTP_TIMEOUT_ENV = "AISTUDIO_EXIT_HTTP_TIMEOUT"
EXIT_TTL_ENV = "AISTUDIO_EXIT_TTL"
EXIT_CANARY_ACCOUNT_ENV = "AISTUDIO_EXIT_CANARY_ACCOUNT"

EXTERNAL_MODE = "external"

# The manager normally runs on the docker bridge (or loopback for a host
# process); anything else must be explicitly whitelisted by the operator.
DEFAULT_ALLOWED_HOSTS: tuple[str, ...] = ("127.0.0.1", "172.17.0.1")

DEFAULT_HTTP_TIMEOUT_S = 2.0
DEFAULT_TTL_S = 300.0


def _env_value(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _float_env(name: str, default: float) -> float:
    raw = _env_value(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        log.warning("[exit-pool] 忽略非法 %s=%r，使用默认值 %s", name, raw, default)
        return default
    if value <= 0.0:
        log.warning("[exit-pool] 忽略非正数 %s=%r，使用默认值 %s", name, raw, default)
        return default
    return value


def _allowed_hosts() -> frozenset[str]:
    raw = _env_value(EXIT_ALLOWED_HOSTS_ENV)
    if not raw:
        return frozenset(DEFAULT_ALLOWED_HOSTS)
    hosts = frozenset(part.strip().lower() for part in raw.split(",") if part.strip())
    return hosts or frozenset(DEFAULT_ALLOWED_HOSTS)


def _normalize_endpoint(raw: str) -> str | None:
    """Return a trimmed ``http(s)://host`` endpoint, or ``None`` when malformed.

    The endpoint is validated at config load so an unusable value disables the
    whole feature instead of failing (and logging) on every assignment attempt.
    """
    value = raw.strip().rstrip("/")
    if not value:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    if not parts.hostname:
        return None
    return value


@dataclass(frozen=True)
class ExitPoolConfig:
    """Validated runtime configuration for the external exit pool."""

    endpoint: str
    token: str
    hmac_key: str
    allowed_hosts: frozenset[str] = frozenset(DEFAULT_ALLOWED_HOSTS)
    http_timeout_s: float = DEFAULT_HTTP_TIMEOUT_S
    ttl_s: float = DEFAULT_TTL_S
    canary_account: str = ""

    @classmethod
    def from_env(cls) -> ExitPoolConfig | None:
        """Build the config from env, or return ``None`` when the mode is off.

        ``AISTUDIO_EXIT_MODE`` absent/empty means off (no warning).  Any other
        value than ``external`` is treated as off with a warning so a typo can
        never silently enable an unexpected mode.
        """
        mode = _env_value(EXIT_MODE_ENV)
        if not mode:
            return None
        if mode.lower() != EXTERNAL_MODE:
            log.warning(
                "[exit-pool] 未知 %s=%r（仅支持 external），出口池保持关闭",
                EXIT_MODE_ENV,
                mode,
            )
            return None

        raw_endpoint = _env_value(EXIT_ENDPOINT_ENV)
        endpoint = _normalize_endpoint(raw_endpoint)
        token = _env_value(EXIT_TOKEN_ENV)
        hmac_key = _env_value(EXIT_HMAC_KEY_ENV)
        missing = [
            name
            for name, value in (
                (EXIT_ENDPOINT_ENV, raw_endpoint),
                (EXIT_TOKEN_ENV, token),
                (EXIT_HMAC_KEY_ENV, hmac_key),
            )
            if not value
        ]
        if missing:
            log.warning(
                "[exit-pool] %s=external 但缺少 %s，出口池保持关闭",
                EXIT_MODE_ENV,
                ", ".join(missing),
            )
            return None
        if endpoint is None:
            log.warning(
                "[exit-pool] 非法 %s=%r（仅支持 http/https 且主机非空），"
                "出口池保持关闭",
                EXIT_ENDPOINT_ENV,
                raw_endpoint,
            )
            return None

        return cls(
            endpoint=endpoint,
            token=token,
            hmac_key=hmac_key,
            allowed_hosts=_allowed_hosts(),
            http_timeout_s=_float_env(EXIT_HTTP_TIMEOUT_ENV, DEFAULT_HTTP_TIMEOUT_S),
            ttl_s=_float_env(EXIT_TTL_ENV, DEFAULT_TTL_S),
            canary_account=_env_value(EXIT_CANARY_ACCOUNT_ENV),
        )


__all__ = [
    "DEFAULT_ALLOWED_HOSTS",
    "DEFAULT_HTTP_TIMEOUT_S",
    "DEFAULT_TTL_S",
    "EXTERNAL_MODE",
    "ExitPoolConfig",
]
