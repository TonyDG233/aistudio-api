"""User-visible instance contract for WP5 v4."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class InstanceState(StrEnum):
    STARTING = "starting"
    ACTIVE = "active"
    RECLAIMING = "reclaiming"
    INVALID = "invalid"


@dataclass(frozen=True)
class Instance:
    """Only fields the user API is allowed to expose."""

    id: str
    number: int
    account_id: str
    state: InstanceState
    channels: tuple[str, ...] = ()
    in_flight: int = 0
    latest: bool = False


@dataclass(frozen=True)
class InstanceList:
    max_instances: int
    latest_instance_id: str | None
    current_instance_id: str | None
    instances: tuple[Instance, ...]


__all__ = ["Instance", "InstanceList", "InstanceState"]
