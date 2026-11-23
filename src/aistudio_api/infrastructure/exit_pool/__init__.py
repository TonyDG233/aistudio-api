"""External exit pool adapters (environment gated, off by default)."""

from __future__ import annotations

from aistudio_api.infrastructure.exit_pool.config import (
    DEFAULT_ALLOWED_HOSTS,
    DEFAULT_HTTP_TIMEOUT_S,
    DEFAULT_TTL_S,
    EXIT_DIRECT_FALLBACK_ENV,
    EXTERNAL_MODE,
    ExitPoolConfig,
    direct_fallback_enabled,
)
from aistudio_api.infrastructure.exit_pool.resolver import (
    SKIP_LOG_INTERVAL_S,
    ExitAssignment,
    ExitAssignmentResolver,
    ExitDecision,
    ExitResolver,
    get_exit_resolver,
    hmac_account_id,
    parse_socks_url,
    reset_direct_fallback_log,
    reset_exit_resolver,
    reset_skip_log,
    resolve_credential_exit,
)

__all__ = [
    "DEFAULT_ALLOWED_HOSTS",
    "DEFAULT_HTTP_TIMEOUT_S",
    "DEFAULT_TTL_S",
    "EXIT_DIRECT_FALLBACK_ENV",
    "EXTERNAL_MODE",
    "SKIP_LOG_INTERVAL_S",
    "ExitAssignment",
    "ExitAssignmentResolver",
    "ExitDecision",
    "ExitPoolConfig",
    "ExitResolver",
    "direct_fallback_enabled",
    "get_exit_resolver",
    "hmac_account_id",
    "parse_socks_url",
    "reset_direct_fallback_log",
    "reset_exit_resolver",
    "reset_skip_log",
    "resolve_credential_exit",
]
