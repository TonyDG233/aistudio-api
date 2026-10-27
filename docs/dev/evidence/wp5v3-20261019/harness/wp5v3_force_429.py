"""Force one local 429 and verify the normal switch path handles it."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8099"
OUT = Path(__file__).resolve().parent.parent / "raw"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    body = {
        "model": "pg/gemini-flash-latest",
        "messages": [{"role": "user", "content": "只回答 OK (forced 429 switch)"}],
        "max_tokens": 24,
    }
    with httpx.Client(trust_env=False, timeout=600) as client:
        started = time.time()
        resp = client.post(f"{BASE}/v1/chat/completions", json=body)
        elapsed = round(time.time() - started, 1)
        snapshot = client.get(f"{BASE}/surface/instances").json()
    record = {
        "status": resp.status_code,
        "elapsed_s": elapsed,
        "body": resp.text[:4000],
        "contexts": snapshot.get("contexts", []),
        "context_count": snapshot.get("context_count"),
    }
    (OUT / "phase-forced-429-switch.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    accounts = [
        slot.get("account_id")
        for slot in (snapshot.get("contexts") or [])
        if isinstance(slot, dict)
    ]
    print(
        json.dumps(
            {
                "status": resp.status_code,
                "elapsed_s": elapsed,
                "context_accounts": accounts,
                "context_count": snapshot.get("context_count"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()