"""Tests for ``scripts/clear_verify_required.py`` (offline, temp stores only)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from aistudio_api.config import settings
from aistudio_api.infrastructure.account.account_store import AccountStore

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "clear_verify_required.py"
)

_STORAGE_STATE: dict[str, object] = {
    "cookies": [
        {"name": "SID", "value": "sid", "domain": ".google.com", "path": "/"},
        {"name": "SAPISID", "value": "sap", "domain": ".google.com", "path": "/"},
    ],
    "origins": [],
}

FAKE_IDS = [
    "acc_a5a0296a",
    "acc_b477bef7",
    "acc_2d684b14",
    "acc_79cbb018",
    "acc_fbb16921",
]
REAL_ID = "acc_86bbfa31"


def _load_tool() -> Any:
    spec = importlib.util.spec_from_file_location(
        "clear_verify_required_tool", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def tool() -> Any:
    return _load_tool()


def _make_store(
    tmp_path: Path,
    *,
    flagged: list[str],
    real_flagged: list[str] | None = None,
    plain: list[str] | None = None,
) -> AccountStore:
    store = AccountStore(tmp_path / "accounts")
    all_ids = [*flagged, *(real_flagged or []), *(plain or [])]
    for index, account_id in enumerate(all_ids, start=1):
        store.save_account(
            name=f"Acc{index}",
            email=f"acc{index}@example.com",
            storage_state=_STORAGE_STATE,
            account_id=account_id,
        )
    for account_id in [*flagged, *(real_flagged or [])]:
        store.update_account_refresh(account_id, verify_required=True)
    # The five false flags were produced by the no-exit probe path and carry
    # this fingerprint; the genuine locks keep last_refresh_error=None.
    for account_id in flagged:
        store.mark_refresh_attempt(account_id, error="skipped_no_exit: no_exit")
    return store


def _registry(accounts_dir: Path) -> dict[str, Any]:
    data = json.loads((accounts_dir / "registry.json").read_text("utf-8"))
    return data["accounts"]


def _rotator_flags(state_file: Path) -> dict[str, bool]:
    data = json.loads(state_file.read_text("utf-8"))
    return {
        account_id: bool(item.get("verify_required"))
        for account_id, item in data.items()
    }


def test_list_reports_only_flagged_accounts(tmp_path: Path, tool: Any) -> None:
    store = _make_store(
        tmp_path, flagged=FAKE_IDS[:2], real_flagged=[REAL_ID], plain=["acc_plain"]
    )

    listed = [account.id for account in tool.list_verify_required(store)]

    assert listed == [FAKE_IDS[0], FAKE_IDS[1], REAL_ID]
    assert "acc_plain" not in listed


def test_dry_run_plan_writes_nothing(tmp_path: Path, tool: Any) -> None:
    store = _make_store(tmp_path, flagged=FAKE_IDS, real_flagged=[REAL_ID])
    accounts_dir = tmp_path / "accounts"
    before = _registry(accounts_dir)

    plan = tool.plan_clear(store, FAKE_IDS)

    assert plan.targets == FAKE_IDS
    assert plan.untouched == []
    assert plan.unknown == []
    assert all(item["verify_required"] is True for item in plan.before.values())
    # Masked emails only: the raw address is never printed or required.
    assert all("@" in str(item["email_masked"]) for item in plan.before.values())
    assert _registry(accounts_dir) == before


def _write_rotator_state(state_file: Path, ids: list[str], *, flag: bool) -> None:
    state_file.write_text(
        json.dumps({account_id: {"verify_required": flag} for account_id in ids}),
        encoding="utf-8",
    )


def test_apply_clears_only_selected_and_backs_up(
    tmp_path: Path, tool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "persist_rotator", True)
    store = _make_store(
        tmp_path, flagged=FAKE_IDS, real_flagged=[REAL_ID], plain=["acc_plain"]
    )
    accounts_dir = tmp_path / "accounts"
    rotator_state = tmp_path / "rotator_state.json"
    all_ids = [*FAKE_IDS, REAL_ID, "acc_plain"]
    _write_rotator_state(rotator_state, all_ids, flag=True)
    monkeypatch.setenv("AISTUDIO_ROTATOR_STATE_FILE", str(rotator_state))

    plan = tool.plan_clear(store, FAKE_IDS)
    result = tool.apply_clear(
        store,
        plan=plan,
        accounts_dir=accounts_dir,
        rotator_state_file=rotator_state,
        backup_root=tmp_path / "backups",
    )

    assert result["changed"] == FAKE_IDS
    backup = Path(str(result["backup"]))
    assert backup.is_dir() and (backup / "registry.json").is_file()
    assert (backup / "rotator_state.json").is_file()
    assert sorted(path.stem for path in (backup / "meta").glob("*.json")) == sorted(
        FAKE_IDS
    )

    registry = _registry(accounts_dir)
    for account_id in FAKE_IDS:
        assert registry[account_id]["verify_required"] is False
        meta = json.loads((accounts_dir / account_id / "meta.json").read_text("utf-8"))
        assert meta["verify_required"] is False
    # The real account is untouched, and the unflagged account stayed false.
    assert registry[REAL_ID]["verify_required"] is True
    assert registry["acc_plain"]["verify_required"] is False

    flags = _rotator_flags(rotator_state)
    for account_id in FAKE_IDS:
        assert flags[account_id] is False
    assert flags[REAL_ID] is True


def test_unknown_id_aborts_without_writes(tmp_path: Path, tool: Any) -> None:
    store = _make_store(tmp_path, flagged=FAKE_IDS[:2])
    accounts_dir = tmp_path / "accounts"
    before = _registry(accounts_dir)
    plan = tool.plan_clear(store, [FAKE_IDS[0], "acc_missing"])

    with pytest.raises(tool.ClearVerifyError):
        tool.apply_clear(
            store,
            plan=plan,
            accounts_dir=accounts_dir,
            rotator_state_file=tmp_path / "rotator_state.json",
            backup_root=tmp_path / "backups",
        )

    assert plan.unknown == ["acc_missing"]
    assert _registry(accounts_dir) == before
    assert not (tmp_path / "backups").exists()


def test_write_failure_restores_backup(
    tmp_path: Path, tool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "persist_rotator", True)
    store = _make_store(tmp_path, flagged=FAKE_IDS)
    accounts_dir = tmp_path / "accounts"
    rotator_state = tmp_path / "rotator_state.json"
    _write_rotator_state(rotator_state, FAKE_IDS, flag=True)
    monkeypatch.setenv("AISTUDIO_ROTATOR_STATE_FILE", str(rotator_state))
    before_registry = _registry(accounts_dir)
    before_rotator = rotator_state.read_text("utf-8")

    original = store.update_account_refresh
    calls = {"count": 0}

    def flaky(account_id: str, **kwargs: Any) -> bool:
        calls["count"] += 1
        if calls["count"] == 3:
            raise RuntimeError("simulated disk failure")
        return original(account_id, **kwargs)

    monkeypatch.setattr(store, "update_account_refresh", flaky)
    plan = tool.plan_clear(store, FAKE_IDS)

    with pytest.raises(RuntimeError):
        tool.apply_clear(
            store,
            plan=plan,
            accounts_dir=accounts_dir,
            rotator_state_file=rotator_state,
            backup_root=tmp_path / "backups",
        )

    # Best-effort all-or-nothing: registry and rotator files are restored.
    assert _registry(accounts_dir) == before_registry
    assert rotator_state.read_text("utf-8") == before_rotator
    assert list((tmp_path / "backups").iterdir())  # backup still kept for audit


def test_cli_list_json(
    tmp_path: Path, tool: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _make_store(tmp_path, flagged=FAKE_IDS[:1], real_flagged=[REAL_ID])
    assert store is not None
    code = tool.run(
        [
            "--list",
            "--accounts-dir",
            str(tmp_path / "accounts"),
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert list(payload["targets"]) == [FAKE_IDS[0], REAL_ID]
    assert all("verify_required" in item for item in payload["before"].values())


def test_plan_flags_targets_without_no_exit_fingerprint(
    tmp_path: Path, tool: Any
) -> None:
    store = _make_store(tmp_path, flagged=FAKE_IDS[:1], real_flagged=[REAL_ID])

    plan = tool.plan_clear(store, [FAKE_IDS[0], REAL_ID])

    assert plan.targets == [FAKE_IDS[0], REAL_ID]
    # The genuine lock (last_refresh_error=None) must require --force.
    assert plan.force_required == [REAL_ID]


def test_apply_refuses_targets_without_fingerprint_without_force(
    tmp_path: Path, tool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "persist_rotator", True)
    store = _make_store(tmp_path, flagged=FAKE_IDS[:1], real_flagged=[REAL_ID])
    accounts_dir = tmp_path / "accounts"
    before = _registry(accounts_dir)
    plan = tool.plan_clear(store, [REAL_ID])

    with pytest.raises(tool.ClearVerifyError):
        tool.apply_clear(
            store,
            plan=plan,
            accounts_dir=accounts_dir,
            rotator_state_file=tmp_path / "rotator_state.json",
            backup_root=tmp_path / "backups",
        )

    assert _registry(accounts_dir) == before
    assert not (tmp_path / "backups").exists()


def test_apply_force_clears_targets_without_fingerprint(
    tmp_path: Path, tool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "persist_rotator", True)
    store = _make_store(tmp_path, flagged=[], real_flagged=[REAL_ID])
    accounts_dir = tmp_path / "accounts"
    rotator_state = tmp_path / "rotator_state.json"
    _write_rotator_state(rotator_state, [REAL_ID], flag=True)
    monkeypatch.setenv("AISTUDIO_ROTATOR_STATE_FILE", str(rotator_state))
    plan = tool.plan_clear(store, [REAL_ID])

    result = tool.apply_clear(
        store,
        plan=plan,
        accounts_dir=accounts_dir,
        rotator_state_file=rotator_state,
        backup_root=tmp_path / "backups",
        force=True,
    )

    assert result["changed"] == [REAL_ID]
    assert _registry(accounts_dir)[REAL_ID]["verify_required"] is False


def test_cli_list_with_apply_is_rejected(tmp_path: Path, tool: Any) -> None:
    _make_store(tmp_path, flagged=FAKE_IDS[:1])

    with pytest.raises(SystemExit) as excinfo:
        tool.run(
            [
                "--list",
                "--apply",
                "--accounts-dir",
                str(tmp_path / "accounts"),
            ]
        )

    assert excinfo.value.code == 2


def test_cli_apply_requires_yes_and_writes_nothing(
    tmp_path: Path, tool: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _make_store(tmp_path, flagged=FAKE_IDS[:1])
    accounts_dir = tmp_path / "accounts"
    before = _registry(accounts_dir)

    code = tool.run(
        [
            "--ids",
            FAKE_IDS[0],
            "--apply",
            "--accounts-dir",
            str(accounts_dir),
            "--rotator-state-file",
            str(tmp_path / "rotator_state.json"),
        ]
    )

    assert code == 2
    assert "--yes" in capsys.readouterr().err
    assert _registry(accounts_dir) == before
    assert store is not None


def test_cli_apply_prints_stop_hint_and_backs_up_same_dir(
    tmp_path: Path,
    tool: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(settings, "persist_rotator", True)
    _make_store(tmp_path, flagged=FAKE_IDS[:1])
    accounts_dir = tmp_path / "accounts"
    rotator_state = tmp_path / "rotator_state.json"
    _write_rotator_state(rotator_state, FAKE_IDS[:1], flag=True)
    monkeypatch.setenv("AISTUDIO_ROTATOR_STATE_FILE", str(rotator_state))
    # AISTUDIO_DATA_DIR points elsewhere: the default backup must still follow
    # the resolved store directory, never a second resolution.
    monkeypatch.setenv("AISTUDIO_DATA_DIR", str(tmp_path / "other-data"))

    code = tool.run(
        [
            "--ids",
            FAKE_IDS[0],
            "--apply",
            "--yes",
            "--accounts-dir",
            str(accounts_dir),
            "--rotator-state-file",
            str(rotator_state),
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "must be stopped" in out
    backups = list((accounts_dir / "backups").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "registry.json").is_file()
    assert not (tmp_path / "other-data" / "accounts" / "backups").exists()
    assert _registry(accounts_dir)[FAKE_IDS[0]]["verify_required"] is False
