"""Unified CLI entrypoint for local development and installed usage."""

from __future__ import annotations

import argparse
import os
import sys


def build_parser() -> argparse.ArgumentParser:
    from aistudio_api.config import settings

    parser = argparse.ArgumentParser(description="AI Studio API entrypoint")
    subparsers = parser.add_subparsers(dest="command")

    server_parser = subparsers.add_parser("server", help="启动 Gemini 兼容 API 服务")
    server_parser.add_argument("--port", type=int, default=settings.port)
    server_parser.add_argument(
        "--browser-port", type=int, default=settings.browser_port
    )
    server_parser.add_argument(
        "--ui-debug",
        action="store_true",
        help="启动纯离线 UI Debug 沙盒（禁止真实浏览器/账号/外网）",
    )

    return parser


def main():
    # Must be set before importing aistudio_api.config/app, because Windows
    # shells can mangle inline environment assignments in background runners.
    if "--ui-debug" in sys.argv:
        os.environ["AISTUDIO_UI_DEBUG"] = "1"

    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "ui_debug", False):
        os.environ["AISTUDIO_UI_DEBUG"] = "1"

    from aistudio_api.api.app import main as server_main

    port = getattr(args, "port", None)
    browser_port = getattr(args, "browser_port", None)

    from aistudio_api.config import settings

    port_val = port if port is not None else settings.port
    browser_port_val = (
        browser_port if browser_port is not None else settings.browser_port
    )

    sys.argv = [
        "aistudio-api-server",
        "--port",
        str(port_val),
        "--browser-port",
        str(browser_port_val),
    ]
    server_main()


if __name__ == "__main__":
    main()
