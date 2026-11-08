"""Owns a local ``waa-signer`` subprocess started by this process.

Rules:
* Only a daemon that this instance spawned is terminated on ``close``/atexit.
* Startup is bounded by ``startup_timeout_s`` and verified through ``/health``.
* Failure is explicit (``SignerUnavailableError``); there is no silent remote
  fallback and no duplicate process per endpoint.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from aistudio_api.infrastructure.utils.logger import get_logger
from aistudio_api.infrastructure.waa.signer_client import (
    SignerClient,
    SignerError,
    SignerUnavailableError,
)

log = get_logger("waa-daemon")

_PROJECT_ROOT = Path(__file__).resolve().parents[4]


def resolve_binary_path(binary_path: str) -> Path:
    """Resolve a configured signer binary, including the Windows ``.exe`` form."""
    path = Path(binary_path).expanduser()
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if (
        sys.platform.startswith("win")
        and path.suffix.lower() != ".exe"
        and not path.exists()
    ):
        candidate = path.with_name(path.name + ".exe")
        if candidate.exists():
            return candidate
    return path


class LocalSignerDaemon:
    """Manage one locally spawned signer process bound to ``endpoint``."""

    def __init__(
        self,
        *,
        endpoint: str,
        binary_path: str,
        state_dir: str,
        startup_timeout_s: float = 30.0,
        locale: str = "en-US",
        timezone: str = "America/Los_Angeles",
        max_workers: int = 8,
        idle_ttl_s: float = 900.0,
        proxy: str | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.binary_path = resolve_binary_path(binary_path)
        self.state_dir = Path(state_dir).expanduser()
        self.startup_timeout_s = max(1.0, float(startup_timeout_s))
        self.locale = locale
        self.timezone = timezone
        self.max_workers = max(1, int(max_workers))
        self.idle_ttl_s = max(1.0, float(idle_ttl_s))
        self.proxy = proxy
        self._proc: asyncio.subprocess.Process | None = None
        self._started_by_us = False
        self._atexit_registered = False

    @property
    def running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.returncode is None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    def _listen_address(self) -> str:
        parsed = urlparse(self.endpoint)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8081
        if ":" in host:
            host = f"[{host}]"
        return f"{host}:{port}"

    def _command(self) -> list[str]:
        command = [
            str(self.binary_path),
            "-listen",
            self._listen_address(),
            "-state-dir",
            str(self.state_dir),
            "-locale",
            self.locale,
            "-timezone",
            self.timezone,
            "-max-workers",
            str(self.max_workers),
            "-idle-ttl",
            f"{self.idle_ttl_s:.0f}s",
        ]
        if self.proxy:
            command.extend(["-proxy", self.proxy])
        return command

    async def start(self, *, health_client: SignerClient) -> None:
        """Spawn the signer and wait until an initial ``/health`` succeeds."""
        if self.running:
            return
        if not self.binary_path.is_file():
            raise SignerUnavailableError(f"waa-signer 二进制不存在: {self.binary_path}")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._command(),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=os.environ.copy(),
            )
        except OSError as exc:
            raise SignerUnavailableError(f"无法启动 waa-signer: {exc}") from exc
        self._started_by_us = True
        if not self._atexit_registered:
            atexit.register(self._terminate_sync)
            self._atexit_registered = True

        deadline = asyncio.get_running_loop().time() + self.startup_timeout_s
        last_error = "startup timeout"
        while asyncio.get_running_loop().time() < deadline:
            if self._proc.returncode is not None:
                last_error = f"waa-signer 提前退出 (code={self._proc.returncode})"
                break
            try:
                health = await health_client.health()
            except SignerError as exc:
                last_error = str(exc)
            else:
                if health.ok and health.ready:
                    log.info(
                        "[waa] signer 已启动: endpoint=%s pid=%s workers=%d/%d",
                        self.endpoint,
                        self._proc.pid,
                        health.workers,
                        health.max_workers,
                    )
                    return
                last_error = "waa-signer 尚未 ready"
            await asyncio.sleep(0.2)

        await self.close()
        raise SignerUnavailableError(f"waa-signer 启动失败: {last_error}")

    async def close(self) -> None:
        proc = self._proc
        self._proc = None
        if not self._started_by_us or proc is None:
            return
        self._started_by_us = False
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    return
                await proc.wait()
        log.info("[waa] 自有 signer 进程已关闭: endpoint=%s", self.endpoint)

    def _terminate_sync(self) -> None:
        proc = self._proc
        if proc is None or not self._started_by_us:
            return
        try:
            if proc.returncode is None:
                proc.terminate()
        except Exception:
            pass


__all__ = ["LocalSignerDaemon", "resolve_binary_path"]
