"""Import four random Ark credentials for WP5 v3 switching tests.

Credentials are read from the reference project, sent to the local API via a
temporary bundle file, and never printed or committed. Imported account IDs are
written to ``raw/imported-accounts.json`` (no cookies/emails).
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8099"
ARK_AUTH = Path(r"D:\LLM\Ark_AIStudioToAPI\configs\auth")
OUT = Path(__file__).resolve().parent.parent / "raw"
OUT.mkdir(parents=True, exist_ok=True)
EXCLUDE = {"auth-1.json", "auth-5.json"}


def _pick_files(count: int = 4) -> list[Path]:
    candidates = sorted(
        path
        for path in ARK_AUTH.glob("auth-*.json")
        if path.name not in EXCLUDE and path.stat().st_size > 1000
    )
    if len(candidates) < count:
        raise SystemExit(f"not enough Ark credential files: {len(candidates)}")
    return random.sample(candidates, count)


def main() -> None:
    files = _pick_files()
    items: list[dict[str, object]] = []
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("cookies"), list) or not data["cookies"]:
            continue
        items.append(
            {
                "name": f"WP5v3:{path.stem}",
                "cookies": json.dumps(data, ensure_ascii=False),
                "auth_user": "0",
            }
        )
    bundle = Path(os.environ.get("TEMP", ".")) / "wp5v3-import-bundle.json"
    bundle.write_text(
        json.dumps({"accounts": items}, ensure_ascii=False), encoding="utf-8"
    )

    with httpx.Client(trust_env=False, timeout=120) as client:
        resp = client.post(
            f"{BASE}/accounts/import-bundle",
            json={"file_path": str(bundle)},
        )
        resp.raise_for_status()
        imported = resp.json().get("accounts") or []
        ids = [str(item["id"]) for item in imported]
        refresh = client.post(
            f"{BASE}/accounts/refresh",
            json={"account_ids": ids, "refresh_email": True},
        )
        refresh_payload = refresh.json() if refresh.status_code == 200 else {}
        accounts = client.get(f"{BASE}/accounts").json()

    selected = [
        {
            "id": account.get("id"),
            "name": account.get("name"),
            "tier": account.get("tier"),
            "expired": account.get("expired"),
        }
        for account in accounts
        if account.get("id") in set(ids)
    ]
    record = {
        "source_files": [path.name for path in files],
        "imported": selected,
        "refresh_status": refresh.status_code,
        "refresh_results": refresh_payload.get("results", []),
    }
    (OUT / "imported-accounts.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))
    bundle.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())