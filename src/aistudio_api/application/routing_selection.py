"""Request/switch candidate ordering.

This module owns the preference order only. Eligibility (tier/tag/model/channel
admission) stays in routing_eligibility; switch-specific planning stays in
instance_switch. Keeping the ranking here prevents strategy-group config from
 owning request or account-switch order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aistudio_api.application.routing_eligibility import EligibleAccount


def rank_eligible(items: list[EligibleAccount]) -> list[EligibleAccount]:
    """Return candidates by native affinity, group weight, account weight, id."""
    return sorted(
        items,
        key=lambda item: (
            item.affinity,
            -item.group.weight,
            -item.account.routing.weight,
            item.account.account_id,
        ),
    )
