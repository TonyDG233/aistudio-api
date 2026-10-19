"""Sequential 4-account switch test using the unified request pool."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8099"
OUT = Path(__file__).resolve().parent.parent / "raw"
OUT.mkdir(parents=True, exist_ok=True)


def _snapshot(client: httpx.Client) -> dict[str, object]:
    resp = client.get(f"{BASE}/surface/instances")
    if resp.status_code != 200:
        return {"status": resp.status_code, "body": resp.text[:2000]}
    return resp.json()


def main() -> None:
    rounds: list[dict[str, object]] = []
    with httpx.Client(trust_env=False, timeout=600) as client:
        for index in range(1, 5):
            body = {
                "model": "pg/gemini-flash-latest",
                "messages": [
                    {"role": "user", "content": f"只回答 OK (rotation {index})"}
                ],
                "max_tokens": 24,
            }
            started = time.time()
            resp = client.post(f"{BASE}/v1/chat/completions", json=body)
            elapsed = round(time.time() - started, 1)
            snapshot = _snapshot(client)
            record = {
                "round": index,
                "status": resp.status_code,
                "elapsed_s": elapsed,
                "body": resp.text[:4000],
                "snapshot": snapshot,
            }
            (OUT / f"phase-rotation-{index}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            rounds.append(record)
            accounts = [
                slot.get("account_id")
                for slot in (snapshot.get("contexts") or [])
                if isinstance(slot, dict)
            ]
            print(
                index,
                resp.status_code,
                elapsed,
                "contexts=",
                snapshot.get("context_count"),
                "accounts=",
                accounts,
            )

        final = _snapshot(client)
    accounts_seen = sorted(
        {
            str(slot.get("account_id"))
            for item in rounds
            for slot in ((item.get("snapshot") or {}).get("contexts") or [])
            if isinstance(slot, dict) and slot.get("account_id")
        }
    )
    summary = {
        "rounds": [
            {
                "round": item["round"],
                "status": item["status"],
                "elapsed_s": item["elapsed_s"],
            }
            for item in rounds
        ],
        "accounts_seen": accounts_seen,
        "distinct_accounts": len(accounts_seen),
        "final_context_count": final.get("context_count"),
        "max_contexts": (final.get("budgets") or {}).get("max_contexts"),
    }
    (OUT / "phase-rotation-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()