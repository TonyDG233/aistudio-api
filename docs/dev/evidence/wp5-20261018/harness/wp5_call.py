import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8080"
OUT_DIR = Path(r"C:\Users\ADMIN\AppData\Local\Temp\kilo\wp5-verify")
OUT_DIR.mkdir(parents=True, exist_ok=True)

expect = sys.argv[1]  # expect200 | expect4xx
model = sys.argv[2]
tag = sys.argv[3]

client = httpx.Client(trust_env=False, timeout=600)
body = {"contents": [{"role": "user", "parts": [{"text": "请数到三，每个数字单独一行。"}]}]}


def run_once(suffix: str) -> tuple[int, list[str], float]:
    lines: list[str] = []
    t0 = time.time()
    with client.stream(
        "POST",
        f"{BASE}/v1beta/models/{model}:streamGenerateContent?alt=sse",
        json=body,
    ) as stream:
        status = stream.status_code
        for line in stream.iter_lines():
            if line:
                lines.append(line)
            if len(lines) >= 900:
                break
    (OUT_DIR / f"{tag}-{suffix}.out").write_text("\n".join(lines), encoding="utf-8")
    return status, lines, time.time() - t0


status, lines, secs = run_once("try1")
joined = "\n".join(lines)
if expect == "expect200" and status == 200 and "list index out of range" in joined and len(lines) <= 3:
    print("cold-start race (R8) detected, retrying once after 5s...")
    time.sleep(5)
    status, lines, secs = run_once("try2")
    joined = "\n".join(lines)

texts: list[str] = []
error_codes: list[str] = []
error_msgs: list[str] = []
for ln in lines:
    payload = ln[5:].strip() if ln.startswith("data: ") else None
    if not payload or payload in ("", "[DONE]"):
        continue
    try:
        chunk = json.loads(payload)
    except json.JSONDecodeError:
        try:
            chunk = json.loads(joined)
        except json.JSONDecodeError:
            continue
    if not isinstance(chunk, dict):
        continue
    if isinstance(chunk.get("error"), dict):
        err = chunk["error"]
        error_msgs.append(str(err.get("message"))[:400])
        if err.get("code"):
            error_codes.append(str(err["code"]))
    for cand in chunk.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            if isinstance(part, dict) and part.get("text"):
                texts.append(str(part["text"]))

if not texts and not error_msgs:
    # non-SSE 4xx body fallback
    try:
        blob = json.loads(joined) if joined.strip() else None
        if isinstance(blob, dict) and isinstance(blob.get("error"), dict):
            error_msgs.append(str(blob["error"].get("message"))[:400])
            if blob["error"].get("code"):
                error_codes.append(str(blob["error"]["code"]))
    except json.JSONDecodeError:
        pass

answer = "".join(texts)
print(f"[{tag}] status={status} secs={secs:.1f}")
print(f"[{tag}] answer[:200]={answer[:200]!r}")
if error_msgs:
    print(f"[{tag}] errorMsgs={error_msgs[:2]} codes={error_codes[:2]}")

if expect == "expect200":
    ok = status == 200 and bool(answer)
    verdict = "PASS" if ok else "FAIL"
else:
    denied = bool(error_msgs) and (
        any("surface_" in m for m in error_msgs)
        or any(code in ("403", "429", "400") for code in error_codes)
        or 400 <= status < 500
    )
    ok = denied and not answer
    verdict = "PASS" if ok else "FAIL"
print(f"[{tag}] VERDICT={verdict} (expect={expect})")
sys.exit(0 if ok else 1)