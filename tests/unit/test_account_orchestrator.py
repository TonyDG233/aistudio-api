"""P2 control-plane vs request-instance separation tests.

The old request-time global failover (``ensure_active_account`` /
``try_switch_account``) is removed.  This module now only records outcomes for
the account that actually ran the attempt; control-plane rebinds are covered by
``test_instance_manager.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from aistudio_api.application import account_orchestrator
from aistudio_api.application.account_orchestrator import record_rotator_event
from aistudio_api.application.account_rotator import AccountRotator


def test_request_time_global_failover_is_gone() -> None:
    assert not hasattr(account_orchestrator, "ensure_active_account")
    assert not hasattr(account_orchestrator, "try_switch_account")
    assert not hasattr(account_orchestrator, "MAX_RETRIES")


def test_record_rotator_event_uses_explicit_account() -> None:
    rotator = MagicMock(spec=AccountRotator)
    record_rotator_event(
        "success",
        account_id="acc-2",
        model="gemini-3.7-flash",
        surface="chat",
        usage={"prompt_tokens": 1},
        rotator=rotator,
    )
    rotator.record_success.assert_called_once_with(
        "acc-2",
        model="gemini-3.7-flash",
        usage={"prompt_tokens": 1},
        surface="chat",
    )


def test_record_rotator_event_maps_all_outcomes() -> None:
    rotator = MagicMock(spec=AccountRotator)
    record_rotator_event(
        "rate_limited", account_id="acc-1", model="m", surface="chat", rotator=rotator
    )
    record_rotator_event(
        "auth_error", account_id="acc-1", model="m", surface="chat", rotator=rotator
    )
    record_rotator_event(
        "session_expired",
        account_id="acc-1",
        model="m",
        surface="chat",
        rotator=rotator,
    )
    record_rotator_event(
        "error", account_id="acc-1", model="m", surface="chat", rotator=rotator
    )
    rotator.record_rate_limited.assert_called_once_with(
        "acc-1", model="m", surface="chat"
    )
    rotator.record_auth_error.assert_called_once_with(
        "acc-1", model="m", surface="chat"
    )
    rotator.record_session_expired.assert_called_once_with(
        "acc-1", model="m", surface="chat"
    )
    rotator.record_error.assert_called_once_with(
        "acc-1", model="m", surface="chat"
    )


def test_record_rotator_event_ignores_missing_account() -> None:
    rotator = MagicMock(spec=AccountRotator)
    record_rotator_event("success", account_id=None, rotator=rotator)
    rotator.record_success.assert_not_called()
