"""Minimal OpenAI-compatible caller for WP5 v3 evidence."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8099"
OUT = Path(__file__).resolve().parent.parent / "raw"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    tag = sys.argv[1]
    model = sys.argv[2]
    prompt = sys.argv[3] if len(sys.argv) > 3 else "只回答 OK"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 24,
    }
    started = time.time()
    with httpx.Client(trust_env=False, timeout=600) as client:
        resp = client.post(f"{BASE}/v1/chat/completions", json=body)
    elapsed = round(time.time() - started, 1)
    record = {
        "tag": tag,
        "model": model,
        "status": resp.status_code,
        "elapsed_s": elapsed,
        "body": resp.text[:20000],
    }
    (OUT / f"{tag}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    answer = ""
    try:
        choices = (resp.json().get("choices") or [])
        if choices:
            answer = str((choices[0].get("message") or {}).get("content") or "")
    except Exception:
        pass
    print(f"[{tag}] status={resp.status_code} elapsed={elapsed}s answer={answer[:120]!r}")
    if resp.status_code != 200 and resp.text:
        print(f"[{tag}] body={resp.text[:500]}")


if __name__ == "__main__":
    main()