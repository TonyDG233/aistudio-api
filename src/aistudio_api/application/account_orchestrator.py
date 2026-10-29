"""Rotator event recording for the request routing pipeline.

The legacy ``ensure_active_account`` / ``try_switch_account`` global failover
path is intentionally gone (D05-040).  Requests never switch accounts here; the
routing pipeline only records outcomes for the instance/account that actually
ran the attempt.  Control-plane rebinds live in ``InstanceManager``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aistudio_api.application.account_rotator import AccountRotator


def record_rotator_event(
    event: str,
    *,
    account_id: str | None,
    model: str | None = None,
    surface: str | None = None,
    usage: dict[str, object] | None = None,
    rotator: AccountRotator | None = None,
) -> None:
    """Record one attempt outcome without mutating the active account."""
    if not account_id:
        return
    if rotator is None:
        from aistudio_api.api.state import runtime_state

        rotator = runtime_state.rotator
    if rotator is None:
        return

    if event == "success":
        rotator.record_success(
            account_id, model=model, usage=usage, surface=surface
        )
    elif event == "rate_limited":
        rotator.record_rate_limited(account_id, model=model, surface=surface)
    elif event == "auth_error":
        rotator.record_auth_error(account_id, model=model, surface=surface)
    elif event == "session_expired":
        rotator.record_session_expired(account_id, model=model, surface=surface)
    elif event == "error":
        rotator.record_error(account_id, model=model, surface=surface)


__all__ = ["record_rotator_event"]
