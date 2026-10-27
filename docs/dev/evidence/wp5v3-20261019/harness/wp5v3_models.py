"""Capture Gemini/OpenAI model-list projections for WP5 v3 evidence."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8099"
OUT = Path(__file__).resolve().parent.parent / "raw"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    with httpx.Client(trust_env=False, timeout=60) as client:
        gemini = client.get(f"{BASE}/v1beta/models")
        openai = client.get(f"{BASE}/v1/models")
    payload = {
        "gemini_status": gemini.status_code,
        "gemini_names": [
            item.get("name") for item in (gemini.json().get("models") or [])
        ],
        "openai_status": openai.status_code,
        "openai_ids": [item.get("id") for item in (openai.json().get("data") or [])],
    }
    (OUT / "phase-models.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()