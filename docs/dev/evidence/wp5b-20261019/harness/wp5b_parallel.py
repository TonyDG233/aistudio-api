"""Fire a chat and a build request concurrently for cross-account evidence."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8099"
OUT = Path(__file__).resolve().parent.parent / "raw"
OUT.mkdir(parents=True, exist_ok=True)


def call(tag: str, model: str) -> None:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复 OK"}],
        "max_tokens": 64,
    }
    started = time.time()
    try:
        with httpx.Client(trust_env=False, timeout=600) as client:
            resp = client.post(f"{BASE}/v1/chat/completions", json=body)
        record = {
            "tag": tag,
            "model": model,
            "status": resp.status_code,
            "started_at": started,
            "elapsed_s": round(time.time() - started, 1),
            "body": resp.text[:20000],
        }
    except Exception as exc:
        record = {
            "tag": tag,
            "model": model,
            "status": None,
            "started_at": started,
            "elapsed_s": round(time.time() - started, 1),
            "error": repr(exc),
        }
    (OUT / f"{tag}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(tag, record["status"], record["elapsed_s"])


threads = [
    threading.Thread(target=call, args=("phase-e-chat-pro", "chat/gemini-flash-latest")),
    threading.Thread(target=call, args=("phase-e-build-free", "build/gemini-flash-latest")),
]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()