"""External exit pool adapters (environment gated, off by default)."""

from __future__ import annotations

from aistudio_api.infrastructure.exit_pool.config import (
    DEFAULT_ALLOWED_HOSTS,
    DEFAULT_HTTP_TIMEOUT_S,
    DEFAULT_TTL_S,
    EXTERNAL_MODE,
    ExitPoolConfig,
)
from aistudio_api.infrastructure.exit_pool.resolver import (
    ExitAssignment,
    ExitResolver,
    get_exit_resolver,
    hmac_account_id,
    parse_socks_url,
    reset_exit_resolver,
)

__all__ = [
    "DEFAULT_ALLOWED_HOSTS",
    "DEFAULT_HTTP_TIMEOUT_S",
    "DEFAULT_TTL_S",
    "EXTERNAL_MODE",
    "ExitAssignment",
    "ExitPoolConfig",
    "ExitResolver",
    "get_exit_resolver",
    "hmac_account_id",
    "parse_socks_url",
    "reset_exit_resolver",
]
