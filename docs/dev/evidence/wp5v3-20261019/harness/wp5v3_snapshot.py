"""Capture the WP5 v3 request-pool snapshot."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8099"
OUT = Path(__file__).resolve().parent.parent / "raw"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    tag = sys.argv[1] if len(sys.argv) > 1 else "phase-instances"
    with httpx.Client(trust_env=False, timeout=60) as client:
        resp = client.get(f"{BASE}/surface/instances")
    record = {
        "tag": tag,
        "status": resp.status_code,
        "body": resp.text[:20000],
    }
    (OUT / f"{tag}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[{tag}] status={resp.status_code}")
    print(resp.text[:2000])


if __name__ == "__main__":
    main()