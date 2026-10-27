"""Probe random Ark credentials and import valid accounts for switching tests."""

from __future__ import annotations

import json
import random
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8099"
ARK_AUTH = Path(r"D:\LLM\Ark_AIStudioToAPI\configs\auth")
OUT = Path(__file__).resolve().parent.parent / "raw"
RECORD = OUT / "imported-accounts.json"
EXCLUDE = {"auth-1.json", "auth-5.json", "auth-13.json", "auth-44.json", "auth-46.json", "auth-18.json"}

TARGET = 4


def main() -> None:
    record = json.loads(RECORD.read_text(encoding="utf-8")) if RECORD.exists() else {}
    valid = [item for item in record.get("imported", []) if not item.get("expired")]
    sources = set(record.get("source_files", []))
    candidates = sorted(
        path
        for path in ARK_AUTH.glob("auth-*.json")
        if path.name not in EXCLUDE and path.name not in sources and path.stat().st_size > 1000
    )
    random.shuffle(candidates)
    new_sources: list[str] = []
    new_ids: list[str] = []

    with httpx.Client(trust_env=False, timeout=180) as client:
        for path in candidates:
            if len(valid) >= TARGET:
                break
            data = json.loads(path.read_text(encoding="utf-8"))
            if not data.get("cookies"):
                continue
            resp = client.post(
                f"{BASE}/accounts/probe-import",
                json={
                    "cookies": json.dumps(data, ensure_ascii=False),
                    "name_prefix": f"WP5v3:{path.stem}",
                },
            )
            new_sources.append(path.name)
            if resp.status_code != 200:
                continue
            payload = resp.json()
            for account in payload.get("accounts") or []:
                new_ids.append(str(account.get("id")))
                valid.append(
                    {
                        "id": account.get("id"),
                        "name": account.get("name"),
                        "tier": account.get("tier"),
                        "expired": account.get("expired"),
                    }
                )

        if new_ids:
            client.post(
                f"{BASE}/accounts/refresh",
                json={"account_ids": new_ids, "refresh_email": True},
            )
        accounts = client.get(f"{BASE}/accounts").json()

    by_id = {account.get("id"): account for account in accounts}
    valid = [
        {
            "id": item["id"],
            "name": by_id.get(item["id"], {}).get("name", item.get("name")),
            "tier": by_id.get(item["id"], {}).get("tier", item.get("tier")),
            "expired": by_id.get(item["id"], {}).get("expired", item.get("expired")),
        }
        for item in valid
        if item["id"] in by_id
    ]
    result = {
        "source_files": sorted(sources | set(new_sources)),
        "imported": valid,
        "valid_count": sum(1 for item in valid if not item.get("expired")),
    }
    RECORD.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()