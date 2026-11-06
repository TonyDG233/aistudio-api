"""User-visible instance contract (one account + one Context + one Page)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class InstanceState(StrEnum):
    STARTING = "starting"
    ACTIVE = "active"
    SLEEPING = "sleeping"
    UNSTARTED = "unstarted"
    RECLAIMING = "reclaiming"
    RETIRING = "retiring"
    COOLDOWN = "cooldown"
    INVALID = "invalid"


@dataclass(frozen=True)
class Instance:
    """Only fields the user API is allowed to expose."""

    id: str
    number: int
    account_id: str
    state: InstanceState
    surface: str
    min_tier: str
    in_flight: int = 0
    latest: bool = False
    tier: str = ""
    last_used: float = 0.0
    only_tier: str = ""
    max_tier: str = ""


@dataclass(frozen=True)
class InstanceList:
    max_instances: int
    latest_instance_id: str | None
    current_instance_id: str | None
    instances: tuple[Instance, ...]


__all__ = ["Instance", "InstanceList", "InstanceState"]
