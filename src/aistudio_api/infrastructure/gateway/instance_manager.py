"""Instance landing and four-layer budgets (WP5 phase 2, level A/B seam).

Level A keeps the existing single-browser behavior: chat and build pages share
the active account's cookie jar, and switching accounts is delegated to
``application.account_orchestrator.switch_to_account``. Level B (Realm pool)
will implement the same ``land``/``snapshot`` interface with per-account
BrowserContexts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from aistudio_api.infrastructure.utils.logger import get_logger

if TYPE_CHECKING:
    from aistudio_api.application.surface_orchestrator import GroupPolicy
    from aistudio_api.infrastructure.gateway.client import AIStudioClient

logger = get_logger("application.instances")

_CHANNEL_TO_SURFACE = {"chat": "chat", "build": "build"}


@dataclass(frozen=True)
class InstanceLease:
    """One landed instance for a request attempt."""

    account_id: str
    channel: str
    surface: str
    realm_id: str = "default"
    slot_index: int | None = None
    is_borrow: bool = False
    adapter: object | None = None


@dataclass(frozen=True)
class BudgetLimits:
    global_max: int
    channel_max: int
    group_max: int
    realm_max: int


@dataclass(frozen=True)
class BudgetUsage:
    global_live: int
    channel_live: int
    group_live: int
    realm_live: int


def evaluate_budget(
    limits: BudgetLimits,
    usage: BudgetUsage,
    *,
    need_new: bool,
    need_realm: bool,
) -> str | None:
    """Return the first exhausted budget name, or ``None`` when landing is allowed."""
    if not need_new:
        return None
    if usage.global_live >= limits.global_max:
        return "global"
    if usage.channel_live >= limits.channel_max:
        return "channel"
    if usage.group_live >= limits.group_max:
        return "group"
    if need_realm and usage.realm_live >= limits.realm_max:
        return "realm"
    return None


class InstanceManager:
    """Land requests on chat/build instances and enforce budgets."""

    def __init__(
        self,
        client: AIStudioClient,
        *,
        features_provider=None,
        account_store=None,
    ) -> None:
        self._client = client
        self._features_provider = features_provider
        self._account_store = account_store
        self._realm_pool_obj = None

    def _features(self) -> dict[str, object]:
        if self._features_provider is not None:
            try:
                return dict(self._features_provider())
            except Exception:
                return {}
        try:
            from aistudio_api.infrastructure.gateway.model_defaults import (
                get_features_config,
            )

            return dict(get_features_config())
        except Exception:
            return {}

    def _store(self):
        if self._account_store is not None:
            return self._account_store
        from aistudio_api.infrastructure.account.account_store import AccountStore

        return AccountStore()

    def _realm_mode(self) -> bool:
        raw = self._features().get("routing")
        routing = raw if isinstance(raw, dict) else {}
        return bool(routing.get("realm_mode", True))

    def _realm_pool(self):
        if self._realm_pool_obj is None:
            from aistudio_api.infrastructure.gateway.realm_pool import RealmPool

            self._realm_pool_obj = RealmPool(
                lambda: self._client.session.ensure_browser_cdp_client(),
                features_provider=self._features,
            )
        return self._realm_pool_obj

    def _auth_for(self, account_id: str) -> tuple[str | None, str | None]:
        try:
            from aistudio_api.api.state import runtime_state

            service = runtime_state.account_service
            if service is not None:
                path = service.get_account_auth_path(account_id)
                account = service.get_account(account_id)
                return (
                    str(path) if path is not None else None,
                    getattr(account, "auth_user", None),
                )
        except Exception:
            pass
        return None, None

    def _page_alive(self, channel: str) -> bool:
        if channel == "build":
            check = getattr(self._client, "_build_surface_page_alive", None)
            if callable(check):
                try:
                    return bool(check())
                except Exception:
                    return False
            return False
        session = getattr(self._client, "session", None)
        ctx = getattr(session, "active_context", None)
        page = getattr(ctx, "page", None)
        if page is None:
            return False
        try:
            return not page.is_closed()
        except Exception:
            return False

    def mounted_channels(self) -> tuple[str, ...]:
        """Channels that currently hold a live page."""
        return tuple(
            channel for channel in ("chat", "build") if self._page_alive(channel)
        )

    def _realm_page_counts(self) -> dict[str, int]:
        if self._realm_pool_obj is None:
            return {"chat": 0, "build": 0}
        return self._realm_pool_obj.page_counts()

    def live_page_count(self) -> int:
        default = sum(1 for channel in ("chat", "build") if self._page_alive(channel))
        realm = self._realm_page_counts()
        return default + realm.get("chat", 0) + realm.get("build", 0)

    @property
    def in_flight_count(self) -> int:
        try:
            return int(self._client.in_flight_count)
        except Exception:
            return 0

    def _channel_limit(self, features: dict[str, object], channel: str) -> int:
        raw_channels = features.get("channels")
        channels = raw_channels if isinstance(raw_channels, dict) else {}
        raw = channels.get(channel)
        spec = raw if isinstance(raw, dict) else {}
        try:
            return max(0, int(spec.get("max_instances", 2)))
        except (TypeError, ValueError):
            return 2

    def _global_limit(self, features: dict[str, object]) -> int:
        raw = features.get("routing")
        routing = raw if isinstance(raw, dict) else {}
        try:
            return max(1, int(routing.get("global_max_instances", 2)))
        except (TypeError, ValueError):
            return 2

    def _realm_limit(self, features: dict[str, object]) -> int:
        raw = features.get("routing")
        routing = raw if isinstance(raw, dict) else {}
        if not bool(routing.get("realm_mode", True)):
            return 1
        try:
            return max(1, int(routing.get("max_realms", 2)))
        except (TypeError, ValueError):
            return 2

    def _group_live(self, group: GroupPolicy | None) -> int:
        if group is None or group.source == "implicit-global":
            return self.live_page_count()
        try:
            account = self._store().get_active_account()
        except Exception:
            account = None
        if account is None:
            return 0
        from aistudio_api.application.surface_orchestrator import (
            account_matches_group,
        )

        features = self._features()
        groups = features.get("groups") or []
        if account_matches_group(group, account.tier, account.email, groups):
            return self.live_page_count()
        return 0

    def _usage(self, channel: str, group: GroupPolicy | None) -> BudgetUsage:
        realm = self._realm_page_counts()
        default_channel = 1 if self._page_alive(channel) else 0
        return BudgetUsage(
            global_live=self.live_page_count(),
            channel_live=default_channel + realm.get(channel, 0),
            group_live=self._group_live(group),
            realm_live=(
                len(self._realm_pool_obj.snapshot())
                if self._realm_pool_obj is not None
                else (1 if self.live_page_count() > 0 else 0)
            ),
        )

    def limits_for(self, group: GroupPolicy | None) -> BudgetLimits:
        features = self._features()
        return BudgetLimits(
            global_max=self._global_limit(features),
            channel_max=self._channel_limit(features, "chat")
            if group is None
            else max(
                self._channel_limit(features, "chat"),
                self._channel_limit(features, "build"),
            ),
            group_max=group.max_page_instances if group is not None else 2,
            realm_max=self._realm_limit(features),
        )

    def budget_limits(self, channel: str, group: GroupPolicy | None) -> BudgetLimits:
        features = self._features()
        return BudgetLimits(
            global_max=self._global_limit(features),
            channel_max=self._channel_limit(features, channel),
            group_max=group.max_page_instances if group is not None else 2,
            realm_max=self._realm_limit(features),
        )

    def can_land(
        self, channel: str, group: GroupPolicy | None
    ) -> tuple[bool, str | None]:
        """Return ``(allowed, reason)`` for a channel landing."""
        need_new = not self._page_alive(channel)
        need_realm = False
        usage = self._usage(channel, group)
        reason = evaluate_budget(
            self.budget_limits(channel, group),
            usage,
            need_new=need_new,
            need_realm=need_realm,
        )
        if reason is not None:
            logger.info(
                "[surface:budget] channel=%s need_new=%s reason=%s usage=%s",
                channel,
                need_new,
                reason,
                usage,
            )
            return False, reason
        return True, None

    async def land(
        self,
        *,
        account_id: str,
        channel: str,
        surface: str,
        model: str,
        require_idle: bool = True,
    ) -> InstanceLease | None:
        """Activate the candidate account and return an instance lease."""
        if self._realm_mode():
            from aistudio_api.infrastructure.gateway.realm_pool import (
                RealmCapacityError,
            )

            auth_file, auth_user = self._auth_for(account_id)
            try:
                realm = await self._realm_pool().acquire(
                    account_id=account_id,
                    auth_file=auth_file,
                    auth_user=auth_user,
                )
            except RealmCapacityError as exc:
                logger.info(
                    "[surface:budget] realm capacity exhausted account=%s: %s",
                    account_id,
                    exc,
                )
                return None
            return InstanceLease(
                account_id=account_id,
                channel=channel,
                surface=surface,
                realm_id=realm.realm_id,
                adapter=realm,
            )

        from aistudio_api.application.account_orchestrator import switch_to_account

        try:
            active = self._store().get_active_account()
        except Exception:
            active = None
        if active is None or active.id != account_id:
            switched = await switch_to_account(
                account_id,
                model=model,
                surface=surface,
                require_idle=require_idle,
            )
            if not switched:
                logger.info(
                    "[surface:switch] skip account=%s channel=%s reason=busy_or_unavailable",
                    account_id,
                    channel,
                )
                return None
        return InstanceLease(
            account_id=account_id,
            channel=channel,
            surface=surface,
        )

    def slot_indexes(self) -> dict[tuple[str, str], int]:
        """Map enabled slot rows to their stable index."""
        features = self._features()
        raw_slots = features.get("slots")
        slots = raw_slots if isinstance(raw_slots, list) else []
        indexes: dict[tuple[str, str], int] = {}
        for index, slot in enumerate(slots):
            if not isinstance(slot, dict):
                continue
            if not bool(slot.get("enabled", True)):
                continue
            account_id = str(slot.get("account_id") or "")
            channel = str(slot.get("channel") or "")
            if account_id and channel in _CHANNEL_TO_SURFACE:
                indexes.setdefault((account_id, channel), index)
        return indexes

    def snapshot(self) -> dict[str, object]:
        """Read-only instance/budget snapshot for the management API."""
        features = self._features()
        page_count = self.live_page_count()
        raw_routing = features.get("routing")
        routing_cfg = raw_routing if isinstance(raw_routing, dict) else {}
        realm_mode = bool(routing_cfg.get("realm_mode", True))
        return {
            "live_instances": page_count,
            "chat_page_alive": self._page_alive("chat"),
            "build_page_alive": self._page_alive("build"),
            "realm_mode": realm_mode,
            "live_realms": 1 if page_count else 0,
            "in_flight": self.in_flight_count,
            "limits": {
                "global_max": self._global_limit(features),
                "chat_max": self._channel_limit(features, "chat"),
                "build_max": self._channel_limit(features, "build"),
                "max_realms": self._realm_limit(features),
            },
            "planned_instances": len(self.slot_indexes()),
            "realms": (
                self._realm_pool_obj.snapshot()
                if self._realm_pool_obj is not None
                else []
            ),
            "slots": {
                f"{account_id}:{channel}": index
                for (account_id, channel), index in self.slot_indexes().items()
            },
        }


__all__ = [
    "BudgetLimits",
    "BudgetUsage",
    "InstanceLease",
    "InstanceManager",
    "evaluate_budget",
]
