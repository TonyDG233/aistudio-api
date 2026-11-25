#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""独立扫码助手：在本机有头浏览器里完成 AI Studio 账号的 Verify device 流程。

用法：uv run --with "websockets>=13,<16" --with "segno>=1.6,<2" verify_helper.py scan-xxx.json
凭证为服务端导出的 JSON（version == 1，cookies 非空）。脚本只在本机临时
Chrome/Edge profile 中注入 Cookie，不连接服务端，也不依赖本项目代码。
凭证 JSON 与生成的 qr.png 都含敏感信息，用完请立即删除。退出码：0 通过；2 设备级失败；3 环境级失败或超时；1 参数/凭证/浏览器错误。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, NoReturn

try:
    import segno
    from websockets.sync.client import connect
except ImportError as exc:
    raise SystemExit(
        "缺少依赖 websockets/segno，请用 `uv run --with websockets --with segno ...` 运行。"
    ) from exc

for _stream in (sys.stdout, sys.stderr):
    _reconfigure: Any = getattr(_stream, "reconfigure", None)
    if callable(_reconfigure):
        _reconfigure(encoding="utf-8", errors="replace")

VERIFY_URL = "https://aistudio.google.com/verify-account"
BOOTSTRAP_URL = "https://accounts.google.com/"
QR_PATH = Path(tempfile.gettempdir()) / f"aistudio-verify-qr-{os.getpid()}.png"
QR_RE = re.compile(r"recaptcha\.net/qr/([A-Za-z0-9_-]{6,32})")
SUCCESS_MARKERS = (
    "验证通过",
    "go to ai studio",
    "you're all set",
    "verification complete",
    "device verified",
)
DEVICE_FAIL = "we couldn't verify your account with this device"
ENV_FAIL = "something went wrong while verifying your device"
SAME_SITE = {"none": "None", "no_restriction": "None", "lax": "Lax", "strict": "Strict"}
PAGE_TEXT_JS = "(() => { const b = document.body; return {text: b ? String(b.innerText || '').slice(0, 20000) : '', href: String(location.href || '')}; })()"
CLICK_JS = """(() => {
  const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
  const usable = (el) => !el.disabled && el.getAttribute('aria-disabled') !== 'true';
  const match = nodes.find((el) => usable(el) && el.hasAttribute('data-test-verify-button')) || nodes.find((el) => usable(el) && String(el.textContent || '').trim().toLowerCase().includes('verify device'));
  if (!match) return false;
  match.click();
  return true;
})()"""
READ_QR_JS = """(() => {
  const button = document.getElementById('rc-mobile-verification-button');
  const canvas = document.querySelector('canvas.recaptchaJavascriptChallengeQrCanvas');
  const fallback = document.querySelector('.recaptchaJavascriptChallengeQrFallbackText');
  const links = Array.from(document.querySelectorAll('a[href]')).map((n) => String(n.href || '')).filter((h) => h.includes('recaptcha.net/qr/'));
  const alts = [];
  for (const img of document.querySelectorAll('img')) {
    if (alts.length >= 5) break;
    const label = String(img.getAttribute('alt') || img.getAttribute('aria-label') || '');
    if (label && label.toLowerCase().includes('qr')) alts.push(label.slice(0, 200));
  }
  return {
    href: button && button.href ? String(button.href) : '',
    title: canvas && canvas.title ? String(canvas.title) : '',
    fallback: fallback && fallback.textContent ? String(fallback.textContent) : '',
    canvasAriaLabel: canvas && canvas.getAttribute('aria-label') ? String(canvas.getAttribute('aria-label')) : '',
    imgQrAlts: alts,
    links: links,
  };
})()"""


class HelperError(RuntimeError):
    """可展示给用户的、预期内的错误。"""


def load_bundle(path_text: str) -> dict[str, object]:
    """读取并校验凭证 JSON；错误信息绝不包含 Cookie 名称或值。"""
    path = Path(path_text).expanduser()
    if not path.is_file():
        raise HelperError(f"凭证文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise HelperError(f"无法读取凭证 JSON：{exc}") from exc
    cookies = data.get("cookies") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(cookies, list)
        or not cookies
    ):
        raise HelperError("凭证格式错误：需要 version 1 且 cookies 非空")
    if any(
        not isinstance(c, dict)
        or not isinstance(c.get("name"), str)
        or not c["name"]
        or not isinstance(c.get("value"), str)
        for c in cookies
    ):
        raise HelperError("凭证格式错误：cookies 中存在无效条目")
    return data


def resolve_chrome(explicit: str | None) -> str:
    """按 --chrome、环境变量、系统安装路径的顺序解析浏览器。"""
    candidates = [explicit] if explicit else []
    if not explicit:
        # Prefer the repo CloakBrowser: it is the only binary this flow has
        # been verified with end-to-end. Edge can be judged as a broken
        # browser environment by reCAPTCHA even when headed.
        for root in (Path.cwd(), Path(__file__).resolve().parents[2]):
            with contextlib.suppress(OSError):
                for candidate in sorted(root.glob(".cloakbrowser/*/chrome*")):
                    if candidate.name in {"chrome", "chrome.exe"} and candidate.is_file():
                        candidates.append(str(candidate))
    if not explicit:
        if env_path := os.environ.get("AISTUDIO_VERIFY_CHROME_PATH"):
            candidates.append(env_path)
        if os.name == "nt":
            roots = (
                os.environ.get("PROGRAMFILES"),
                os.environ.get("PROGRAMFILES(X86)"),
                os.environ.get("LOCALAPPDATA"),
            )
            rels = (
                r"Google\Chrome\Application\chrome.exe",
                r"Microsoft\Edge\Application\msedge.exe",
            )
            candidates += [
                str(Path(root) / rel) for root in roots if root for rel in rels
            ] + ["chrome", "msedge"]
        elif sys.platform == "darwin":
            candidates += [
                f"/Applications/{name}.app/Contents/MacOS/{name}"
                for name in ("Google Chrome", "Microsoft Edge", "Chromium")
            ]
        else:
            candidates += [
                "chromium",
                "chromium-browser",
                "google-chrome",
                "google-chrome-stable",
                "microsoft-edge",
            ]
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path)
        if found := shutil.which(candidate):
            return found
    raise HelperError(
        f"--chrome 指定的浏览器不存在：{explicit}"
        if explicit
        else "未找到 Edge/Chrome。请用 --chrome PATH 指定，或设置 AISTUDIO_VERIFY_CHROME_PATH。"
    )


def launch_browser(binary: str, port: int, profile: Path) -> subprocess.Popen:
    """以有头模式启动浏览器；端口 0 表示由浏览器自选并写入 DevToolsActivePort。"""
    args = [
        binary,
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-site-isolation-trials",
        "--window-size=1280,900",
        "--lang=en-US",
    ]
    if (
        sys.platform.startswith("linux")
        and hasattr(os, "geteuid")
        and os.geteuid() == 0
    ):
        args.append("--no-sandbox")
    args.append("about:blank")
    try:
        return subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise HelperError(f"无法启动浏览器：{exc}") from exc


def _http_json(url: str, timeout: float, method: str = "GET") -> object:
    """请求本机 DevTools HTTP 端点并解析 JSON。"""
    request = urllib.request.Request(url, method=method)
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
        request, timeout=timeout
    ) as response:
        return json.loads(response.read().decode("utf-8"))


def _devtools_port(profile: Path, proc: subprocess.Popen, deadline: float) -> int:
    """Read the port Chromium wrote to ``DevToolsActivePort`` (avoids port races)."""
    marker = profile / "DevToolsActivePort"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise HelperError(
                f"浏览器启动失败：进程提前退出（退出码 {proc.returncode}）。"
            )
        try:
            first = marker.read_text(encoding="utf-8").splitlines()[0].strip()
            return int(first)
        except (OSError, ValueError, IndexError):
            time.sleep(0.2)
    raise HelperError("等待浏览器 DevTools 端口超时（15 秒）。")


def _browser_ws_url(port: int, proc: subprocess.Popen, deadline: float) -> str:
    """等待 /json/version 就绪并返回 page target 的 WebSocket 地址。"""
    base = f"http://127.0.0.1:{port}"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise HelperError(
                f"浏览器启动失败：进程提前退出（退出码 {proc.returncode}）。"
            )
        try:
            _http_json(f"{base}/json/version", 1.0)
            listing = _http_json(f"{base}/json/list", 1.0)
        except Exception:
            time.sleep(0.25)
            continue
        for item in listing if isinstance(listing, list) else [listing]:
            if (
                isinstance(item, dict)
                and item.get("type") == "page"
                and item.get("webSocketDebuggerUrl")
            ):
                ws = str(item["webSocketDebuggerUrl"])
                if ws.startswith(
                    (f"ws://127.0.0.1:{port}/", f"ws://localhost:{port}/")
                ):
                    return ws
        with contextlib.suppress(Exception):
            created = _http_json(f"{base}/json/new?about:blank", 2.0, "PUT")
            if isinstance(created, dict):
                return str(created["webSocketDebuggerUrl"])
        time.sleep(0.25)
    raise HelperError("等待浏览器 DevTools 端口超时（30 秒）。")


class CDP:
    def __init__(self, ws: Any) -> None:
        """保存 WebSocket 与自增请求 id。"""
        self.ws, self.next_id = ws, 0

    def send(
        self,
        method: str,
        params: dict[str, object] | None = None,
        timeout: float = 15.0,
    ) -> dict[str, object]:
        """发送命令并等待同 id 的响应。"""
        self.next_id += 1
        message: dict[str, object] = {
            "id": self.next_id,
            "method": method,
            **({"params": params} if params else {}),
        }
        self.ws.send(json.dumps(message, ensure_ascii=False))
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                raw = self.ws.recv(timeout=remaining)
            except Exception as exc:
                raise HelperError(
                    f"CDP 命令 {method} 失败：{type(exc).__name__}"
                ) from exc
            reply = json.loads(raw)
            if reply.get("id") != self.next_id:
                continue
            if isinstance(error := reply.get("error"), dict):
                raise HelperError(f"CDP 调用失败：{error.get('message') or '未知错误'}")
            return reply.get("result") or {}
        raise HelperError(f"CDP 命令 {method} 超时")


def evaluate(
    cdp: CDP, expression: str, context_id: int | None = None, timeout: float = 10.0
) -> object:
    """在页面或指定 isolated world 中执行 JS 并返回取值。"""
    params: dict[str, object] = {"expression": expression, "returnByValue": True}
    if context_id is not None:
        params["contextId"] = context_id
    result = cdp.send("Runtime.evaluate", params, timeout)
    if result.get("exceptionDetails"):
        return None
    value = result.get("result")
    return value.get("value") if isinstance(value, dict) else None


def inject_cookies(cdp: CDP, cookies: list[object]) -> int:
    """逐个注入 Cookie，只打印成功/失败数量，返回失败数。"""
    success = failed = 0
    for cookie in cookies:
        if not isinstance(cookie, dict):
            failed += 1
            continue
        name, path = str(cookie.get("name") or ""), str(cookie.get("path") or "/")
        path = path if path.startswith("/") else f"/{path}"
        item: dict[str, object] = {
            "name": name,
            "value": str(cookie.get("value") or ""),
            "path": path,
            "secure": bool(cookie.get("secure", True)),
            "httpOnly": bool(cookie.get("httpOnly", False)),
        }
        raw_domain = str(cookie.get("domain") or "").strip()
        host = raw_domain.lstrip(".")
        if not raw_domain:
            raw_url = cookie.get("url")
            if not isinstance(raw_url, str) or not raw_url.startswith("https://"):
                failed += 1
                continue
            item["url"] = raw_url
        elif name.startswith("__Host-"):
            item.update(url=f"https://{host}/", path="/", secure=True)
        else:
            item.update(domain=raw_domain, url=f"https://{host}/")
        if isinstance(same_site := cookie.get("sameSite"), str) and (
            mapped := SAME_SITE.get(same_site.strip().lower())
        ):
            item["sameSite"] = mapped
        expiry = cookie.get("expires")
        if not isinstance(expiry, (int, float)) or isinstance(expiry, bool):
            expiry = cookie.get("expirationDate")
        if isinstance(expiry, (int, float)) and not isinstance(expiry, bool):
            item["expires"] = float(expiry)
        if name.startswith("__Secure-") or (
            item.get("sameSite") == "None" and item["secure"] is False
        ):
            item["secure"] = True
        try:
            result = cdp.send("Network.setCookie", item, timeout=10.0)
        except HelperError:
            failed += 1
            continue
        success += result.get("success") is True
        failed += result.get("success") is not True
    print(
        f"Cookie 注入完成：成功 {success} 个，失败 {failed} 个，共 {len(cookies)} 个。"
    )
    if not success:
        raise HelperError("所有 Cookie 注入失败，请确认浏览器版本与凭证格式。")
    return failed


def read_qr_code(cdp: CDP, worlds: dict[str, int]) -> str | None:
    """在 bframe 的 isolated world 中读取 reCAPTCHA 二维码短码。"""
    try:
        tree = cdp.send("Page.getFrameTree", timeout=5.0)
    except HelperError:
        return None
    stack = [tree.get("frameTree")]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        stack.extend(node.get("childFrames") or [])
        frame = node.get("frame")
        if not isinstance(frame, dict):
            continue
        url, frame_id = str(frame.get("url") or ""), str(frame.get("id") or "")
        if not frame_id or ("bframe" not in url and "recaptcha" not in url):
            continue
        context_id = worlds.get(frame_id)
        if context_id is None:
            try:
                raw_context = cdp.send(
                    "Page.createIsolatedWorld",
                    {
                        "frameId": frame_id,
                        "worldName": "verify_helper_qr",
                        "grantUniveralAccess": True,
                    },
                    timeout=5.0,
                ).get("executionContextId")
                context_id = int(str(raw_context))
                worlds[frame_id] = context_id
            except (HelperError, KeyError, TypeError):
                continue
        try:
            payload = evaluate(cdp, READ_QR_JS, context_id, timeout=5.0)
        except HelperError:
            worlds.pop(frame_id, None)
            continue
        if isinstance(payload, dict):
            extras = [*(payload.get("links") or []), *(payload.get("imgQrAlts") or [])]
            for candidate in [
                payload.get("href"),
                payload.get("title"),
                payload.get("fallback"),
                payload.get("canvasAriaLabel"),
                *extras,
            ]:
                if isinstance(candidate, str) and (match := QR_RE.search(candidate)):
                    return match.group(1)
    return None


def drive(args: argparse.Namespace, cdp: CDP, qr_path: Path, account: str) -> int:
    """点击验证、读取二维码，并按页面文案等待最终结果。"""
    deadline = time.monotonic() + args.timeout
    worlds: dict[str, int] = {}
    qr_code: str | None = None
    last_click = last_probe = 0.0
    last_text = ""
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now - last_probe >= 2.0:
            last_probe = now
            try:
                payload = evaluate(cdp, PAGE_TEXT_JS, timeout=5.0)
            except HelperError:
                payload = None
            text = ""
            href = ""
            if isinstance(payload, dict):
                text = str(payload.get("text") or "")
                href = str(payload.get("href") or "")
            if text:
                last_text = text
                normalized = " ".join(
                    text.replace("\u2019", "'").replace("\u2018", "'").split()
                ).lower()
                left_verify_page = (
                    "aistudio.google.com" in href and "/verify-account" not in href
                )
                if qr_code is not None and (
                    left_verify_page
                    or any(marker in normalized for marker in SUCCESS_MARKERS)
                ):
                    print(f"验证通过：{account} 已完成设备验证。")
                    return 0
                if DEVICE_FAIL in normalized:
                    print(
                        "设备级失败：We couldn't verify your account with this device。"
                        "该手机近期验证次数可能已达上限或未通过设备校验；"
                        "请稍后再试，或按 Google 页面提示处理。"
                    )
                    return 2
                if ENV_FAIL in normalized:
                    print(
                        "环境级失败：Something went wrong while verifying your device。请检查浏览器环境与网络。"
                    )
                    return 3
        if qr_code is None:
            if (code := read_qr_code(cdp, worlds)) is not None:
                qr_code = code
                segno.make(f"https://recaptcha.net/qr/{code}").save(
                    str(qr_path), scale=10, border=3
                )
                print(f"二维码短码：{code}\n二维码已保存：{qr_path}")
                if not args.no_open:
                    try:
                        if os.name == "nt":
                            os.startfile(str(qr_path))
                        else:
                            opener = "open" if sys.platform == "darwin" else "xdg-open"
                            subprocess.Popen(
                                [opener, str(qr_path)],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True,
                            )
                    except Exception as exc:
                        print(
                            f"[提示] 无法自动打开图片（{type(exc).__name__}），请手动打开：{qr_path}"
                        )
            elif now - last_click >= 2.0:
                with contextlib.suppress(HelperError):
                    if evaluate(cdp, CLICK_JS, timeout=5.0):
                        last_click = now
        time.sleep(0.5 if qr_code is None else 2.0)
    print("等待验证结果超时。")
    print(
        f"页面最后 300 字：\n{last_text.strip()[-300:]}"
        if last_text
        else "页面没有可读取的文本。"
    )
    return 3


def shutdown(cdp: CDP | None, proc: subprocess.Popen | None) -> None:
    """先尝试 Browser.close，失败或进程仍在时终止浏览器。"""
    if cdp is not None:
        with contextlib.suppress(Exception):
            cdp.send("Browser.close", timeout=3.0)
        with contextlib.suppress(Exception):
            cdp.ws.close()
    if proc is not None and proc.poll() is None:
        with contextlib.suppress(Exception):
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                proc.terminate()
            proc.wait(5)
        if proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.kill()
                proc.wait(3)


def _cleanup_stale_profiles() -> None:
    """Remove helper profiles left behind by earlier crashed runs (> 1 hour)."""
    root = Path(tempfile.gettempdir())
    cutoff = time.time() - 3600
    with contextlib.suppress(OSError):
        for entry in root.glob("aistudio-verify-*"):
            try:
                if (
                    not entry.is_dir()
                    or not (entry / ".verify-helper-profile").is_file()
                ):
                    continue
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue


class _ArgumentParser(argparse.ArgumentParser):
    """Argparse that reserves exit code 2 for device-level failures."""

    def error(self, message: str) -> NoReturn:
        print(f"[错误] {message}", file=sys.stderr)
        raise SystemExit(1)


def run(args: argparse.Namespace) -> int:
    """串联凭证校验、浏览器启动、Cookie 注入、导航与扫码等待。"""
    bundle = load_bundle(args.bundle)
    cookies = bundle.get("cookies")
    if not isinstance(cookies, list):
        raise HelperError("凭证格式错误：cookies 必须是数组")
    account = str(bundle.get("email") or bundle.get("account_id") or "未知账号")
    print(f"已读取凭证：{account}，Cookie {len(cookies)} 个。")
    binary = resolve_chrome(args.chrome)
    requested_port = args.port if args.port is not None else 0
    _cleanup_stale_profiles()
    profile = Path(tempfile.mkdtemp(prefix="aistudio-verify-"))
    (profile / ".verify-helper-profile").write_text("1", encoding="utf-8")
    proc: subprocess.Popen | None = None
    cdp: CDP | None = None
    try:
        proc = launch_browser(binary, requested_port, profile)
        deadline = time.monotonic() + 20.0
        port = _devtools_port(profile, proc, deadline)
        try:
            cdp = CDP(
                connect(
                    _browser_ws_url(port, proc, deadline), max_size=None, proxy=None
                )
            )
        except Exception as exc:
            raise HelperError(f"连接浏览器 CDP 失败：{exc}") from exc
        cdp.send("Page.enable")
        cdp.send("Network.enable")
        inject_cookies(cdp, cookies)
        # The pooled session always negotiates accounts.google.com before the
        # verify page; skipping it is the documented cause of a 403 caller
        # identity error.  A bootstrap hiccup must not abort the attempt.
        with contextlib.suppress(HelperError):
            cdp.send("Page.navigate", {"url": BOOTSTRAP_URL}, timeout=30.0)
            time.sleep(3.0)
        nav = cdp.send("Page.navigate", {"url": VERIFY_URL}, timeout=30.0)
        if nav.get("errorText"):
            raise HelperError(f"打开验证页失败：{nav['errorText']}")
        return drive(args, cdp, QR_PATH, account)
    finally:
        shutdown(cdp, proc)
        with contextlib.suppress(OSError):
            QR_PATH.unlink()
        if args.keep_profile:
            print(f"已保留临时浏览器 profile：{profile}")
            print("[提示] 该目录包含登录 Cookie，排查完成后请手动删除。")
        else:
            shutil.rmtree(profile, ignore_errors=True)
            if profile.exists():
                print(
                    f"[警告] 临时 profile 未能删除，其中包含登录 Cookie，请手动删除：{profile}",
                    file=sys.stderr,
                )


def main() -> int:
    """解析参数并运行；所有预期错误以清晰中文输出。"""
    parser = _ArgumentParser(
        description=(
            "在本机有头浏览器中完成 AI Studio 账号的 Verify device 扫码验证。"
            "凭证请从服务端导出为 JSON；凭证与 qr.png 含敏感信息，用完请立即删除。"
        ),
        epilog=(
            '运行方式：uv run --with "websockets>=13,<16" '
            '--with "segno>=1.6,<2" verify_helper.py scan-xxx.json'
        ),
    )
    parser.add_argument("bundle", help="服务端导出的凭证 JSON 路径")
    parser.add_argument(
        "--chrome", metavar="PATH", help="指定浏览器可执行文件（优先使用仓库的 CloakBrowser；Edge 可能被 reCAPTCHA 判为环境异常）"
    )
    parser.add_argument(
        "--no-open", action="store_true", help="只保存 qr.png，不自动打开"
    )
    parser.add_argument(
        "--keep-profile", action="store_true", help="保留临时浏览器 profile 便于排查"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=240.0,
        metavar="SECONDS",
        help="等待扫码与结果的超时秒数（默认 240）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        metavar="N",
        help="DevTools 调试端口（默认自动选择空闲端口）",
    )
    args = parser.parse_args()
    if args.timeout <= 0 or (args.port is not None and not 1 <= args.port <= 65535):
        print(
            "[错误] --timeout 必须大于 0，--port 必须在 1~65535 之间", file=sys.stderr
        )
        return 1
    try:
        return run(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

