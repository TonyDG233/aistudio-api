#!/usr/bin/env python3
"""Safely clear false ``verify_required`` flags from an account registry.

The external-exit incident marked five healthy accounts as ``verify_required``
because a maintenance probe fell back to a bare host-egress request, got a
region 403, and a canary on another exit then reported ``account_bad``.  This
tool clears only the account ids the operator explicitly passes; it never
filters by fingerprint on its own, so the three genuinely locked accounts are
untouched unless their ids are listed.

Safety contract:

* default is dry-run; ``--apply`` (with ``--yes``) is required to write anything;
* a timestamped backup of ``registry.json``, the affected ``meta.json`` files
  and ``rotator_state.json`` is taken before the first write;
* the backup directory and the store directory come from the same resolved
  path, so a restore can never target a different accounts dir;
* an unknown id is rejected before any write;
* targets without a ``no_exit`` fingerprint (e.g. ``last_refresh_error=null``,
  the genuine verify locks) are refused unless ``--force`` is passed;
* on any write error the backup is restored (best-effort all-or-nothing);
* email output is masked; no cookie/token is ever read or printed.

Operational note: the running service keeps the registry and rotator state in
memory.  Stop it before ``--apply`` (or restart it afterwards) so the cleared
flags are actually reloaded.

Usage::

    scripts/clear_verify_required.sh --list
    scripts/clear_verify_required.sh --ids acc_a,acc_b
    scripts/clear_verify_required.sh --ids acc_a,acc_b --apply --yes
    scripts/clear_verify_required.sh --ids acc_a --apply --yes --force
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.config import resolve_rotator_state_file, settings
from aistudio_api.infrastructure.account.account_store import (
    AccountMeta,
    AccountStore,
)
from aistudio_api.infrastructure.utils.common import mask_email


class ClearVerifyError(RuntimeError):
    """A validation or persistence failure that must abort the run."""


@dataclass
class ClearPlan:
    """Resolved targets and their before-state (no writes performed)."""

    targets: list[str]
    untouched: list[str]
    unknown: list[str]
    before: dict[str, dict[str, object]]
    force_required: list[str] = field(default_factory=list)

    @property
    def has_unknown(self) -> bool:
        return bool(self.unknown)


def _has_no_exit_fingerprint(account: AccountMeta) -> bool:
    """True when the account was flagged by the no-exit probe path.

    The five false flags carry ``last_refresh_error=skipped_no_exit: no_exit``.
    The three genuinely locked accounts have ``last_refresh_error=null`` (or an
    unrelated error), so clearing them blindly would mask a real verify lock.
    """
    error = str(account.last_refresh_error or "")
    return "no_exit" in error


def _account_dir(accounts_dir: Path, account_id: str) -> Path:
    return accounts_dir / account_id


def list_verify_required(store: AccountStore) -> list[AccountMeta]:
    """Accounts currently carrying ``verify_required=true`` (store order)."""
    return [account for account in store.list_accounts() if account.verify_required]


def plan_clear(store: AccountStore, ids: list[str]) -> ClearPlan:
    """Validate the requested ids and snapshot their current state."""
    requested = list(
        dict.fromkeys(str(item).strip() for item in ids if str(item).strip())
    )
    by_id = {account.id: account for account in store.list_accounts()}
    unknown = [account_id for account_id in requested if account_id not in by_id]
    targets = [
        account_id
        for account_id in requested
        if account_id in by_id and by_id[account_id].verify_required
    ]
    untouched = [
        account_id
        for account_id in requested
        if account_id in by_id and not by_id[account_id].verify_required
    ]
    before = {
        account_id: {
            "verify_required": by_id[account_id].verify_required,
            "expired": by_id[account_id].expired,
            "email_masked": mask_email(by_id[account_id].email) or "-",
            "last_refresh_error": by_id[account_id].last_refresh_error,
        }
        for account_id in targets
    }
    force_required = [
        account_id
        for account_id in targets
        if not _has_no_exit_fingerprint(by_id[account_id])
    ]
    return ClearPlan(
        targets=targets,
        untouched=untouched,
        unknown=unknown,
        before=before,
        force_required=force_required,
    )


def backup_state(
    *,
    accounts_dir: Path,
    rotator_state_file: Path,
    account_ids: list[str],
    backup_root: Path | None = None,
) -> Path:
    """Copy registry/metas/rotator state into a timestamped backup directory."""
    root = backup_root or (accounts_dir / "backups")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = root / f"clear-verify-{stamp}"
    suffix = 1
    while target.exists():
        suffix += 1
        target = root / f"clear-verify-{stamp}-{suffix}"
    target.mkdir(parents=True, exist_ok=False)

    registry = accounts_dir / "registry.json"
    if not registry.is_file():
        raise ClearVerifyError(f"registry not found: {registry}")
    shutil.copy2(registry, target / "registry.json")

    meta_dir = target / "meta"
    meta_dir.mkdir()
    for account_id in account_ids:
        meta = _account_dir(accounts_dir, account_id) / "meta.json"
        if meta.is_file():
            shutil.copy2(meta, meta_dir / f"{account_id}.json")

    if rotator_state_file.is_file():
        shutil.copy2(rotator_state_file, target / "rotator_state.json")
    return target


def _restore(
    *,
    accounts_dir: Path,
    rotator_state_file: Path,
    backup_dir: Path,
) -> None:
    registry_backup = backup_dir / "registry.json"
    if registry_backup.is_file():
        shutil.copy2(registry_backup, accounts_dir / "registry.json")
    meta_backup = backup_dir / "meta"
    if meta_backup.is_dir():
        for source in meta_backup.glob("*.json"):
            target = _account_dir(accounts_dir, source.stem) / "meta.json"
            if target.parent.is_dir():
                shutil.copy2(source, target)
    rotator_backup = backup_dir / "rotator_state.json"
    if rotator_backup.is_file():
        rotator_state_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rotator_backup, rotator_state_file)


def apply_clear(
    store: AccountStore,
    *,
    plan: ClearPlan,
    accounts_dir: Path,
    rotator_state_file: Path,
    backup_root: Path | None = None,
    force: bool = False,
) -> dict[str, object]:
    """Persist ``verify_required=false`` for ``plan.targets`` atomically."""
    if plan.unknown:
        raise ClearVerifyError(f"unknown account ids: {', '.join(plan.unknown)}")
    if not plan.targets:
        return {"changed": [], "backup": None}
    if plan.force_required and not force:
        raise ClearVerifyError(
            "refusing to clear accounts without a no_exit fingerprint "
            f"(last_refresh_error missing 'no_exit'): "
            f"{', '.join(plan.force_required)}; re-run with --force only after "
            "confirming these are false flags"
        )
    if not settings.persist_rotator:
        raise ClearVerifyError(
            "settings.persist_rotator is false; rotator state would not persist"
        )
    # Make the rotator honour an explicit state-file override before it loads.
    previous_state_env = os.environ.get("AISTUDIO_ROTATOR_STATE_FILE")
    os.environ["AISTUDIO_ROTATOR_STATE_FILE"] = str(rotator_state_file)
    # Snapshot before constructing the rotator: its init may reconcile/save
    # state, and a failure mid-apply must restore the original files.
    backup_dir = backup_state(
        accounts_dir=accounts_dir,
        rotator_state_file=rotator_state_file,
        account_ids=plan.targets,
        backup_root=backup_root,
    )
    try:
        rotator = AccountRotator(store)
        for account_id in plan.targets:
            changed = store.update_account_refresh(account_id, verify_required=False)
            if not changed:
                raise ClearVerifyError(
                    f"account {account_id} disappeared while clearing"
                )
        for account_id in plan.targets:
            rotator.record_verify_cleared(account_id)
    except Exception:
        _restore(
            accounts_dir=accounts_dir,
            rotator_state_file=rotator_state_file,
            backup_dir=backup_dir,
        )
        raise
    finally:
        if previous_state_env is None:
            os.environ.pop("AISTUDIO_ROTATOR_STATE_FILE", None)
        else:
            os.environ["AISTUDIO_ROTATOR_STATE_FILE"] = previous_state_env
    return {"changed": list(plan.targets), "backup": str(backup_dir)}


def _print_plan(plan: ClearPlan, *, as_json: bool) -> None:
    if as_json:
        import json

        print(
            json.dumps(
                {
                    "targets": plan.targets,
                    "untouched": plan.untouched,
                    "unknown": plan.unknown,
                    "before": plan.before,
                    "force_required": plan.force_required,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if not plan.targets and not plan.untouched and not plan.unknown:
        print("verify_required=true accounts: none")
        return
    for account_id in plan.targets:
        before = plan.before[account_id]
        print(
            f"{account_id} email={before['email_masked']} "
            f"verify_required={before['verify_required']} "
            f"expired={before['expired']} "
            f"last_refresh_error={before['last_refresh_error']}"
        )
    for account_id in plan.untouched:
        print(f"{account_id} (not verify_required; nothing to clear)")
    for account_id in plan.unknown:
        print(f"{account_id} (unknown id)")
    if plan.force_required:
        print(
            "WARNING: targets without a no_exit fingerprint require --force: "
            + ", ".join(plan.force_required)
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clear_verify_required",
        description=("List or clear false verify_required flags (dry-run by default)."),
        epilog=(
            "Stop the service before --apply: it caches registry/rotator state "
            "in memory and must reload the cleared files."
        ),
    )
    parser.add_argument(
        "--accounts-dir",
        type=Path,
        default=None,
        help="accounts directory (default: AISTUDIO_ACCOUNTS_DIR / data/accounts)",
    )
    parser.add_argument(
        "--rotator-state-file",
        type=Path,
        default=None,
        help="rotator state path (default: AISTUDIO_ROTATOR_STATE_FILE)",
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=None,
        help="backup parent directory (default: <accounts-dir>/backups)",
    )
    parser.add_argument(
        "--ids",
        default="",
        help="comma-separated account ids to clear (required with --apply)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list accounts currently marked verify_required",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="persist the clear (default is dry-run)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="required with --apply: confirm the service is stopped",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "allow clearing targets without a no_exit fingerprint "
            "(e.g. last_refresh_error=null); confirm they are false flags first"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the plan/result as JSON",
    )
    return parser


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    accounts_dir = Path(args.accounts_dir).resolve() if args.accounts_dir else None
    store = AccountStore(accounts_dir)
    # Reuse the store's own resolution so backup/restore and the registry it
    # rewrites can never land in two different directories.
    resolved_accounts_dir = store.accounts_dir
    rotator_state_file = (
        Path(args.rotator_state_file).resolve()
        if args.rotator_state_file
        else resolve_rotator_state_file()
    )

    ids = [item for item in str(args.ids).split(",") if item.strip()]
    if args.list and (args.apply or ids):
        parser.error("--list cannot be combined with --ids/--apply")
    if not args.list and not ids:
        parser.error("--ids is required unless --list is given")

    if args.list:
        flagged = list_verify_required(store)
        plan = ClearPlan(
            targets=[account.id for account in flagged],
            untouched=[],
            unknown=[],
            before={
                account.id: {
                    "verify_required": account.verify_required,
                    "expired": account.expired,
                    "email_masked": mask_email(account.email) or "-",
                    "last_refresh_error": account.last_refresh_error,
                }
                for account in flagged
            },
        )
        _print_plan(plan, as_json=args.json)
        return 0

    plan = plan_clear(store, ids)
    _print_plan(plan, as_json=args.json)
    if plan.unknown:
        print(
            f"error: unknown account ids, no writes: {', '.join(plan.unknown)}",
            file=sys.stderr,
        )
        return 2
    if not args.apply:
        print("dry-run: pass --apply --yes to persist the clear")
        return 0
    if not args.yes:
        print(
            "error: --apply requires --yes after stopping the service "
            "(the running process caches registry/rotator state)",
            file=sys.stderr,
        )
        return 2
    if plan.force_required and not args.force:
        print(
            "error: refusing to clear accounts without a no_exit fingerprint "
            f"(use --force): {', '.join(plan.force_required)}",
            file=sys.stderr,
        )
        return 2
    print(
        "NOTE: the service must be stopped before --apply; restart it "
        "afterwards so the cleared flags reload."
    )

    result = apply_clear(
        store,
        plan=plan,
        accounts_dir=resolved_accounts_dir,
        rotator_state_file=rotator_state_file,
        backup_root=Path(args.backup_root).resolve() if args.backup_root else None,
        force=args.force,
    )
    after = plan_clear(store, plan.targets)
    if args.json:
        import json

        print(json.dumps({"result": result, "after": after.before}, ensure_ascii=False))
    else:
        print(f"backup: {result['backup']}")
        for account_id in plan.targets:
            stored = store.get_account(account_id)
            print(
                f"after {account_id}: verify_required="
                f"{None if stored is None else stored.verify_required}"
            )
    return 0


def main() -> int:
    try:
        return run()
    except ClearVerifyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
