"""Idle-slot rebalancing for WP5 v3.

Request routing and instance switching share the eligibility graph but use
different orders. This planner only touches idle slots; in-flight slots are
never rebound. It assigns the most constrained slot first so scarce accounts
(for example a pro-only chat pool) are reserved for the slots that need them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from aistudio_api.application.channel_registry import build_registry
from aistudio_api.application.routing_eligibility import (
    AccountView,
    EligibleAccount,
    eligible_accounts,
)
from aistudio_api.infrastructure.gateway.instance_pool import InstancePool, InstanceSlot
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("instance-switch")


@dataclass(frozen=True)
class SlotView:
    slot_id: str
    channel: str
    group_name: str
    current_account_id: str
    in_flight: int


@dataclass(frozen=True)
class RebalanceMove:
    slot_id: str
    channel: str
    group_name: str
    from_account_id: str
    to_account_id: str
    reason: str = "rebalance"


def slot_view(slot: InstanceSlot) -> SlotView | None:
    channel = slot.channel_role
    if not channel:
        if slot.build_ready() and not slot.chat_ready():
            channel = "build"
        elif slot.chat_ready():
            channel = "chat"
    if not channel:
        return None
    return SlotView(
        slot_id=slot.slot_id,
        channel=channel,
        group_name=slot.group_name,
        current_account_id=slot.current_account_id,
        in_flight=slot.in_flight,
    )


def plan_rebalance(
    slots: Sequence[SlotView],
    accounts: Sequence[AccountView],
    features: dict[str, object],
    *,
    model: str = "models/flash",
) -> list[RebalanceMove]:
    """Plan account assignments for idle slots, most constrained slot first."""
    registry = build_registry(features)
    groups = features.get("groups") or []
    pending = [slot for slot in slots if slot.in_flight == 0 and slot.channel]
    candidates_by_slot: dict[str, list[EligibleAccount]] = {}
    for slot in pending:
        candidates_by_slot[slot.slot_id] = eligible_accounts(
            accounts,
            slot.channel,
            model,
            features=features,
            registry=registry,
            groups=groups,
        )

    assigned: set[str] = set()
    moves: list[RebalanceMove] = []
    for slot in sorted(
        pending,
        key=lambda item: (len(candidates_by_slot[item.slot_id]), item.slot_id),
    ):
        candidates = candidates_by_slot[slot.slot_id]
        if not candidates:
            continue
        candidate_ids = {candidate.account_id for candidate in candidates}
        if (
            slot.current_account_id in candidate_ids
            and slot.current_account_id not in assigned
        ):
            assigned.add(slot.current_account_id)
            continue

        choice = next(
            (
                candidate
                for candidate in candidates
                if candidate.account_id not in assigned
            ),
            None,
        )
        if choice is None:
            continue
        assigned.add(choice.account_id)
        moves.append(
            RebalanceMove(
                slot_id=slot.slot_id,
                channel=slot.channel,
                group_name=choice.group.name or choice.group.source,
                from_account_id=slot.current_account_id,
                to_account_id=choice.account_id,
            )
        )
    return moves


class InstanceSwitcher:
    """Apply planner moves to the request pool; never touches in-flight slots."""

    def __init__(
        self,
        pool: InstancePool,
        account_store,
        account_service,
        *,
        features_provider: Callable[[], dict[str, object]],
    ) -> None:
        self._pool = pool
        self._store = account_store
        self._account_service = account_service
        self._features_provider = features_provider

    def _features(self) -> dict[str, object]:
        try:
            return dict(self._features_provider())
        except Exception:
            return {}

    def _auth_for(self, account_id: str) -> tuple[str | None, str | None]:
        try:
            path = self._account_service.get_account_auth_path(account_id)
            account = self._account_service.get_account(account_id)
            return (
                str(path) if path is not None else None,
                getattr(account, "auth_user", None),
            )
        except Exception:
            return None, None

    async def rebalance(self) -> dict[str, object]:
        features = self._features()
        accounts = [
            AccountView.from_account(account) for account in self._store.list_accounts()
        ]
        slots = [
            view for slot in self._pool.slots() if (view := slot_view(slot)) is not None
        ]
        moves = plan_rebalance(slots, accounts, features)
        applied: list[dict[str, object]] = []
        for move in moves:
            auth_file, auth_user = self._auth_for(move.to_account_id)
            try:
                await self._pool.rebind(
                    move.slot_id,
                    account_id=move.to_account_id,
                    auth_file=auth_file,
                    auth_user=auth_user,
                    channel=move.channel,
                    group_name=move.group_name,
                )
            except Exception as exc:
                log.warning(
                    "[switch] rebind failed slot=%s account=%s: %s",
                    move.slot_id,
                    move.to_account_id,
                    exc,
                )
                continue
            applied.append(
                {
                    "slot_id": move.slot_id,
                    "from": move.from_account_id,
                    "to": move.to_account_id,
                    "channel": move.channel,
                }
            )
        return {
            "ok": True,
            "reason": "rebalanced" if applied else "no_change",
            "moves": applied,
            "idle_slots": len(slots),
        }


__all__ = [
    "InstanceSwitcher",
    "RebalanceMove",
    "SlotView",
    "plan_rebalance",
    "slot_view",
]
