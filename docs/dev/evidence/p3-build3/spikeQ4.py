"""spikeQ4：本仓 build_signer.js 的真实 E2E（此前零执行的关键验证）。

流程与 spikeQ3 完全一致（聊天页共存 + BUILD 拦截表 + AUTH responder + 过门 +
经 WS(9998) 发 warmup 规格触发宿主 RPC → 吞没/harvest），唯一区别：
- direct 探针改为调用 window.__buildSigner.execute(rid, model, jsonText)（本仓冻结契约），
  经 Runtime binding __aistudio_stream_push__ 收集 status/chunk/done/error。
用法: uv run python spikeQ4.py <auth.json> <appId>
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

TEMP = Path(r"C:\Users\ADMIN\AppData\Local\Temp\kilo")
os.environ.setdefault("AISTUDIO_PROXY", "http://127.0.0.1:10809")
sys.path.insert(0, r"D:\LLM\self_programming\aistudio-api\src")

import websockets  # noqa: E402

from aistudio_api.infrastructure.browser.browser_engine import (  # noqa: E402
    launch_chromium_process,
)
from aistudio_api.infrastructure.browser.cdp_client import CDPClient  # noqa: E402

PORT = 9333
WS_PORT = 9998
PROFILE = TEMP / "spike-profile"
OUT = TEMP / "spikeQ4-evidence.json"
SIGNER_JS = Path(
    r"D:\LLM\self_programming\aistudio-api\src\aistudio_api\infrastructure\browser\js\build_signer.js"
).read_text(encoding="utf-8")

AUTH_RESPONDER_JS = """
(() => {
  if (window !== window.top) return;
  if (window.__spikeAuthResponder) return;
  window.__spikeAuthResponder = true;
  window.addEventListener("message", (ev) => {
    try {
      const d = ev.data;
      if (d && d.type === "requestAuthIndex" && ev.source) {
        ev.source.postMessage({ type: "authIndexResponse", authIndex: 0 }, "*");
      }
    } catch (e) {}
  });
})();
"""

DETECT_JS = """() => {
  const detect = (M, S) => {
    if (!M) return { ok: false, code: "MAKERSUITE_MISSING",
      msg: "window.default_MakerSuite 不存在：页面未加载完成或上游结构变更；处置：等待页面就绪后重试，若持续缺失按上游变更处理" };
    const t = (k) => typeof M[k];
    if (t("Cn") !== "function" || t("GB") !== "function")
      return { ok: false, code: "SIGNER_FUNCS_DRIFT",
        msg: "签名函数漂移: Cn=" + t("Cn") + " GB=" + t("GB") + "；处置：更新 hook 目标名单" };
    if (!S || !S.tb) return { ok: false, code: "TB_MISSING",
      msg: "tb 实例未捕获：warmup 未完成或 XHR hook 失效；处置：重跑 warmup / 检查 hook 注入" };
    let keyAttr = "";
    try { const mb = S.tb.mb; if (mb && mb.nK) keyAttr = "nK"; else if (mb && mb.jK) keyAttr = "jK"; } catch (e) {}
    return { ok: true, keyAttr, apiKeyPresent: !!(S.rpcHeaders && S.rpcHeaders["x-goog-api-key"]) };
  };
  const M = window.default_MakerSuite;
  const S = window.__buildSigner;
  let tb = false;
  try { tb = !!(S && S.harvest && S.harvest().tbPresent); } catch (e) {}
  const Ss = S ? { tb: tb } : null;
  return {
    live: detect(M, Ss),
    missing_M: detect(undefined, Ss),
    missing_tb: detect(M, undefined),
  };
}"""

ACTIVE_RECT_JS = """() => {
    const el = document.activeElement;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {x: r.x + r.width / 2, y: r.y + r.height / 2};
}"""

STATE_EXPR = """() => {
    const S = window.__buildSigner;
    if (!S) return null;
    let h = {};
    try { h = S.harvest ? S.harvest() : {}; } catch (e) {}
    return { warmup: (S.state === "warmup-captured" || S.state === "ready") ? "swallowed" : "none",
             hasToken: !!(h && h.tbPresent), state: S.state,
             rpcHeaderNames: Object.keys((h && h.headers) || {}) };
}"""

DISPATCH_JS = """(args) => {
  const S = window.__buildSigner;
  if (!S) return {error: "buildSigner missing"};
  if (!S.ready()) return {error: "not ready", state: S.state, lastError: S.lastError()};
  S.execute(args.rid, args.model, args.jsonText);
  return {started: true, state: S.state};
}"""

T0 = time.time()
ws_conns: list = []


async def ws_handler(websocket, path=None):
    ws_conns.append(websocket)
    try:
        async for _ in websocket:
            pass
    except Exception:  # noqa: BLE001
        pass


def load_ark_cookies(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    now = time.time()
    out: list[dict[str, object]] = []
    for c in data.get("cookies") or []:
        item: dict[str, object] = {
            "name": str(c.get("name")),
            "value": str(c.get("value")),
            "domain": str(c.get("domain") or ""),
            "path": str(c.get("path") or "/"),
            "secure": bool(c.get("secure", True)),
            "httpOnly": bool(c.get("httpOnly", False)),
        }
        exp = c.get("expires")
        if isinstance(exp, (int, float)) and exp > now:
            item["expires"] = float(exp)
        out.append(item)
    return out


async def main() -> None:
    auth_path = Path(sys.argv[1])
    app_id = sys.argv[2]
    cookies = load_ark_cookies(auth_path)
    host_url = (
        f"https://aistudio.google.com/apps/{app_id}"
        "?showAssistant=true&showPreview=true"
    )
    evidence: dict[str, object] = {"host_url": host_url, "spike": "Q4-carrier-signer-e2e"}

    server = await websockets.serve(ws_handler, "127.0.0.1", WS_PORT)
    proc = launch_chromium_process(port=PORT, user_data_dir=str(PROFILE), headless=True)
    client = CDPClient(PORT)
    try:
        # 与 Q3 相同的会话上下文：先聊天页（V2 变量），再 apps 第二标签
        _chat = await client.connect_page(block_assets=False)
        try:
            await _chat.goto(
                "https://aistudio.google.com/prompts/new_chat",
                timeout_s=60.0,
                wait_until="load",
            )
        except Exception as _e:  # noqa: BLE001
            evidence["chat_goto_error"] = str(_e)
        await asyncio.sleep(12)
        evidence["chat_tab_opened"] = True

        page = await client.create_new_page(block_assets=False)
        from aistudio_api.infrastructure.browser.cdp_client import (
            BUILD_SURFACE_BLOCKED_URL_PATTERNS,
        )

        await page.set_blocked_urls(BUILD_SURFACE_BLOCKED_URL_PATTERNS)
        evidence["blocked_patterns"] = len(BUILD_SURFACE_BLOCKED_URL_PATTERNS)
        try:
            await page.cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        except Exception:  # noqa: BLE001
            pass
        for src in (AUTH_RESPONDER_JS, SIGNER_JS):
            await page.cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": src})
        await page.set_cookies(cookies)  # type: ignore[arg-type]
        try:
            await page.goto(host_url, timeout_s=90.0, wait_until="load")
        except Exception as e:  # noqa: BLE001
            evidence["goto_error"] = str(e)
        deadline = time.time() + 80
        while time.time() < deadline and not ws_conns:
            await page.evaluate(
                """() => {
                    const words = ["Continue to the app", "Skip", "Launch"];
                    const els = Array.from(document.querySelectorAll(
                        'button, a, div[role="button"]'));
                    for (const w of words) {
                        const el = els.find(e => {
                            const t = (e.innerText || e.textContent || "").trim();
                            return t === w;
                        });
                        if (el) { el.click(); return w; }
                    }
                    return null;
                }"""
            )
            await asyncio.sleep(2)
        evidence["ws_connected_at_s"] = round(time.time() - T0, 2) if ws_conns else None
        print(f"[Q4] WS: {evidence['ws_connected_at_s']}")

        # 过门（可信输入配方）
        for i in range(45):
            await page.cdp.send(
                "Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": 100 + (i * 9) % 420, "y": 120 + (i * 7) % 360},
            )
            await asyncio.sleep(0.012)
        rect = await page.evaluate(ACTIVE_RECT_JS)
        if isinstance(rect, dict) and rect.get("x") and rect.get("y"):
            cx, cy = float(rect["x"]), float(rect["y"])
            await page.cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": cx, "y": cy})
            await page.cdp.send("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": cx, "y": cy,
                "button": "left", "buttons": 1, "clickCount": 1})
            await asyncio.sleep(0.05)
            await page.cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": cx, "y": cy,
                "button": "left", "buttons": 0, "clickCount": 1})
            await asyncio.sleep(0.5)
        for _ in range(8):
            await page.cdp.send("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": "Escape", "code": "Escape",
                "windowsVirtualKeyCode": 27, "nativeVirtualKeyCode": 27})
            await page.cdp.send("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": "Escape", "code": "Escape",
                "windowsVirtualKeyCode": 27, "nativeVirtualKeyCode": 27})
            await asyncio.sleep(0.03)

        # warmup 吞没（与 Q3 相同的 WS 规格注入路径）
        if ws_conns:
            spec = {
                "method": "POST",
                "path": "/v1beta/models/__warmup__:streamGenerateContent",
                "query_params": {"alt": "sse"},
                "request_attempt_id": "spike-q4",
                "request_id": "spike-q4-warmup",
                "streaming_mode": "real",
            }
            await ws_conns[0].send(json.dumps(spec))
            deadline = time.time() + 25
            state = None
            while time.time() < deadline:
                state = await page.evaluate(STATE_EXPR)
                if isinstance(state, dict) and state.get("warmup") == "swallowed":
                    break
                await asyncio.sleep(1)
            evidence["signer_after_warmup"] = state
            print("[Q4] signer: " + json.dumps(state, ensure_ascii=False))

        # E8 探测（保持与 Q3 相同读数）
        evidence["detect"] = await page.evaluate(DETECT_JS)
        print("[Q4] detect: " + json.dumps(evidence["detect"], ensure_ascii=False))

        # === Q4 核心：本仓签名器 execute 真实 E2E ===
        rid = "spike-q4-direct"
        events: list[dict] = []
        page.on_binding("__aistudio_stream_push__", lambda p: events.append(json.loads(p)))
        await page.add_binding("__aistudio_stream_push__")
        harvest = await page.evaluate(
            "() => { const S = window.__buildSigner; return S && S.harvest ? S.harvest() : null; }")
        evidence["harvest"] = harvest
        json_text = json.dumps(
            {"contents": [{"parts": [{"text": "只回复两个字符：OK"}], "role": "user"}]},
            ensure_ascii=False,
        )
        t0 = time.time()
        try:
            dispatch = await page.evaluate(
                DISPATCH_JS,
                {"rid": rid, "model": "gemini-3.8-flash", "jsonText": json_text},
                timeout_s=60.0,
            )
        except Exception as e:  # noqa: BLE001
            dispatch = {"exception": f"{type(e).__name__}: {e}"}
        evidence["dispatch"] = dispatch
        print("[Q4] dispatch: " + json.dumps(dispatch, ensure_ascii=False))

        timed_out = True
        deadline = time.time() + 150
        while time.time() < deadline:
            mine = [e for e in events if e.get("rid") == rid]
            if any(e.get("type") in ("done", "error") for e in mine):
                timed_out = False
                break
            await asyncio.sleep(0.5)
        mine = [e for e in events if e.get("rid") == rid]
        status_events = [e for e in mine if e.get("type") == "status"]
        chunks = [e for e in mine if e.get("type") == "chunk"]
        errors = [e for e in mine if e.get("type") == "error"]
        result: dict[str, object] = {
            "timedOut": timed_out,
            "seconds": round(time.time() - t0, 2),
            "status": status_events[-1].get("status") if status_events else None,
            "frames": len(chunks),
            "done": any(e.get("type") == "done" for e in mine),
            "error": errors[-1].get("message") if errors else None,
            "eventTypes": [e.get("type") for e in mine],
        }
        evidence["direct"] = {"seconds": result["seconds"], "result": result}
        evidence["frames_full"] = [
            {"i": i, "raw": e.get("text")} for i, e in enumerate(chunks)
        ]
        print("[Q4] direct: " + json.dumps(result, ensure_ascii=False))
        print(f"[Q4] frames_full={len(chunks)}")
    finally:
        OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[Q4] 证据已写入 {OUT}")
        try:
            server.close()
            await server.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass
        proc.terminate()


if __name__ == "__main__":
    asyncio.run(main())