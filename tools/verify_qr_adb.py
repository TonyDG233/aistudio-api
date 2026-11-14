#!/usr/bin/env python3
"""Island（炼妖壶）工作资料 + ADB 实机 verify-account 全链路工具。

**【实机验证结论留档：Island 工作资料绕过设备验证尝试已失败】**

经真机控制变量实测，通过 Android ``FLAG_MANAGED_PROFILE``（Island / 壶中界 User 10）
隔离 ANDROID_ID 与独立 KeyMint 密钥以突破 Google AI Studio ``verify-account``
（``recaptcha.net/qr/``）单设备验证限额的方案**已证实无效**：Google 的
``recaptcha.net/qr/``（DroidGuard / Play Integrity）会检测并直接拒绝第三方受管工作资料
（Managed Profile）沙盒环境（返回 ``We couldn't verify your account with this device...
or doesn't meet device security requirements``），而切回同一台物理手机的原生主空间
（User 0）则可正常通过验证。

因此本脚本无法用于重置或绕过单台物理手机的验证配额限制，仅保留作为剪贴板二维码离线解码
与主空间（``--allow-main-user``）免摄像头 ADB 辅助推送工具留底。

一条命令完成：递交二维码（图片 / 剪贴板 / URL）→ 连接 ADB → 对比并抓取主空间
（User 0）与 Island 工作资料（User 10）的 ANDROID_ID / GSF ID → 安全校验后把
二维码挑战 URL 注入目标空间 → 实时监控前台窗口、GMS 进程空间归属与 logcat
验证事件 → 输出《实机验证设备身份与完成报告》。

用法示例::

    uv run python tools/verify_qr_adb.py devices
    uv run python tools/verify_qr_adb.py connect --pair 192.168.1.100:41234 --code 123456
    uv run python tools/verify_qr_adb.py connect --pair 10.0.0.1:41234 \
        --host 192.168.1.100 --code 123456 --connect 41235
    uv run python tools/verify_qr_adb.py install-island
    uv run python tools/verify_qr_adb.py setup-island
    uv run python tools/verify_qr_adb.py inspect-ids
    uv run python tools/verify_qr_adb.py verify --qr-image qr.png
    uv run python tools/verify_qr_adb.py verify --clipboard --timeout 120

设计要点：
- 二维码解码零 DLL 依赖：内置纯 Python 解码器（PNG/BMP/DIB 解码 + 定位图形检测 +
  Reed-Solomon 纠错），环境里存在 cv2 / zxing-cpp / pyzbar 时自动优先使用它们。
- 所有 adb 调用都经过 ``AdbClient``，可注入假 runner 做完全离线单元测试。
- 安全校验：目标空间与主空间 ANDROID_ID 相同或为空时立即中止，绝不误用 User 0。
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ISLAND_APK_DIR = PROJECT_ROOT / "data" / "tools" / "island"
PKG_GMS = "com.google.android.gms"
PKG_GMS_UNSTABLE = "com.google.android.gms.unstable"
PKG_GSF = "com.google.android.gsf"
PKG_VENDING = "com.android.vending"
PKG_GMS_SUPERVISION = "com.google.android.gms.supervision"
VERIFY_PACKAGES: tuple[str, ...] = (
    PKG_GMS,
    PKG_GMS_UNSTABLE,
    "com.android.chrome",
    "com.google.android.webview",
    "com.google.android.googlequicksearchbox",
    "com.google.android.apps.chrome",
    "org.chromium.webview_shell",
)
ISLAND_REQUIRED_CORE: tuple[str, ...] = (PKG_GMS, PKG_GSF, PKG_VENDING)
ISLAND_OPTIONAL_CORE: tuple[str, ...] = (PKG_GMS_SUPERVISION,)
BROWSER_PREFERENCES: tuple[str, ...] = (
    "com.android.chrome",
    "com.microsoft.emmx",
    "org.mozilla.firefox",
    "mark.via",
    "com.heytap.browser",
    "com.google.android.apps.chrome",
)
BROWSER_ROLE = "android.app.role.BROWSER"
ISLAND_PROBE_URL = "https://www.google.com/recaptcha/enterprise"
PARALLEL_SPACE_UID_OFFSET = 10_000
ANDROID_USER_UID_RANGE = 100_000
ADB_EXE_NAME = "adb.exe" if os.name == "nt" else "adb"
APK_MAGIC = b"PK\x03\x04"
ADB_PAIR_MIN_PLATFORM_VERSION = 30
PHONE_WIFI_IP_EXAMPLE = "192.168.1.100"
VPN_IP_PREFIXES: tuple[str, ...] = ("10.", "198.18.", "198.19.")
VPN_172_RE = re.compile(r"^172\.(1[6-9]|2\d|3[01])\.")
ADB_CLIENT_VERSION_RE = re.compile(r"Android Debug Bridge version (\d+)\.(\d+)\.(\d+)")
ADB_PLATFORM_VERSION_RE = re.compile(r"^\s*Version (\d+)\.(\d+)\.(\d+)", re.MULTILINE)
ADB_FAILURE_MARKERS: tuple[str, ...] = (
    "failed",
    "error",
    "cannot",
    "unable",
    "unknown command",
    "not found",
    "offline",
    "refused",
)


class ToolError(RuntimeError):
    """工具通用错误。"""


class AdbError(ToolError):
    """adb 定位 / 调用失败。"""


class QrDecodeError(ToolError):
    """二维码解码失败。"""


class ImageDecodeError(QrDecodeError):
    """图像（PNG/BMP/DIB）解码失败。"""


class SafetyError(ToolError):
    """空间身份隔离校验失败，必须中止。"""


@dataclass(frozen=True)
class CommandResult:
    """一条外部命令的执行结果。"""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return self.stdout if self.stdout.strip() else self.stderr


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str

    @property
    def usable(self) -> bool:
        return self.state == "device"


@dataclass(frozen=True)
class AndroidUser:
    """``pm list users`` 解析出的一条用户记录。"""

    user_id: int
    name: str
    flags: str
    raw_flags: int | None

    @property
    def is_primary(self) -> bool:
        return self.user_id == 0

    @property
    def is_work_profile(self) -> bool:
        if self.is_primary:
            return False
        if self.raw_flags is not None and self.raw_flags & 0x10:
            return True
        return bool(
            re.search(r"工作|work|island|炼妖壶|managed", self.name, re.IGNORECASE)
        )

    @property
    def label(self) -> str:
        if self.is_primary:
            kind = "主空间"
        elif self.is_work_profile:
            kind = "工作资料"
        else:
            kind = "普通用户"
        return f"User {self.user_id} ({self.name}, {kind})"


@dataclass(frozen=True)
class GsfId:
    """GSF (Google Services Framework) Android ID。"""

    raw: str
    value: int

    @property
    def decimal(self) -> str:
        return str(self.value)

    @property
    def hex(self) -> str:
        return hex(self.value)

    def __str__(self) -> str:
        return f"{self.decimal} ({self.hex})"


@dataclass(frozen=True)
class ProcessEntry:
    """``ps -A`` 的一行。"""

    user: str
    pid: int
    ppid: int
    name: str


@dataclass(frozen=True)
class WindowFocus:
    """``dumpsys window`` 中的前台焦点信息（component 不含 ``u<uid>`` 前缀）。"""

    current_focus: str | None = None
    focused_app: str | None = None
    resumed_activity: str | None = None
    user_id: int | None = None

    @property
    def component(self) -> str | None:
        return self.current_focus or self.resumed_activity or self.focused_app

    @property
    def package(self) -> str | None:
        comp = self.component
        if not comp:
            return None
        match = WINDOW_COMPONENT_RE.match(comp)
        if match:
            return match.group("pkg")
        if "/" in comp:
            return comp.split("/", 1)[0]
        return comp

    @property
    def activity(self) -> str | None:
        comp = self.component
        if not comp:
            return None
        match = WINDOW_COMPONENT_RE.match(comp)
        if match:
            return match.group("activity")
        if "/" in comp:
            return comp.split("/", 1)[1]
        return None


@dataclass(frozen=True)
class SpaceIdentity:
    """某个 Android 用户空间的设备身份快照。"""

    user_id: int
    user_name: str
    android_id: str
    gsf: GsfId | None
    gms_installed: bool
    gms_version: str | None
    is_work_profile: bool

    @property
    def android_id_valid(self) -> bool:
        return bool(ANDROID_ID_RE.fullmatch(self.android_id))

    def same_android_id(self, other: SpaceIdentity) -> bool:
        if not (self.android_id_valid and other.android_id_valid):
            return False
        return self.android_id.lower() == other.android_id.lower()

    def same_gsf_id(self, other: SpaceIdentity) -> bool:
        if self.gsf is None or other.gsf is None:
            return False
        return self.gsf.value == other.gsf.value


# ---------------------------------------------------------------------------
# 外部命令执行
# ---------------------------------------------------------------------------


def default_runner(args: Sequence[str], timeout: float) -> CommandResult:
    """使用 subprocess 执行命令（默认 runner，可被测试替换）。"""
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        completed = subprocess.run(list(args), **kwargs)
    except FileNotFoundError as exc:
        raise AdbError(f"命令不存在: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"命令超时 ({timeout}s): {' '.join(args)}") from exc
    return CommandResult(
        args=tuple(args),
        returncode=int(completed.returncode),
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def _read_java_properties(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    props: dict[str, str] = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line[0] in "#!":
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        props[key.strip()] = _unescape_java_property(value.strip())
    return props


def _unescape_java_property(value: str) -> str:
    out: list[str] = []
    idx = 0
    while idx < len(value):
        char = value[idx]
        if char == "\\" and idx + 1 < len(value):
            nxt = value[idx + 1]
            if nxt in ("\\", ":"):
                out.append(nxt)
                idx += 2
                continue
            if nxt == "u" and idx + 5 < len(value):
                try:
                    out.append(chr(int(value[idx + 2 : idx + 6], 16)))
                except ValueError:
                    out.append(nxt)
                idx += 6
                continue
            out.append(nxt)
            idx += 2
            continue
        out.append(char)
        idx += 1
    return "".join(out)


def _sdk_roots() -> list[Path]:
    """探测常见 Android SDK 根目录（环境变量 → 项目相对 → 系统标准位置）。"""
    roots: list[Path] = []
    for env_name in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        value = os.environ.get(env_name)
        if value and value.strip():
            roots.append(Path(value.strip().strip('"')).expanduser())
    roots.append(PROJECT_ROOT / "android-sdk")
    roots.append(PROJECT_ROOT / "android" / "sdk")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        roots.append(Path(local_app_data) / "Android" / "Sdk")
    home = Path.home()
    roots.append(home / "Android" / "Sdk")
    roots.append(home / "Library" / "Android" / "sdk")
    return roots


def _adb_candidates(explicit: str | None = None) -> list[Path]:
    """按优先级生成 adb 候选路径（项目内置 → SDK → PATH 兜底）。"""
    candidates: list[Path] = []

    def _add(value: str | Path | None) -> None:
        if not value:
            return
        text = str(value).strip().strip('"')
        if not text:
            return
        path = Path(text).expanduser()
        if path not in candidates:
            candidates.append(path)

    _add(explicit)
    _add(os.environ.get("ADB_PATH"))
    _add(PROJECT_ROOT / "data" / "tools" / "platform-tools" / ADB_EXE_NAME)
    sdk_dir = _read_java_properties(PROJECT_ROOT / "android" / "local.properties").get(
        "sdk.dir"
    )
    if sdk_dir:
        _add(Path(sdk_dir) / "platform-tools" / ADB_EXE_NAME)
    for root in _sdk_roots():
        _add(root / "platform-tools" / ADB_EXE_NAME)
    _add(shutil.which("adb"))
    return candidates


def parse_adb_platform_version(text: str) -> int | None:
    """解析 ``adb version`` 输出中的 platform-tools 主版本号（识别不了返回 None）。"""
    match = ADB_PLATFORM_VERSION_RE.search(text)
    if match:
        return int(match.group(1))
    match = ADB_CLIENT_VERSION_RE.search(text)
    if not match:
        return None
    major, minor, patch = (int(item) for item in match.groups())
    if (major, minor) == (1, 0):
        return ADB_PAIR_MIN_PLATFORM_VERSION if patch >= 40 else 0
    return major


def _adb_supports_pair(adb_path: str) -> bool:
    """探测 adb 是否支持 ``pair``（platform-tools >= 30）；无法探测时按支持处理。"""
    try:
        result = default_runner([str(adb_path), "version"], 10.0)
    except (AdbError, OSError):
        return True
    version = parse_adb_platform_version(result.output)
    if version is None:
        return True
    return version >= ADB_PAIR_MIN_PLATFORM_VERSION


def resolve_adb_path(explicit: str | None = None) -> Path:
    """查找 adb：--adb-path → ADB_PATH → 项目内置 → local.properties → SDK → PATH。

    自动跳过 platform-tools < 30 的旧版 adb（不支持 ``pair``），优先返回支持
    无线配对的新版；探测不到版本信息时保持兼容（按支持处理）。
    """
    candidates = _adb_candidates(explicit)
    existing: list[Path] = []
    for candidate in candidates:
        try:
            if candidate.is_file():
                existing.append(candidate)
        except OSError:
            continue
    for candidate in existing:
        if _adb_supports_pair(str(candidate)):
            return candidate
    if existing:
        return existing[0]
    searched = ", ".join(str(item) for item in candidates) or "(无)"
    raise AdbError(
        "未找到 adb 可执行文件。请用 --adb-path 指定，或设置 ADB_PATH 环境变量。"
        f"已查找: {searched}"
    )


def normalize_adb_address(value: str | None, host: str | None = None) -> str | None:
    """把 ``--pair/--connect`` 的地址归一成 ``host:port``。

    - 只给端口号（``41234``）时，用 ``--host`` 拼成 ``192.168.1.100:41234``；
    - 给了 ``--host`` 时，把原有的 VPN / 旧 IP 替换掉（``10.0.0.1:41234`` →
      ``192.168.1.100:41234``）；
    - 没给 ``--host`` 时原样保留，只给端口号则报错提示补 ``--host``。
    """
    if not value:
        return None
    text = value.strip().strip('"')
    if not text:
        return None
    host = (host or "").strip()
    if text.isdigit():
        if not host:
            raise ToolError(
                f"地址 {text} 只是端口号，请加 --host <手机局域网IP>"
                f"（如 --host {PHONE_WIFI_IP_EXAMPLE}）"
            )
        return f"{host}:{text}"
    if ":" in text:
        ip_part, _sep, port = text.rpartition(":")
        if host and ip_part:
            return f"{host}:{port}"
        return text
    if host:
        return f"{host}:{text}"
    return text


def _address_host(address: str) -> str | None:
    """取地址中的 IP 部分（不带端口时返回原值）。"""
    if address.startswith("["):
        end = address.find("]")
        return address[1:end] if end > 0 else None
    if ":" not in address:
        return address
    ip_part, _sep, _port = address.rpartition(":")
    return ip_part or None


def _looks_like_vpn_ip(ip: str) -> bool:
    """常见 VPN/代理虚拟网卡网段：10/8、172.16/12、198.18/15。"""
    return ip.startswith(VPN_IP_PREFIXES) or bool(VPN_172_RE.match(ip))


def _local_lan_ip() -> str | None:
    """本机 192.168.x.x 局域网地址（拿不到返回 None）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
    except OSError:
        return None
    return ip if ip.startswith("192.168.") else None


def vpn_host_hint(address: str) -> str | None:
    """目标疑似手机 VPN 虚拟网卡 IP 且本机在 192.168 网段时，给出 --host 提示。"""
    ip = _address_host(address)
    if not ip or not _looks_like_vpn_ip(ip):
        return None
    lan = _local_lan_ip()
    if lan is None:
        return None
    return (
        f"检测到目标地址 {ip} 可能为手机 VPN 虚拟网卡 IP，请加上 --host "
        f"{PHONE_WIFI_IP_EXAMPLE}（手机真实 Wi-Fi IP）重试，无需关闭手机梯子！"
        f"（本机局域网地址 {lan}）"
    )


def _adb_action_failed(output: str) -> bool:
    """判断 pair/connect 输出是否为失败（避免把 ``already connected`` 误判成失败）。"""
    text = output.strip().lower()
    return bool(text) and any(marker in text for marker in ADB_FAILURE_MARKERS)


class AdbClient:
    """轻量 adb 客户端：所有调用都可注入 runner，便于离线测试。"""

    def __init__(
        self,
        adb_path: Path | str | None = None,
        *,
        serial: str | None = None,
        runner: Callable[[Sequence[str], float], CommandResult] | None = None,
        timeout: float = 45.0,
    ) -> None:
        self.adb_path = Path(adb_path) if adb_path else resolve_adb_path()
        self.serial = serial
        self.timeout = timeout
        self._runner = runner or default_runner
        self._serial_lookup_done = False

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: float | None = None,
        check: bool = False,
    ) -> CommandResult:
        argv: list[str] = [str(self.adb_path)]
        if self.serial:
            argv.extend(["-s", self.serial])
        argv.extend(str(item) for item in args)
        result = self._runner(tuple(argv), float(timeout or self.timeout))
        if check and not result.ok:
            raise AdbError(
                f"adb 命令失败 ({result.returncode}): {' '.join(argv)}\n"
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    def _hardware_serial(self, serial: str) -> str:
        """读取某个传输通道背后的物理手机序列号（``ro.serialno``）。"""
        result = self._runner(
            (str(self.adb_path), "-s", serial, "shell", "getprop ro.serialno"),
            float(self.timeout),
        )
        return result.stdout.strip() if result.ok else ""

    def _auto_select_serial(self) -> str:
        """多个在线通道属于同一台手机时自动选一个，优先 ``ip:port``。"""
        devices = [item for item in self.list_devices() if item.usable]
        if not devices:
            raise AdbError(
                "未发现可用的 ADB 设备。请先 adb connect <ip:port>"
                "（或检查 USB 调试授权）。"
            )
        if len(devices) == 1:
            return devices[0].serial
        groups: dict[str, list[str]] = {}
        for device in devices:
            identity = self._hardware_serial(device.serial) or (
                f"transport:{device.serial}"
            )
            groups.setdefault(identity, []).append(device.serial)
        if len(groups) > 1:
            summary = "; ".join(
                f"{identity}: {', '.join(serials)}"
                for identity, serials in groups.items()
            )
            raise AdbError("检测到多台不同的 ADB 设备，请用 --serial 指定：" + summary)
        serials = next(iter(groups.values()))
        return next(
            (item for item in serials if ADB_IP_SERIAL_RE.match(item)), serials[0]
        )

    def _ensure_serial(self) -> None:
        """未显式给 ``--serial`` 时，仅在需要访问设备前的最后一刻自动选择。"""
        if self.serial is not None or self._serial_lookup_done:
            return
        self._serial_lookup_done = True
        self.serial = self._auto_select_serial()

    def shell(self, command: str, *, timeout: float | None = None) -> CommandResult:
        """执行 adb shell，整条 command 作为单个参数交给设备端 shell 解析。"""
        self._ensure_serial()
        return self.run(["shell", command], timeout=timeout)

    def shell_text(self, command: str, *, timeout: float | None = None) -> str:
        result = self.shell(command, timeout=timeout)
        if not result.ok:
            raise AdbError(
                f"adb shell 失败 ({result.returncode}): {command}\n"
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout

    @staticmethod
    def _shell_quote(value: str) -> str:
        """把任意值包成设备端 shell 单引号字面量（内部单引号转义为 ``'\\''``）。"""
        return "'" + value.replace("'", "'\\''") + "'"

    def list_devices(self) -> list[AdbDevice]:
        return parse_adb_devices(self.run(["devices"], check=True).stdout)

    def pair(self, host_port: str, code: str) -> str:
        result = self.run(["pair", host_port, code], timeout=max(self.timeout, 90.0))
        return result.output.strip()

    def connect(self, host_port: str) -> str:
        result = self.run(["connect", host_port], timeout=max(self.timeout, 60.0))
        return result.output.strip()

    def install(self, apk: Path, *, reinstall: bool = True) -> str:
        self._ensure_serial()
        args = ["install", "-r"] if reinstall else ["install"]
        args.append(str(apk))
        result = self.run(args, timeout=max(self.timeout, 300.0))
        return result.output.strip()

    def list_users(self) -> list[AndroidUser]:
        return parse_pm_list_users(self.shell_text("pm list users"))

    def android_id(self, user_id: int) -> str:
        """读取指定用户空间的 ANDROID_ID；主空间受保护时自动回退其他只读通道。"""
        result = self.shell(f"settings --user {user_id} get secure android_id")
        value = parse_android_id(result.stdout)
        if value or user_id != 0:
            return value
        fallbacks: tuple[tuple[str, Callable[[str], str]], ...] = (
            ("settings get secure android_id", parse_android_id),
            (
                "content query --uri content://settings/secure --where "
                + self._shell_quote("name='android_id'"),
                parse_android_id_value_field,
            ),
        )
        for command, parser in fallbacks:
            fallback = self.shell(command)
            if not fallback.ok:
                continue
            value = parser(fallback.stdout)
            if value:
                return value
        return ""

    def gsf_id(self, user_id: int) -> GsfId | None:
        queries = (
            (
                "content query --user {uid} --uri content://com.google.android.gsf.gservices"
                " --projection value --where \"name='android_id'\""
            ),
            (
                "content query --user {uid}"
                " --uri content://com.google.android.gsf.gservices/prefix"
                " --projection android_id"
            ),
        )
        for template in queries:
            result = self.shell(template.format(uid=user_id))
            if not result.ok:
                continue
            parsed = parse_gsf_id(result.stdout)
            if parsed is not None:
                return parsed
        return None

    def gms_version(self, user_id: int) -> tuple[bool, str | None]:
        packages = self.shell(f"pm list packages --user {user_id}")
        installed = any(
            line.strip() == f"package:{PKG_GMS}"
            for line in packages.stdout.splitlines()
        )
        if not installed:
            return (False, None)
        dump = self.shell(f"dumpsys package {PKG_GMS} --user {user_id}")
        match = re.search(r"versionName=(\S+)", dump.stdout)
        return (True, match.group(1) if match else None)

    def window_focus(self) -> WindowFocus:
        return parse_window_focus(self.shell_text("dumpsys window"))

    def ps_entries(self) -> list[ProcessEntry]:
        return parse_ps_a(self.shell_text("ps -A"))

    def logcat_clear(self) -> None:
        self.shell("logcat -c")

    def logcat_dump(self) -> str:
        return self.shell("logcat -d -v threadtime").stdout

    def resolve_activity(self, url: str, user_id: int) -> str | None:
        command = (
            "cmd package resolve-activity --brief"
            " -a android.intent.action.VIEW -c android.intent.category.BROWSABLE"
            f" -d {self._shell_quote(url)} --user {user_id}"
        )
        return parse_resolved_component(self.shell(command).stdout)

    def start_view(self, url: str, user_id: int) -> CommandResult:
        command = (
            f"am start --user {user_id} -W"
            " -a android.intent.action.VIEW -c android.intent.category.BROWSABLE"
            f" -d {self._shell_quote(url)}"
        )
        return self.shell(command, timeout=max(self.timeout, 90.0))

    def list_package_names(
        self, user_id: int, *, enabled_only: bool = False
    ) -> list[str]:
        """列出指定用户空间已安装（或仅已启用）的包名。"""
        flag = " -e" if enabled_only else ""
        return parse_package_list(
            self.shell(f"pm list packages --user {user_id}{flag}").stdout
        )

    def package_uids(self, user_id: int) -> dict[str, int]:
        """``pm list packages -U``：包名 → 该用户空间内的完整 UID。"""
        return parse_package_uids(
            self.shell(f"pm list packages --user {user_id} -U").stdout
        )

    def active_vpn_packages(self) -> list[str]:
        """``dumpsys vpn_management`` 中当前活跃 VPN 的应用包名。"""
        return parse_active_vpn_packages(self.shell("dumpsys vpn_management").stdout)

    def vpn_uid_ranges(self) -> list[tuple[int, int]]:
        """当前 VPN NetworkCapabilities 里的 UID 允许区间。"""
        return parse_vpn_uid_ranges(self.shell("dumpsys vpn_management").stdout)

    def install_existing(self, package: str, user_id: int) -> CommandResult:
        """为指定用户空间映射（安装）主空间已有的包。"""
        return self.shell(f"pm install-existing --user {user_id} {package}")

    def enable_package(self, package: str, user_id: int) -> CommandResult:
        return self.shell(f"pm enable --user {user_id} {package}")

    def unhide_package(self, package: str, user_id: int) -> CommandResult:
        return self.shell(f"cmd package unhide --user {user_id} {package}")

    def grant_browser_role(self, package: str, user_id: int) -> CommandResult:
        return self.shell(
            f"cmd role add-role-holder --user {user_id} {BROWSER_ROLE} {package}"
        )

    def browser_role_holders(self, user_id: int) -> list[str]:
        return parse_plain_package_names(
            self.shell(
                f"cmd role get-role-holders --user {user_id} {BROWSER_ROLE}"
            ).stdout
        )


# ---------------------------------------------------------------------------
# 文本解析
# ---------------------------------------------------------------------------

USER_INFO_RE = re.compile(
    r"UserInfo\{(?P<uid>\d+):(?P<name>[^:}]*):(?P<flags>[^}]*)\}(?P<tail>.*)"
)
WINDOW_COMPONENT_RE = re.compile(
    r"^(?:u(?P<user>\d+)\s+)?(?P<pkg>[A-Za-z0-9_.]+)/(?P<activity>[A-Za-z0-9_.$]+)$"
)
PS_LINE_RE = re.compile(
    r"^(?P<user>\S+)\s+(?P<pid>\d+)\s+(?P<ppid>\d+)(?:\s+(?P<rest>.*))?$"
)
ANDROID_ID_RE = re.compile(r"^[0-9a-fA-F]{8,16}$")
ADB_IP_SERIAL_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}:\d+$")
RESOLVED_COMPONENT_RE = re.compile(r"^((?:u\d+\s+)?[A-Za-z0-9_.]+/[A-Za-z0-9_.$]+)$")
PACKAGE_LINE_RE = re.compile(r"^package:([A-Za-z0-9_.]+)")
PACKAGE_UID_LINE_RE = re.compile(
    r"^package:(?P<pkg>[A-Za-z0-9_.]+)\s+uid:(?P<uid>\d+)$"
)
ACTIVE_VPN_RE = re.compile(r"Active package name:\s*(?P<pkg>[A-Za-z0-9_.]+)")
VPN_UIDS_RE = re.compile(r"\bUids:\s*<\{([^}]*)\}>")
UID_RANGE_RE = re.compile(r"^(?P<low>\d+)(?:-(?P<high>\d+))?$")
PACKAGE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")
RESOLVER_COMPONENT = "android/com.android.internal.app.ResolverActivity"

VERIFY_LOG_RE = re.compile(
    r"Recaptcha|DroidGuard|PlayIntegrity|Play Integrity|ActivityTaskManager"
    r"|ActivityManager|Displayed|Finishing|verify",
    re.IGNORECASE,
)


def parse_adb_devices(text: str) -> list[AdbDevice]:
    devices: list[AdbDevice] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(("List of devices", "*")):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        devices.append(AdbDevice(serial=parts[0], state=parts[1]))
    return devices


def _parse_flag_value(raw: str) -> int | None:
    text = raw.strip().lower()
    if not text:
        return None
    try:
        return int(text, 16)
    except ValueError:
        return None


def parse_pm_list_users(text: str) -> list[AndroidUser]:
    """解析 ``pm list users`` 输出（含 Island 工作资料用户）。"""
    users: list[AndroidUser] = []
    for raw_line in text.splitlines():
        match = USER_INFO_RE.search(raw_line)
        if not match:
            continue
        flags = match.group("flags").strip()
        name = match.group("name").strip()
        tail = match.group("tail") or ""
        if not name and "running" in tail:
            name = "running"
        users.append(
            AndroidUser(
                user_id=int(match.group("uid")),
                name=name,
                flags=flags,
                raw_flags=_parse_flag_value(flags),
            )
        )
    return users


def parse_android_id(text: str) -> str:
    """解析 ``settings get secure android_id`` 输出；空值 / 占位值 / 非法串视为无效。

    ColorOS 等系统生成的 ANDROID_ID 是 ``Long.toHexString`` 结果，可能不足 16 位
    （实测 OPPO ColorOS Android 15 主空间为 15 位），因此接受 8~16 位十六进制。
    """
    for raw_line in text.splitlines():
        candidate = raw_line.strip()
        if not candidate:
            continue
        if candidate.lower() in {"null", "unknown", "0"}:
            return ""
        if not ANDROID_ID_RE.fullmatch(candidate):
            return ""
        return candidate.lower()
    return ""


def parse_android_id_value_field(text: str) -> str:
    """解析 ``content query content://settings/secure`` 输出里的 ``value=`` / ``android_id=``。"""
    match = re.search(r"\b(?:value|android_id)=([0-9a-fA-F]{8,16})\b", text)
    return parse_android_id(match.group(1)) if match else ""


def parse_gsf_id(text: str) -> GsfId | None:
    """解析 gservices content query 输出里的 android_id / value 字段。"""
    for key in ("android_id", "value"):
        match = re.search(rf"\b{key}=([^\s,\]]+)", text)
        if not match:
            continue
        raw = match.group(1).strip().strip("'\"")
        if not raw or raw.lower() in {"null", "none"}:
            continue
        value = _coerce_gsf_value(raw)
        if value is not None:
            return GsfId(raw=raw, value=value)
    return None


def _coerce_gsf_value(raw: str) -> int | None:
    text = raw.strip()
    if text.lower().startswith("0x"):
        try:
            return int(text, 16)
        except ValueError:
            return None
    if re.fullmatch(r"\d+", text):
        return int(text, 10)
    if re.fullmatch(r"[0-9a-fA-F]+", text) and len(text) >= 8:
        return int(text, 16)
    return None


def parse_ps_a(text: str) -> list[ProcessEntry]:
    """解析 ``ps -A``（USER PID PPID ... NAME 多种列布局）。"""
    entries: list[ProcessEntry] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(("USER", "PID")):
            continue
        match = PS_LINE_RE.match(stripped)
        if not match:
            continue
        rest = (match.group("rest") or "").split()
        if not rest:
            continue
        entries.append(
            ProcessEntry(
                user=match.group("user"),
                pid=int(match.group("pid")),
                ppid=int(match.group("ppid")),
                name=rest[-1],
            )
        )
    return entries


def parse_window_focus(text: str) -> WindowFocus:
    """从 ``dumpsys window`` / ``dumpsys activity`` 输出解析前台焦点。"""
    current: tuple[str, int | None] | None = None
    focused_app: tuple[str, int | None] | None = None
    resumed: tuple[str, int | None] | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if current is None:
            for marker in ("mCurrentFocus=", "mFocusedWindow="):
                if marker in stripped:
                    current = _extract_window_component(stripped.split(marker, 1)[1])
                    break
        if focused_app is None and "mFocusedApp=" in stripped:
            focused_app = _extract_window_component(
                stripped.split("mFocusedApp=", 1)[1]
            )
        if resumed is None and "mResumedActivity=" in stripped:
            resumed = _extract_window_component(
                stripped.split("mResumedActivity=", 1)[1]
            )
        if resumed is None and "ResumedActivity" in stripped and "=" in stripped:
            resumed = _extract_window_component(stripped.split("=", 1)[1])
    chosen = current or resumed or focused_app
    return WindowFocus(
        current_focus=current[0] if current else None,
        focused_app=focused_app[0] if focused_app else None,
        resumed_activity=resumed[0] if resumed else None,
        user_id=chosen[1] if chosen else None,
    )


def _extract_window_component(text: str) -> tuple[str, int | None] | None:
    cleaned = text.strip().rstrip("}")
    user_match = re.search(
        r"\bu(?P<user>\d+)\s+(?P<component>[A-Za-z0-9_.]+/[A-Za-z0-9_.$]+)", cleaned
    )
    if user_match:
        return (user_match.group("component"), int(user_match.group("user")))
    for token in cleaned.split():
        candidate = token.strip("{}")
        if "/" in candidate and WINDOW_COMPONENT_RE.match(candidate):
            return (candidate, None)
    match = WINDOW_COMPONENT_RE.search(cleaned)
    if match:
        return (match.group(0), None)
    return None


def parse_resolved_component(text: str) -> str | None:
    """解析 ``cmd package resolve-activity --brief`` 的组件行。"""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if "=" in stripped or " " in stripped:
            continue
        match = RESOLVED_COMPONENT_RE.match(stripped)
        if match:
            return match.group(1)
    return None


def parse_package_list(text: str) -> list[str]:
    """解析 ``pm list packages`` / ``cmd role get-role-holders`` 的包名行。"""
    names: dict[str, None] = {}
    for raw_line in text.splitlines():
        match = PACKAGE_LINE_RE.match(raw_line.strip())
        if match:
            names.setdefault(match.group(1), None)
    return list(names)


def parse_package_uids(text: str) -> dict[str, int]:
    """解析 ``pm list packages -U`` 的 ``package:<pkg> uid:<n>`` 行。"""
    uids: dict[str, int] = {}
    for raw_line in text.splitlines():
        match = PACKAGE_UID_LINE_RE.match(raw_line.strip())
        if match:
            uids[match.group("pkg")] = int(match.group("uid"))
    return uids


def parse_plain_package_names(text: str) -> list[str]:
    """解析只输出包名裸列表的命令（如 ``cmd role get-role-holders``）。"""
    names: dict[str, None] = {}
    for raw_line in text.splitlines():
        token = raw_line.strip()
        if PACKAGE_NAME_RE.match(token):
            names.setdefault(token, None)
    return list(names)


def parse_active_vpn_packages(text: str) -> list[str]:
    """解析 ``dumpsys vpn_management`` 的活跃 VPN 应用包名（去重保序）。"""
    names: dict[str, None] = {}
    for raw_line in text.splitlines():
        match = ACTIVE_VPN_RE.search(raw_line)
        if match:
            names.setdefault(match.group("pkg"), None)
    return list(names)


def parse_vpn_uid_ranges(text: str) -> list[tuple[int, int]]:
    """解析 VPN ``NetworkCapabilities`` 里的 ``Uids: <{a-b, c}>`` 区间。"""
    ranges: list[tuple[int, int]] = []
    for match in VPN_UIDS_RE.finditer(text):
        for item in match.group(1).split(","):
            token = item.strip()
            if not token:
                continue
            parsed = UID_RANGE_RE.match(token)
            if parsed is None:
                continue
            low = int(parsed.group("low"))
            high = int(parsed.group("high") or low)
            ranges.append((low, high))
    return ranges


def parallel_uid_candidates(uid: int) -> tuple[int, ...]:
    """VPN 白名单里可能出现的 UID 形态。

    OPPO ColorOS 的壶中界（User 10 平行空间）在 VPN UID 白名单里以
    ``appId + 10000`` 编码出现（实测 ``1010370`` 对应区间 ``20370``），
    因此同时返回标准完整 UID 与平行空间编码两种候选。
    """
    if uid >= ANDROID_USER_UID_RANGE:
        return (uid, uid % ANDROID_USER_UID_RANGE + PARALLEL_SPACE_UID_OFFSET)
    return (uid,)


def uid_in_ranges(uid: int, ranges: Sequence[tuple[int, int]]) -> bool:
    """UID（含平行空间编码）是否落在任一个 VPN 放行区间内。"""
    return any(
        low <= candidate <= high
        for candidate in parallel_uid_candidates(uid)
        for low, high in ranges
    )


def browser_component_ready(component: str | None) -> bool:
    """``resolve-activity`` 是否已解析到具体浏览器（而非系统选择器）。"""
    return bool(component) and component != RESOLVER_COMPONENT


def user_prefix(user_id: int) -> str:
    return f"u{user_id}_"


def process_belongs_to_user(entry: ProcessEntry, user_id: int) -> bool:
    return entry.user.startswith(user_prefix(user_id)) or entry.user == f"u{user_id}"


def find_user_processes(
    entries: Iterable[ProcessEntry],
    user_id: int,
    *,
    names: Sequence[str] | None = None,
) -> list[ProcessEntry]:
    """找出属于指定用户空间的进程（``u<uid>_a*`` 归属证据）。"""
    wanted = tuple(names) if names else ()
    hits: list[ProcessEntry] = []
    for entry in entries:
        if not process_belongs_to_user(entry, user_id):
            continue
        if wanted and not any(entry.name.startswith(name) for name in wanted):
            continue
        hits.append(entry)
    return hits


def extract_verification_events(logcat: str, *, limit: int = 40) -> list[str]:
    """从 logcat 中筛出验证相关关键事件行（去重，保留最近 limit 条）。"""
    events: list[str] = []
    seen: set[str] = set()
    for raw_line in logcat.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or not VERIFY_LOG_RE.search(line):
            continue
        key = line.split(": ", 1)[-1]
        if key in seen:
            continue
        seen.add(key)
        events.append(line)
    return events[-limit:]


# ---------------------------------------------------------------------------
# 空间身份查询
# ---------------------------------------------------------------------------


def read_space_identity(adb: AdbClient, user: AndroidUser) -> SpaceIdentity:
    installed, version = adb.gms_version(user.user_id)
    return SpaceIdentity(
        user_id=user.user_id,
        user_name=user.name,
        android_id=adb.android_id(user.user_id),
        gsf=adb.gsf_id(user.user_id),
        gms_installed=installed,
        gms_version=version,
        is_work_profile=user.is_work_profile,
    )


def select_work_profile(
    users: Sequence[AndroidUser],
    *,
    requested_user: int | None = None,
    allow_main_user: bool = False,
) -> AndroidUser:
    """选择目标空间；默认只允许 Island 工作资料，绝不静默落到 User 0。"""
    by_id = {user.user_id: user for user in users}
    if requested_user is not None:
        target = by_id.get(requested_user)
        if target is None:
            raise SafetyError(
                f"目标空间 User {requested_user} 不存在。已发现: "
                + ", ".join(str(user.user_id) for user in users)
            )
        if target.is_primary and not allow_main_user:
            raise SafetyError(
                "目标空间是主空间 User 0；如确实要在主空间验证，请显式加 --allow-main-user。"
            )
        if not target.is_primary and not target.is_work_profile and not allow_main_user:
            raise SafetyError(
                f"目标空间 User {target.user_id} 不是 Island 工作资料（{target.name}）；"
                "如确实要在此空间验证，请显式加 --allow-main-user。"
            )
        return target
    for user in users:
        if user.is_work_profile:
            return user
    raise SafetyError(
        "未找到 Island 工作资料（如 User 10）。请先在 Island 中创建并启用工作资料，"
        "或显式传 --user <uid> / --allow-main-user。"
    )


def main_id_hidden_for_work_profile(main: SpaceIdentity, target: SpaceIdentity) -> bool:
    """ColorOS 隐藏主空间 ANDROID_ID 时，独立受管工作资料仍可作为隔离依据。"""
    return (
        target.user_id != 0
        and target.is_work_profile
        and target.android_id_valid
        and not main.android_id_valid
    )


def assert_space_isolated(
    main: SpaceIdentity,
    target: SpaceIdentity,
    *,
    allow_main_user: bool = False,
) -> None:
    """校验目标空间与主空间身份已隔离，否则中止。

    ``allow_main_user`` 且目标与主空间为同一用户时，仅验证目标 ANDROID_ID 有效
    （主空间基线模式没有隔离证明，不比较两侧 ID）。

    目标是非 0 的受管工作资料、且主空间 ANDROID_ID 被系统隐私保护隐藏时，
    只要目标 ANDROID_ID 有效即视为通过（ColorOS ``FLAG_MANAGED_PROFILE``
    工作资料使用独立 ``/data/system/users/<uid>/settings_ssaid.xml`` 盐值）。
    """
    if allow_main_user and target.user_id == main.user_id:
        if not target.android_id_valid:
            raise SafetyError(
                f"User {target.user_id} 的 ANDROID_ID 为空或非法"
                f"（{target.android_id!r}），无法完成主空间基线验证，已中止。"
            )
        return
    if main_id_hidden_for_work_profile(main, target):
        return
    if not main.android_id_valid:
        raise SafetyError(
            f"主空间 User {main.user_id} 的 ANDROID_ID 为空或非法"
            f"（{main.android_id!r}），无法证明设备身份已隔离，已中止。"
        )
    if not target.android_id_valid:
        raise SafetyError(
            f"User {target.user_id} 的 ANDROID_ID 为空或非法"
            f"（{target.android_id!r}），无法证明设备身份已隔离，已中止。"
        )
    if target.android_id.lower() == main.android_id.lower():
        raise SafetyError(
            f"User {target.user_id} 的 ANDROID_ID 与主空间 User {main.user_id} 完全相同"
            f"（{target.android_id}），Island 隔离未生效，已中止。"
        )


def format_identities(
    main: SpaceIdentity,
    target: SpaceIdentity,
    *,
    allow_main_user: bool = False,
) -> str:
    """渲染主空间 / 目标空间身份对比表。"""
    target_header = f"目标 User {target.user_id}"
    rows: list[tuple[str, str, str]] = [
        ("ANDROID_ID", main.android_id or "(空)", target.android_id or "(空)"),
        (
            "GSF ID (dec)",
            main.gsf.decimal if main.gsf else "(未取到)",
            target.gsf.decimal if target.gsf else "(未取到)",
        ),
        (
            "GSF ID (hex)",
            main.gsf.hex if main.gsf else "(未取到)",
            target.gsf.hex if target.gsf else "(未取到)",
        ),
        (
            "GMS 已安装",
            "是" if main.gms_installed else "否",
            "是" if target.gms_installed else "否",
        ),
        ("GMS 版本", main.gms_version or "-", target.gms_version or "-"),
    ]
    lines = [
        f"{'项目':<16}{'主空间 User 0':<28}{target_header:<28}",
        "-" * 72,
    ]
    for name, left, right in rows:
        lines.append(f"{name:<16}{left:<28}{right:<28}")

    gsf_known = main.gsf is not None and target.gsf is not None
    gsf_isolated = gsf_known and not target.same_gsf_id(main)
    lines.append("-" * 72)
    if allow_main_user and target.user_id == main.user_id:
        lines.append("ANDROID_ID 隔离: 不适用（主空间基线模式，无隔离证明）")
        lines.append("结论: 主空间基线模式仅采集验证轨迹，不能证明 Island 隔离。")
        return "\n".join(lines)
    hidden = main_id_hidden_for_work_profile(main, target)
    id_isolated = hidden or (
        target.android_id_valid
        and main.android_id_valid
        and not target.same_android_id(main)
    )
    lines.append(
        f"ANDROID_ID 隔离: {'已隔离 (PASS)' if id_isolated else '未隔离 (FAIL)'}"
    )
    if hidden:
        lines.append(
            "说明: 主空间 ANDROID_ID 受系统隐私保护隐藏；"
            f"目标工作资料 User {target.user_id} 拥有独立受管 ANDROID_ID。"
        )
    if not gsf_known:
        lines.append(
            "GSF ID 隔离: 未知（受系统权限限制无法读取，不影响 ANDROID_ID 隔离结论）"
        )
    else:
        lines.append(
            f"GSF ID 隔离: {'已隔离 (PASS)' if gsf_isolated else '未隔离 (FAIL)'}"
        )
    if hidden:
        lines.append(
            "结论: 主空间受系统隐私保护隐藏，目标工作资料拥有独立受管 "
            "ANDROID_ID，隔离前提成立。"
        )
    elif id_isolated and gsf_isolated:
        lines.append("结论: Island 工作资料与主空间的设备身份已完全隔离。")
    elif id_isolated:
        lines.append(
            "结论: ANDROID_ID 已隔离（GSF 受系统权限限制无法读取，"
            "不改变 ANDROID_ID 隔离结论）。"
        )
    else:
        lines.append("结论: 设备身份隔离未通过，请勿在此状态下做验证。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 图像解码（PNG / BMP / DIB）—— 内置零依赖实现，供纯 Python 二维码解码器使用
# ---------------------------------------------------------------------------

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
PNG_MAX_DIMENSION = 8192
PNG_MAX_PIXELS = 16_777_216


@dataclass
class GrayImage:
    """8bit 灰度图（行优先，``pixels[y * width + x]``）。"""

    width: int
    height: int
    pixels: bytearray

    def gray_at(self, x: int, y: int) -> int:
        return self.pixels[y * self.width + x]


def decode_png(data: bytes) -> GrayImage:
    """解码 PNG（1/2/4/8/16bit，灰度/彩色/调色板/Alpha，非隔行）为灰度图。"""
    if not data.startswith(PNG_SIGNATURE):
        raise ImageDecodeError("不是合法 PNG 文件（签名不匹配）")
    pos = len(PNG_SIGNATURE)
    width = height = 0
    bit_depth = 8
    color_type = 0
    interlace = 0
    palette: bytes | None = None
    idat = bytearray()
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos : pos + 4], "big")
        chunk_type = data[pos + 4 : pos + 8]
        body_start = pos + 8
        body_end = body_start + length
        if body_end + 4 > len(data):
            raise ImageDecodeError("PNG 数据块长度超出文件边界（疑似截断）")
        body = data[body_start:body_end]
        pos = body_end + 4
        if chunk_type == b"IHDR":
            if length < 13:
                raise ImageDecodeError(f"PNG IHDR 块长度非法: {length} < 13")
            try:
                width, height, bit_depth, color_type, _comp, _filt, interlace = (
                    struct.unpack(">IIBBBBB", body[:13])
                )
            except struct.error as exc:
                raise ImageDecodeError(f"PNG IHDR 解析失败: {exc}") from exc
        elif chunk_type == b"PLTE":
            palette = body
        elif chunk_type == b"IDAT":
            idat += body
        elif chunk_type == b"IEND":
            break
    if width <= 0 or height <= 0:
        raise ImageDecodeError("PNG 缺少合法 IHDR")
    if width > PNG_MAX_DIMENSION or height > PNG_MAX_DIMENSION:
        raise ImageDecodeError(
            f"PNG 尺寸超出上限 {PNG_MAX_DIMENSION}: {width}x{height}"
        )
    if width * height > PNG_MAX_PIXELS:
        raise ImageDecodeError(
            f"PNG 总像素数超出上限 {PNG_MAX_PIXELS}: {width}x{height}"
        )
    if interlace:
        raise ImageDecodeError("暂不支持隔行扫描（Adam7）PNG")
    if color_type not in PNG_CHANNELS or bit_depth not in (1, 2, 4, 8, 16):
        raise ImageDecodeError(
            f"暂不支持 PNG color_type={color_type} bit_depth={bit_depth}"
        )

    channels = PNG_CHANNELS[color_type]
    bits_per_pixel = channels * bit_depth
    stride = (width * bits_per_pixel + 7) // 8
    expected = height * (stride + 1)
    decompressor = zlib.decompressobj()
    try:
        raw = decompressor.decompress(bytes(idat), expected + 1)
        if len(raw) > expected or decompressor.unconsumed_tail:
            raise ImageDecodeError("PNG IDAT 解压输出超出预期尺寸（疑似解压炸弹）")
        raw += decompressor.flush()
    except zlib.error as exc:
        raise ImageDecodeError(f"PNG IDAT 解压失败: {exc}") from exc
    if len(raw) > expected:
        raise ImageDecodeError("PNG IDAT 解压输出超出预期尺寸（疑似解压炸弹）")

    bpp = max(1, bits_per_pixel // 8)
    prev = bytearray(stride)
    rows: list[bytes] = []
    offset = 0
    for _row in range(height):
        if offset >= len(raw):
            raise ImageDecodeError("PNG 像素数据不完整")
        filter_type = raw[offset]
        line = bytearray(raw[offset + 1 : offset + 1 + stride])
        offset += 1 + stride
        if len(line) != stride:
            raise ImageDecodeError("PNG 行数据长度不足")
        _png_unfilter(filter_type, line, prev, bpp)
        rows.append(bytes(line))
        prev = line
    return _png_rows_to_gray(width, height, rows, bit_depth, color_type, palette)


def _png_unfilter(
    filter_type: int, line: bytearray, prev: bytes | bytearray, bpp: int
) -> None:
    if filter_type == 0:
        return
    length = len(line)
    if filter_type == 1:
        for idx in range(bpp, length):
            line[idx] = (line[idx] + line[idx - bpp]) & 0xFF
    elif filter_type == 2:
        for idx in range(length):
            line[idx] = (line[idx] + prev[idx]) & 0xFF
    elif filter_type == 3:
        for idx in range(length):
            left = line[idx - bpp] if idx >= bpp else 0
            line[idx] = (line[idx] + ((left + prev[idx]) >> 1)) & 0xFF
    elif filter_type == 4:
        for idx in range(length):
            left = line[idx - bpp] if idx >= bpp else 0
            up_left = prev[idx - bpp] if idx >= bpp else 0
            line[idx] = (line[idx] + _paeth(left, prev[idx], up_left)) & 0xFF
    else:
        raise ImageDecodeError(f"未知 PNG 行过滤器: {filter_type}")


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _png_rows_to_gray(
    width: int,
    height: int,
    rows: Sequence[bytes],
    bit_depth: int,
    color_type: int,
    palette: bytes | None,
) -> GrayImage:
    pixels = bytearray(width * height)
    if bit_depth == 16:
        for y, row in enumerate(rows):
            _pack_gray_row(pixels, y, width, row[0::2], color_type, palette)
    elif bit_depth == 8:
        for y, row in enumerate(rows):
            _pack_gray_row(pixels, y, width, row, color_type, palette)
    else:
        for y, row in enumerate(rows):
            _pack_packed_row(pixels, y, width, row, bit_depth, color_type, palette)
    return GrayImage(width=width, height=height, pixels=pixels)


def _pack_gray_row(
    pixels: bytearray,
    y: int,
    width: int,
    row: bytes,
    color_type: int,
    palette: bytes | None,
) -> None:
    base = y * width
    if color_type == 3:
        if palette is None:
            raise ImageDecodeError("调色板 PNG 缺少 PLTE")
        for x in range(width):
            index = row[x] if x < len(row) else 0
            off = index * 3
            if off + 2 < len(palette):
                pixels[base + x] = _luma(
                    palette[off], palette[off + 1], palette[off + 2]
                )
        return
    if color_type == 0:
        pixels[base : base + width] = row[:width].ljust(width, b"\x00")
        return
    if color_type == 4:
        for x in range(width):
            off = x * 2
            if off < len(row):
                pixels[base + x] = row[off]
        return
    if color_type == 6:
        for x in range(width):
            off = x * 4
            if off + 3 < len(row):
                alpha = row[off + 3]
                pixels[base + x] = _luma(
                    _flatten_alpha(row[off], alpha),
                    _flatten_alpha(row[off + 1], alpha),
                    _flatten_alpha(row[off + 2], alpha),
                )
        return
    for x in range(width):
        off = x * 3
        if off + 2 < len(row):
            pixels[base + x] = _luma(row[off], row[off + 1], row[off + 2])


def _flatten_alpha(channel: int, alpha: int) -> int:
    """把带 Alpha 的通道合成到白色背景。"""
    return (channel * alpha + 255 * (255 - alpha)) // 255


def _pack_packed_row(
    pixels: bytearray,
    y: int,
    width: int,
    row: bytes,
    bit_depth: int,
    color_type: int,
    palette: bytes | None,
) -> None:
    base = y * width
    per_byte = 8 // bit_depth
    max_value = (1 << bit_depth) - 1
    for x in range(width):
        byte_index = x // per_byte
        if byte_index >= len(row):
            break
        shift = 8 - bit_depth * ((x % per_byte) + 1)
        value = (row[byte_index] >> shift) & max_value
        if color_type == 3:
            if palette is None:
                raise ImageDecodeError("调色板 PNG 缺少 PLTE")
            off = value * 3
            if off + 2 < len(palette):
                pixels[base + x] = _luma(
                    palette[off], palette[off + 1], palette[off + 2]
                )
        else:
            pixels[base + x] = value * 255 // max_value


def _luma(red: int, green: int, blue: int) -> int:
    return (red * 299 + green * 587 + blue * 114) // 1000


def decode_bmp(data: bytes) -> GrayImage:
    """解码 BMP（8/24/32bpp BI_RGB）或裸 DIB（剪贴板 CF_DIB）。"""
    payload = data[14:] if data.startswith(b"BM") else data
    if len(payload) < 40:
        raise ImageDecodeError("BMP/DIB 头部长度不足")
    header_size = struct.unpack("<I", payload[0:4])[0]
    if header_size < 40:
        raise ImageDecodeError(f"暂不支持 BMP header_size={header_size}")
    width, raw_height = struct.unpack("<ii", payload[4:12])
    planes, bit_count = struct.unpack("<HH", payload[12:16])
    compression = struct.unpack("<I", payload[16:20])[0]
    if planes != 1 or width <= 0 or raw_height == 0:
        raise ImageDecodeError("BMP 头部字段非法")
    if compression not in (0, 3):
        raise ImageDecodeError(f"暂不支持 BMP compression={compression}")
    if bit_count not in (8, 24, 32):
        raise ImageDecodeError(f"暂不支持 BMP bit_count={bit_count}")
    height = abs(raw_height)
    top_down = raw_height < 0
    palette: bytes | None = None
    offset = header_size
    if bit_count == 8:
        palette = payload[offset : offset + 1024]
        offset += 1024
    stride = ((width * bit_count + 31) // 32) * 4
    pixels = bytearray(width * height)
    for row in range(height):
        source_row = row if top_down else height - 1 - row
        start = offset + source_row * stride
        line = payload[start : start + stride]
        if len(line) < stride:
            raise ImageDecodeError("BMP 像素数据不完整")
        base = row * width
        if bit_count == 8:
            if palette is None or len(palette) < 1024:
                raise ImageDecodeError("8bpp BMP 缺少调色板")
            for x in range(width):
                off = line[x] * 4
                pixels[base + x] = _luma(
                    palette[off + 2], palette[off + 1], palette[off]
                )
            continue
        step = bit_count // 8
        for x in range(width):
            off = x * step
            pixels[base + x] = _luma(line[off + 2], line[off + 1], line[off])
    return GrayImage(width=width, height=height, pixels=pixels)


def load_gray_image(data: bytes) -> GrayImage:
    """自动识别 PNG / BMP / DIB 并解码为灰度图。"""
    if data.startswith(PNG_SIGNATURE):
        return decode_png(data)
    if data.startswith(b"BM"):
        return decode_bmp(data)
    if len(data) >= 4 and struct.unpack("<I", data[0:4])[0] in (40, 108, 124):
        return decode_bmp(data)
    raise ImageDecodeError("无法识别的图片格式（仅支持 PNG / BMP / 剪贴板 DIB）")


def binarize(gray: GrayImage, *, threshold: int | None = None) -> list[bytearray]:
    """二值化：显式 threshold 用全局阈值，否则用全局均值 + 8x8 局部块自适应。"""
    width, height, pixels = gray.width, gray.height, gray.pixels
    rows = [bytearray(width) for _ in range(height)]
    if threshold is not None:
        for y in range(height):
            base = y * width
            row = rows[y]
            for x in range(width):
                row[x] = 1 if pixels[base + x] < threshold else 0
        return rows

    block = 8
    cols = (width + block - 1) // block
    block_means: list[int] = []
    for by in range(0, height, block):
        for bx in range(0, width, block):
            total = 0
            count = 0
            for y in range(by, min(by + block, height)):
                base = y * width
                for x in range(bx, min(bx + block, width)):
                    total += pixels[base + x]
                    count += 1
            block_means.append(total // max(1, count))
    global_mean = sum(pixels) // max(1, len(pixels))
    for by in range(0, height, block):
        for bx in range(0, width, block):
            mean = block_means[(by // block) * cols + (bx // block)]
            local = min(255, max(0, (mean + global_mean) // 2))
            for y in range(by, min(by + block, height)):
                base = y * width
                row = rows[y]
                for x in range(bx, min(bx + block, width)):
                    row[x] = 1 if pixels[base + x] < local else 0
    return rows


# ---------------------------------------------------------------------------
# 二维码解码：可选加速后端 + 内置纯 Python 解码器
# ---------------------------------------------------------------------------

DECODE_BACKENDS: tuple[str, ...] = ("cv2", "zxingcpp", "pyzbar", "builtin")


def available_backends() -> list[str]:
    """探测当前环境可用的解码后端（至少包含内置解码器）。"""
    import importlib.util

    found: list[str] = []
    for name in DECODE_BACKENDS:
        if name == "pyzbar":
            if importlib.util.find_spec("pyzbar") and importlib.util.find_spec("PIL"):
                found.append(name)
            continue
        if importlib.util.find_spec(name):
            found.append(name)
    return [*found, "builtin"]


def decode_qr_from_image(
    data: bytes,
    *,
    backends: Sequence[str] | None = None,
    prefer: Sequence[str] | None = None,
) -> str:
    """从图片字节中解出二维码文本；多后端串联，内置解码器兜底。"""
    payload = normalize_image_bytes(data)
    order = list(backends) if backends else list(DECODE_BACKENDS)
    if prefer:
        order = [name for name in prefer if name in order] + [
            name for name in order if name not in prefer
        ]
    failures: list[str] = []
    for name in order:
        try:
            text = decode_with_backend(name, payload)
        except Exception as exc:  # 逐个后端容错，最终统一抛错
            failures.append(f"{name}: {exc}")
            continue
        if text and text.strip():
            return text.strip()
        failures.append(f"{name}: 未识别到二维码")
    raise QrDecodeError("二维码解码失败（所有后端均失败）: " + " | ".join(failures))


def normalize_image_bytes(data: bytes) -> bytes:
    """把剪贴板裸 DIB 补上 BMP 文件头，便于 cv2 / Pillow 等后端解析。"""
    if data.startswith((b"BM", PNG_SIGNATURE)):
        return data
    if len(data) < 40:
        return data
    header_size = struct.unpack("<I", data[0:4])[0]
    if header_size not in (40, 108, 124):
        return data
    bit_count = struct.unpack("<H", data[14:16])[0]
    compression = struct.unpack("<I", data[16:20])[0]
    palette_bytes = 0
    if bit_count <= 8:
        palette_bytes = (1 << bit_count) * 4
    if compression == 3 and header_size == 40:
        palette_bytes += 12
    offset = 14 + header_size + palette_bytes
    header = struct.pack("<2sIHHI", b"BM", 14 + len(data), 0, 0, offset)
    return header + data


def decode_with_backend(name: str, data: bytes) -> str:
    if name == "cv2":
        return _decode_with_cv2(data)
    if name == "zxingcpp":
        return _decode_with_zxingcpp(data)
    if name == "pyzbar":
        return _decode_with_pyzbar(data)
    if name == "builtin":
        return decode_builtin(data)
    raise QrDecodeError(f"未知解码后端: {name}")


def _decode_with_cv2(data: bytes) -> str:
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ImageDecodeError("cv2.imdecode 无法解析该图片")
    detector = cv2.QRCodeDetector()
    try:
        text, _points, _straight = detector.detectAndDecode(image)
        if text:
            return str(text)
    except cv2.error:
        pass
    try:
        ok, texts, _points, _straight = detector.detectAndDecodeMulti(image)
        if ok:
            for item in texts:
                if item:
                    return str(item)
    except cv2.error:
        pass
    return ""


def _decode_with_zxingcpp(data: bytes) -> str:
    import numpy as np
    import zxingcpp

    gray = load_gray_image(data)
    pixels = np.frombuffer(bytes(gray.pixels), dtype=np.uint8).reshape(
        gray.height, gray.width
    )
    for result in zxingcpp.read_barcodes(pixels):
        text = getattr(result, "text", "")
        if text:
            return str(text)
    return ""


def _decode_with_pyzbar(data: bytes) -> str:
    import io

    from PIL import Image  # pyright: ignore[reportMissingImports]
    from pyzbar.pyzbar import decode as zbar_decode  # pyright: ignore[reportMissingImports]

    with Image.open(io.BytesIO(data)) as image:
        for result in zbar_decode(image.convert("L")):
            payload = result.data.decode("utf-8", errors="replace")
            if payload:
                return payload
    return ""


def decode_builtin(data: bytes) -> str:
    """纯 Python 二维码解码（PNG/BMP/DIB → 灰度 → 定位 → 采样 → RS 纠错 → 解码）。"""
    return decode_gray_image(load_gray_image(data))


def decode_gray_image(gray: GrayImage) -> str:
    """对灰度图做多阈值 + 反色尝试，任一成功即返回文本。"""
    last_error: Exception | None = None
    for threshold in (None, 128, 180):
        base = binarize(gray, threshold=threshold)
        for invert in (False, True):
            bits = _invert_bits(base) if invert else base
            try:
                return decode_binary_matrix(bits)
            except QrDecodeError as exc:
                last_error = exc
    raise QrDecodeError(f"内置解码器无法识别二维码: {last_error}")


def _invert_bits(bits: Sequence[bytearray]) -> list[bytearray]:
    return [bytearray(1 - value for value in row) for row in bits]


@dataclass
class BitMatrix:
    """方阵二值模块图（0/1）。"""

    size: int
    rows: list[bytearray] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.rows:
            self.rows = [bytearray(self.size) for _ in range(self.size)]

    def get(self, x: int, y: int) -> int:
        return self.rows[y][x]

    def set(self, x: int, y: int, value: int) -> None:
        self.rows[y][x] = 1 if value else 0

    def copy(self) -> BitMatrix:
        return BitMatrix(size=self.size, rows=[bytearray(row) for row in self.rows])


# ----- 定位：在二值图中寻找三个定位图形（finder pattern） -----------------


@dataclass(frozen=True)
class FinderPattern:
    x: float
    y: float
    module: float


@dataclass(frozen=True)
class QrOrientation:
    """由三个定位图形推出的二维码网格坐标系。"""

    top_left: tuple[float, float]
    top_right: tuple[float, float]
    bottom_left: tuple[float, float]
    dimension: int
    module: float


def _row_runs(row: bytearray) -> list[tuple[int, int, int]]:
    runs: list[tuple[int, int, int]] = []
    if not row:
        return runs
    start = 0
    current = row[0]
    for x in range(1, len(row)):
        if row[x] != current:
            runs.append((start, x - start, current))
            start = x
            current = row[x]
    runs.append((start, len(row) - start, current))
    return runs


def _matches_finder_ratio(counts: Sequence[int]) -> bool:
    """严格校验 1:1:3:1:1 游程，避免把数据区误判成定位图形。"""
    total = sum(counts)
    if total < 7:
        return False
    outer = [counts[0], counts[1], counts[3], counts[4]]
    smallest = min(outer)
    if smallest <= 0:
        return False
    if max(outer) > smallest * 1.6:
        return False
    unit = sum(outer) / 4.0
    if not 2.2 <= counts[2] / unit <= 3.8:
        return False
    return abs(counts[2] - 3 * unit) <= unit * 1.2


def _column_runs(
    bits: Sequence[bytearray], height: int, x: int
) -> list[tuple[int, int, int]]:
    runs: list[tuple[int, int, int]] = []
    current = bits[0][x]
    start = 0
    for y in range(1, height):
        value = bits[y][x]
        if value != current:
            runs.append((start, y - start, current))
            start = y
            current = value
    runs.append((start, height - start, current))
    return runs


def _ratio_of(
    runs: Sequence[tuple[int, int, int]], index: int
) -> tuple[bool, float, float]:
    """index 指向 1:1:3:1:1 五个游程的最后一个。"""
    if index < 4:
        return (False, 0.0, 0.0)
    window = runs[index - 4 : index + 1]
    colors = [run[2] for run in window]
    if colors != [1, 0, 1, 0, 1]:
        return (False, 0.0, 0.0)
    counts = [run[1] for run in window]
    if not _matches_finder_ratio(counts):
        return (False, 0.0, 0.0)
    total = sum(counts)
    center = window[0][0] + total / 2.0
    return (True, center, total / 7.0)


def find_finder_patterns(
    bits: Sequence[bytearray], width: int, height: int
) -> list[FinderPattern]:
    """横向扫描 1:1:3:1:1 游程，再做纵向复核并聚类。"""
    clusters: list[list[float]] = []
    for y in range(height):
        runs = _row_runs(bits[y])
        for index in range(4, len(runs)):
            ok, center_x, module = _ratio_of(runs, index)
            if not ok:
                continue
            refined = _cross_check_vertical(
                bits, width, height, int(center_x), y, module
            )
            if refined is None:
                continue
            center_y, module_v = refined
            module_avg = (module + module_v) / 2.0
            if module_avg < 1.0:
                continue
            _accumulate_cluster(clusters, center_x, center_y, module_avg)
    patterns: list[FinderPattern] = []
    for cx, cy, module, weight in clusters:
        if weight >= 3:
            patterns.append(FinderPattern(x=cx, y=cy, module=module))
    patterns.sort(key=lambda item: item.module)
    return patterns


def _accumulate_cluster(
    clusters: list[list[float]], x: float, y: float, module: float
) -> None:
    for cluster in clusters:
        if (
            abs(cluster[0] - x) <= cluster[2] * 2.0
            and abs(cluster[1] - y) <= cluster[2] * 2.0
        ):
            weight = cluster[3]
            cluster[0] = (cluster[0] * weight + x) / (weight + 1)
            cluster[1] = (cluster[1] * weight + y) / (weight + 1)
            cluster[2] = (cluster[2] * weight + module) / (weight + 1)
            cluster[3] = weight + 1
            return
    clusters.append([x, y, module, 1.0])


def _cross_check_vertical(
    bits: Sequence[bytearray],
    width: int,
    height: int,
    center_x: int,
    center_y: int,
    module: float,
) -> tuple[float, float] | None:
    if not (0 <= center_x < width and 0 <= center_y < height):
        return None
    runs = _column_runs(bits, height, center_x)
    for index in range(4, len(runs)):
        ok, center, module_v = _ratio_of(runs, index)
        if not ok:
            continue
        center_int = round(center)
        if abs(center_int - center_y) <= max(3.0, module * 2.0):
            return (center, module_v)
    return None


def _right_angle_orientation(
    patterns: Sequence[FinderPattern],
) -> list[QrOrientation]:
    orientations: list[QrOrientation] = []
    count = len(patterns)
    for i in range(count):
        for j in range(i + 1, count):
            for k in range(j + 1, count):
                built = _build_orientation(patterns[i], patterns[j], patterns[k])
                if built is None:
                    continue
                for delta in (0, -4, 4):
                    dimension = built.dimension + delta
                    if 21 <= dimension <= 177:
                        orientations.append(
                            QrOrientation(
                                top_left=built.top_left,
                                top_right=built.top_right,
                                bottom_left=built.bottom_left,
                                dimension=dimension,
                                module=built.module,
                            )
                        )
    orientations.sort(key=lambda item: item.module, reverse=True)
    return orientations


def _build_orientation(
    a: FinderPattern, b: FinderPattern, c: FinderPattern
) -> QrOrientation | None:
    points = [a, b, c]
    pairs = [
        (0, 1, _distance(a, b)),
        (1, 2, _distance(b, c)),
        (0, 2, _distance(a, c)),
    ]
    pairs.sort(key=lambda item: item[2], reverse=True)
    tr_index, bl_index, _hypotenuse = pairs[0]
    tl_index = next(index for index in range(3) if index not in {tr_index, bl_index})
    tl = points[tl_index]
    first, second = points[tr_index], points[bl_index]
    cross = (first.x - tl.x) * (second.y - tl.y) - (first.y - tl.y) * (second.x - tl.x)
    if cross > 0:
        tr, bl = first, second
    else:
        tr, bl = second, first
    module = (tl.module + tr.module + bl.module) / 3.0
    if module <= 0:
        return None
    horizontal = _distance(tl, tr)
    vertical = _distance(tl, bl)
    if horizontal <= 0 or vertical <= 0:
        return None
    estimate = (horizontal + vertical) / 2.0 / module + 7.0
    dimension = round((estimate - 21.0) / 4.0) * 4 + 21
    if dimension < 21 or dimension > 177:
        return None
    modules_across = dimension - 7
    module_h = horizontal / modules_across
    module_v = vertical / modules_across
    if module_h <= 0 or module_v <= 0:
        return None
    if abs(module_h - module_v) / max(module_h, module_v) > 0.35:
        return None
    return QrOrientation(
        top_left=(tl.x, tl.y),
        top_right=(tr.x, tr.y),
        bottom_left=(bl.x, bl.y),
        dimension=dimension,
        module=(module_h + module_v) / 2.0,
    )


def _distance(a: FinderPattern, b: FinderPattern) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def sample_orientation(
    orientation: QrOrientation, bits: Sequence[bytearray], width: int, height: int
) -> BitMatrix:
    """按网格坐标系采样模块，多点多数表决降噪。"""
    dim = orientation.dimension
    span = dim - 7
    tl_x, tl_y = orientation.top_left
    ux = (orientation.top_right[0] - tl_x) / span
    uy = (orientation.top_right[1] - tl_y) / span
    vx = (orientation.bottom_left[0] - tl_x) / span
    vy = (orientation.bottom_left[1] - tl_y) / span
    matrix = BitMatrix(size=dim)
    pitch = (abs(ux) + abs(uy) + abs(vx) + abs(vy)) / 2.0
    if pitch < 3.0:
        offsets = ((0.0, 0.0),)
    else:
        offsets = (
            (0.0, 0.0),
            (-0.25, -0.25),
            (0.25, -0.25),
            (-0.25, 0.25),
            (0.25, 0.25),
        )
    for y in range(dim):
        base_x = tl_x + (y - 3.0) * vx
        base_y = tl_y + (y - 3.0) * vy
        row = matrix.rows[y]
        for x in range(dim):
            center_x = base_x + (x - 3.0) * ux
            center_y = base_y + (x - 3.0) * uy
            dark = 0
            total = 0
            for ox, oy in offsets:
                px = round(center_x + ox * ux + oy * vx)
                py = round(center_y + ox * uy + oy * vy)
                if 0 <= px < width and 0 <= py < height:
                    dark += bits[py][px]
                    total += 1
            row[x] = 1 if total and dark * 2 >= total else 0
    return matrix


def check_timing(matrix: BitMatrix) -> bool:
    """校验 timing pattern，过滤掉错误的三角组合。"""
    mismatches = 0
    for index in range(8, matrix.size - 8):
        expected = 1 if index % 2 == 0 else 0
        if matrix.get(index, 6) != expected:
            mismatches += 1
        if matrix.get(6, index) != expected:
            mismatches += 1
    return mismatches <= 3


# ----- 模块矩阵 → 文本 ------------------------------------------------


FORMAT_MASK = 0x5412
FORMAT_GENERATOR = 0x537
MASK_PATTERNS: tuple[Callable[[int, int], bool], ...] = (
    lambda x, y: (x + y) % 2 == 0,
    lambda _x, y: y % 2 == 0,
    lambda x, _y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (y // 2 + x // 3) % 2 == 0,
    lambda x, y: (x * y) % 2 + (x * y) % 3 == 0,
    lambda x, y: ((x * y) % 2 + (x * y) % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + (x * y) % 3) % 2 == 0,
)
EC_LEVEL_BY_BITS = {0b01: "L", 0b00: "M", 0b11: "Q", 0b10: "H"}


def _format_codeword(data_bits: int) -> int:
    value = data_bits << 10
    for shift in range(4, -1, -1):
        if value & (1 << (shift + 10)):
            value ^= FORMAT_GENERATOR << shift
    return ((data_bits << 10) | value) ^ FORMAT_MASK


FORMAT_TABLE: dict[int, tuple[str, int]] = {}
for _data in range(32):
    FORMAT_TABLE[_format_codeword(_data)] = (EC_LEVEL_BY_BITS[_data >> 3], _data & 0x07)


def decode_binary_matrix(bits: Sequence[bytearray]) -> str:
    """在二值图中定位并解码二维码。"""
    height = len(bits)
    width = len(bits[0]) if height else 0
    if width < 21 or height < 21:
        raise QrDecodeError("图像尺寸不足，无法包含二维码")
    patterns = find_finder_patterns(bits, width, height)
    if len(patterns) < 3:
        raise QrDecodeError(f"仅找到 {len(patterns)} 个定位图形，需要 3 个")
    last_error: Exception | None = None
    for orientation in _right_angle_orientation(patterns[:8]):
        try:
            matrix = sample_orientation(orientation, bits, width, height)
            if not check_timing(matrix):
                continue
            return decode_module_matrix(matrix)
        except QrDecodeError as exc:
            last_error = exc
    raise QrDecodeError(f"定位成功但解码失败: {last_error}")


def decode_module_matrix(matrix: BitMatrix) -> str:
    """从模块矩阵解出文本（含格式信息、掩码、RS 纠错）。"""
    size = matrix.size
    if (size - 17) % 4 != 0:
        raise QrDecodeError(f"模块矩阵尺寸非法: {size}")
    version = (size - 17) // 4
    if not 1 <= version <= 40:
        raise QrDecodeError(f"二维码版本超出范围: {version}")
    format_info = read_format_info(matrix)
    if format_info is None:
        raise QrDecodeError("格式信息校验失败（无法纠正）")
    ec_level, mask = format_info
    codewords = read_codewords(matrix, version, mask)
    data = correct_and_deinterleave(version, ec_level, codewords)
    return decode_segments(data, version)


def read_format_info(matrix: BitMatrix) -> tuple[str, int] | None:
    size = matrix.size
    copy_one = 0
    for index in range(6):
        copy_one = (copy_one << 1) | matrix.get(index, 8)
    copy_one = (copy_one << 1) | matrix.get(7, 8)
    copy_one = (copy_one << 1) | matrix.get(8, 8)
    copy_one = (copy_one << 1) | matrix.get(8, 7)
    for row in range(5, -1, -1):
        copy_one = (copy_one << 1) | matrix.get(8, row)

    copy_two = 0
    for row in range(size - 1, size - 8, -1):
        copy_two = (copy_two << 1) | matrix.get(8, row)
    for col in range(size - 8, size):
        copy_two = (copy_two << 1) | matrix.get(col, 8)

    best: tuple[str, int] | None = None
    best_distance = 4
    for candidate, payload in FORMAT_TABLE.items():
        for value in (copy_one, copy_two):
            distance = bin(candidate ^ value).count("1")
            if distance < best_distance:
                best_distance = distance
                best = payload
    return best


def build_function_map(version: int) -> list[bytearray]:
    """标记所有功能区模块（定位、分隔、timing、校正、格式、版本、暗模块）。"""
    size = version * 4 + 17
    flags = [bytearray(size) for _ in range(size)]

    def mark(x0: int, y0: int, w: int, h: int) -> None:
        for y in range(max(0, y0), min(size, y0 + h)):
            row = flags[y]
            for x in range(max(0, x0), min(size, x0 + w)):
                row[x] = 1

    mark(0, 0, 9, 9)
    mark(size - 8, 0, 8, 9)
    mark(0, size - 8, 9, 8)
    mark(6, 9, 1, size - 17)
    mark(9, 6, size - 17, 1)
    centers = ALIGNMENT_POSITIONS.get(version, ())
    last = len(centers) - 1
    for yi, center_y in enumerate(centers):
        for xi, center_x in enumerate(centers):
            if (xi, yi) in {(0, 0), (0, last), (last, 0)}:
                continue
            mark(center_x - 2, center_y - 2, 5, 5)
    if version >= 7:
        mark(size - 11, 0, 3, 6)
        mark(0, size - 11, 6, 3)
    return flags


def read_codewords(matrix: BitMatrix, version: int, mask: int) -> list[int]:
    size = matrix.size
    flags = build_function_map(version)
    mask_fn = MASK_PATTERNS[mask]
    bits: list[int] = []
    upwards = True
    column = size - 1
    while column > 0:
        if column == 6:
            column -= 1
        rows = range(size - 1, -1, -1) if upwards else range(size)
        for row in rows:
            for col in (column, column - 1):
                if flags[row][col]:
                    continue
                value = matrix.get(col, row)
                if mask_fn(col, row):
                    value ^= 1
                bits.append(value)
        upwards = not upwards
        column -= 2
    codewords: list[int] = []
    for index in range(len(bits) // 8):
        byte = 0
        for offset in range(8):
            byte = (byte << 1) | bits[index * 8 + offset]
        codewords.append(byte)
    return codewords


# RS 分块表（版本 → L/M/Q/H → (块数:总码字:数据码字)+...），由 QR 规范标准表生成。
RS_BLOCK_RAW = (
    "1:26:19|1:26:16|1:26:13|1:26:9;1:44:34|1:44:28|1:44:22|1:44:16;1:70:55|1:70:44|2:35:"
    "17|2:35:13;1:100:80|2:50:32|2:50:24|4:25:9;1:134:108|2:67:43|2:33:15+2:34:16|2:33:11"
    "+2:34:12;2:86:68|4:43:27|4:43:19|4:43:15;2:98:78|4:49:31|2:32:14+4:33:15|4:39:13+1:4"
    "0:14;2:121:97|2:60:38+2:61:39|4:40:18+2:41:19|4:40:14+2:41:15;2:146:116|3:58:36+2:59"
    ":37|4:36:16+4:37:17|4:36:12+4:37:13;2:86:68+2:87:69|4:69:43+1:70:44|6:43:19+2:44:20|"
    "6:43:15+2:44:16;4:101:81|1:80:50+4:81:51|4:50:22+4:51:23|3:36:12+8:37:13;2:116:92+2:"
    "117:93|6:58:36+2:59:37|4:46:20+6:47:21|7:42:14+4:43:15;4:133:107|8:59:37+1:60:38|8:4"
    "4:20+4:45:21|12:33:11+4:34:12;3:145:115+1:146:116|4:64:40+5:65:41|11:36:16+5:37:17|1"
    "1:36:12+5:37:13;5:109:87+1:110:88|5:65:41+5:66:42|5:54:24+7:55:25|11:36:12+7:37:13;5"
    ":122:98+1:123:99|7:73:45+3:74:46|15:43:19+2:44:20|3:45:15+13:46:16;1:135:107+5:136:1"
    "08|10:74:46+1:75:47|1:50:22+15:51:23|2:42:14+17:43:15;5:150:120+1:151:121|9:69:43+4:"
    "70:44|17:50:22+1:51:23|2:42:14+19:43:15;3:141:113+4:142:114|3:70:44+11:71:45|17:47:2"
    "1+4:48:22|9:39:13+16:40:14;3:135:107+5:136:108|3:67:41+13:68:42|15:54:24+5:55:25|15:"
    "43:15+10:44:16;4:144:116+4:145:117|17:68:42|17:50:22+6:51:23|19:46:16+6:47:17;2:139:"
    "111+7:140:112|17:74:46|7:54:24+16:55:25|34:37:13;4:151:121+5:152:122|4:75:47+14:76:4"
    "8|11:54:24+14:55:25|16:45:15+14:46:16;6:147:117+4:148:118|6:73:45+14:74:46|11:54:24+"
    "16:55:25|30:46:16+2:47:17;8:132:106+4:133:107|8:75:47+13:76:48|7:54:24+22:55:25|22:4"
    "5:15+13:46:16;10:142:114+2:143:115|19:74:46+4:75:47|28:50:22+6:51:23|33:46:16+4:47:1"
    "7;8:152:122+4:153:123|22:73:45+3:74:46|8:53:23+26:54:24|12:45:15+28:46:16;3:147:117+"
    "10:148:118|3:73:45+23:74:46|4:54:24+31:55:25|11:45:15+31:46:16;7:146:116+7:147:117|2"
    "1:73:45+7:74:46|1:53:23+37:54:24|19:45:15+26:46:16;5:145:115+10:146:116|19:75:47+10:"
    "76:48|15:54:24+25:55:25|23:45:15+25:46:16;13:145:115+3:146:116|2:74:46+29:75:47|42:5"
    "4:24+1:55:25|23:45:15+28:46:16;17:145:115|10:74:46+23:75:47|10:54:24+35:55:25|19:45:"
    "15+35:46:16;17:145:115+1:146:116|14:74:46+21:75:47|29:54:24+19:55:25|11:45:15+46:46:"
    "16;13:145:115+6:146:116|14:74:46+23:75:47|44:54:24+7:55:25|59:46:16+1:47:17;12:151:1"
    "21+7:152:122|12:75:47+26:76:48|39:54:24+14:55:25|22:45:15+41:46:16;6:151:121+14:152:"
    "122|6:75:47+34:76:48|46:54:24+10:55:25|2:45:15+64:46:16;17:152:122+4:153:123|29:74:4"
    "6+14:75:47|49:54:24+10:55:25|24:45:15+46:46:16;4:152:122+18:153:123|13:74:46+32:75:4"
    "7|48:54:24+14:55:25|42:45:15+32:46:16;20:147:117+4:148:118|40:75:47+7:76:48|43:54:24"
    "+22:55:25|10:45:15+67:46:16;19:148:118+6:149:119|18:75:47+31:76:48|34:54:24+34:55:25"
    "|20:45:15+61:46:16"
)
ALIGNMENT_RAW = (
    "6 18;6 22;6 26;6 30;6 34;6 22 38;6 24 42;6 26 46;6 28 50;6 30 54;6 32 58;6 34 62;"
    "6 26 46 66;6 26 48 70;6 26 50 74;6 30 54 78;6 30 56 82;6 30 58 86;6 34 62 90;"
    "6 28 50 72 94;6 26 50 74 98;6 30 54 78 102;6 28 54 80 106;6 32 58 84 110;"
    "6 30 58 86 114;6 34 62 90 118;6 26 50 74 98 122;6 30 54 78 102 126;"
    "6 26 52 78 104 130;6 30 56 82 108 134;6 34 60 86 112 138;6 30 58 86 114 142;"
    "6 34 62 90 118 146;6 30 54 78 102 126 150;6 24 50 76 102 128 154;"
    "6 28 54 80 106 132 158;6 32 58 84 110 136 162;6 26 54 82 110 138 166;"
    "6 30 58 86 114 142 170"
)
EC_LEVEL_ORDER: tuple[str, ...] = ("L", "M", "Q", "H")


def _parse_rs_table() -> dict[int, dict[str, tuple[tuple[int, int, int], ...]]]:
    table: dict[int, dict[str, tuple[tuple[int, int, int], ...]]] = {}
    for version, version_text in enumerate(RS_BLOCK_RAW.split(";"), start=1):
        levels = version_text.split("|")
        if len(levels) != 4:
            raise ToolError(f"RS 分块表版本 {version} 数据损坏")
        entry: dict[str, tuple[tuple[int, int, int], ...]] = {}
        for level, level_text in zip(EC_LEVEL_ORDER, levels, strict=True):
            groups: list[tuple[int, int, int]] = []
            for group in level_text.split("+"):
                blocks, total, data = (int(item) for item in group.split(":"))
                groups.append((blocks, total, data))
            entry[level] = tuple(groups)
        table[version] = entry
    return table


def _parse_alignment_table() -> dict[int, tuple[int, ...]]:
    positions: dict[int, tuple[int, ...]] = {}
    for version, text in enumerate(ALIGNMENT_RAW.split(";"), start=2):
        positions[version] = tuple(int(item) for item in text.split())
    return positions


RS_BLOCKS = _parse_rs_table()
ALIGNMENT_POSITIONS = _parse_alignment_table()

GF_EXP = [0] * 512
GF_LOG = [0] * 256


def _init_galois_field() -> None:
    value = 1
    for index in range(255):
        GF_EXP[index] = value
        GF_LOG[value] = index
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    for index in range(255, 512):
        GF_EXP[index] = GF_EXP[index - 255]


_init_galois_field()


def gf_mul(left: int, right: int) -> int:
    if left == 0 or right == 0:
        return 0
    return GF_EXP[GF_LOG[left] + GF_LOG[right]]


def gf_div(left: int, right: int) -> int:
    if right == 0:
        raise QrDecodeError("GF 除法分母为 0")
    if left == 0:
        return 0
    return GF_EXP[(GF_LOG[left] - GF_LOG[right]) % 255]


def _poly_eval(poly: Sequence[int], x: int) -> int:
    result = 0
    for coefficient in reversed(poly):
        result = gf_mul(result, x) ^ coefficient
    return result


def _poly_add(left: Sequence[int], right: Sequence[int]) -> list[int]:
    length = max(len(left), len(right))
    result = [0] * length
    for index, value in enumerate(left):
        result[index] ^= value
    for index, value in enumerate(right):
        result[index] ^= value
    return result


def _poly_scale(poly: Sequence[int], factor: int) -> list[int]:
    return [gf_mul(value, factor) for value in poly]


def _poly_mul(left: Sequence[int], right: Sequence[int]) -> list[int]:
    result = [0] * (len(left) + len(right) - 1)
    for i, left_value in enumerate(left):
        if left_value == 0:
            continue
        for j, right_value in enumerate(right):
            if right_value:
                result[i + j] ^= gf_mul(left_value, right_value)
    return result


def _poly_derivative(poly: Sequence[int]) -> list[int]:
    result = [0] * max(1, len(poly) - 1)
    for index in range(1, len(poly)):
        result[index - 1] = poly[index] if index % 2 else 0
    return result


def _berlekamp_massey(syndromes: Sequence[int]) -> list[int]:
    locator = [1]
    previous = [1]
    degree = 0
    shift = 1
    last_discrepancy = 1
    for step in range(len(syndromes)):
        discrepancy = syndromes[step]
        for index in range(1, degree + 1):
            if index < len(locator):
                discrepancy ^= gf_mul(locator[index], syndromes[step - index])
        if discrepancy == 0:
            shift += 1
            continue
        factor = gf_div(discrepancy, last_discrepancy)
        scaled = [0] * shift + _poly_scale(previous, factor)
        updated = _poly_add(locator, scaled)
        if 2 * degree <= step:
            degree = step + 1 - degree
            previous = locator
            last_discrepancy = discrepancy
            shift = 1
        else:
            shift += 1
        locator = updated
    while len(locator) > 1 and locator[-1] == 0:
        locator.pop()
    return locator[: degree + 1]


def _chien_search(locator: Sequence[int], block_length: int) -> list[int]:
    positions: list[int] = []
    for position in range(block_length):
        root = GF_EXP[(-position) % 255]
        if _poly_eval(locator, root) == 0:
            positions.append(position)
    return positions


def rs_correct(block: Sequence[int], ec_length: int) -> list[int]:
    """对单个 RS 块做纠错，返回纠正后的数据码字部分。"""
    if ec_length <= 0:
        return list(block)
    if len(block) <= ec_length:
        raise QrDecodeError("RS 块长度非法")
    poly = list(reversed(block))
    syndromes = [_poly_eval(poly, GF_EXP[index]) for index in range(ec_length)]
    if not any(syndromes):
        return list(block[: len(block) - ec_length])
    locator = _berlekamp_massey(syndromes)
    degree = len(locator) - 1
    if degree <= 0 or degree * 2 > ec_length:
        raise QrDecodeError(f"RS 错误定位失败（degree={degree}）")
    positions = _chien_search(locator, len(block))
    if len(positions) != degree:
        raise QrDecodeError(f"RS Chien 搜根数 {len(positions)} != {degree}")
    omega = _poly_mul(locator, list(syndromes))[:ec_length]
    derivative = _poly_derivative(locator)
    corrected = list(poly)
    for position in positions:
        root = GF_EXP[(-position) % 255]
        denominator = _poly_eval(derivative, root)
        if denominator == 0:
            raise QrDecodeError("RS Forney 分母为 0")
        magnitude = gf_mul(
            GF_EXP[position % 255], gf_div(_poly_eval(omega, root), denominator)
        )
        corrected[position] ^= magnitude
    if any(_poly_eval(corrected, GF_EXP[index]) for index in range(ec_length)):
        raise QrDecodeError("RS 纠错后校验子仍非零")
    return list(reversed(corrected))[: len(block) - ec_length]


def correct_and_deinterleave(
    version: int, ec_level: str, codewords: Sequence[int]
) -> bytes:
    """按块交错规则还原数据块并逐块 RS 纠错。"""
    spec = RS_BLOCKS.get(version, {}).get(ec_level)
    if not spec:
        raise QrDecodeError(f"缺少版本 {version} / {ec_level} 的分块表")
    blocks: list[list[Any]] = []
    for count, total, data_length in spec:
        for _ in range(count):
            blocks.append([total, data_length, []])
    total_needed = sum(int(block[0]) for block in blocks)
    if len(codewords) < total_needed:
        raise QrDecodeError(f"码字数量不足: {len(codewords)} < {total_needed}")
    stream = list(codewords[:total_needed])
    index = 0
    max_data = max(int(block[1]) for block in blocks)
    for position in range(max_data):
        for block in blocks:
            if position < int(block[1]):
                block[2].append(stream[index])
                index += 1
    max_ec = max(int(block[0]) - int(block[1]) for block in blocks)
    for position in range(max_ec):
        for block in blocks:
            if position < int(block[0]) - int(block[1]):
                block[2].append(stream[index])
                index += 1
    output = bytearray()
    for total, data_length, words in blocks:
        corrected = rs_correct(words, int(total) - int(data_length))
        output.extend(corrected[: int(data_length)])
    return bytes(output)


COUNT_BITS: dict[str, tuple[int, int, int]] = {
    "numeric": (10, 12, 14),
    "alnum": (9, 11, 13),
    "byte": (8, 16, 16),
    "kanji": (8, 10, 12),
}
ALNUM_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"


class BitSource:
    """按位读取数据码字流。"""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.index = 0
        self.bit = 0

    def available(self) -> int:
        return 8 * (len(self.data) - self.index) - self.bit

    def read(self, count: int) -> int:
        if count == 0:
            return 0
        if count > self.available():
            raise QrDecodeError(f"数据位不足：请求 {count} 位，剩余 {self.available()}")
        value = 0
        for _ in range(count):
            byte = self.data[self.index]
            value = (value << 1) | ((byte >> (7 - self.bit)) & 1)
            self.bit += 1
            if self.bit == 8:
                self.bit = 0
                self.index += 1
        return value


def _version_group(version: int) -> int:
    if version <= 9:
        return 0
    if version <= 26:
        return 1
    return 2


def decode_segments(data: bytes, version: int) -> str:
    """解析数据段（数字/字母数字/字节/ECI/Kanji）。"""
    source = BitSource(data)
    group = _version_group(version)
    eci = 26
    chunks: list[str] = []
    while source.available() >= 4:
        mode = source.read(4)
        if mode == 0:
            break
        if mode == 7:
            first = source.read(8)
            if first & 0x80 == 0:
                eci = first
            elif first & 0xC0 == 0x80:
                eci = ((first & 0x3F) << 8) | source.read(8)
            else:
                eci = ((first & 0x1F) << 16) | source.read(16)
            continue
        if mode in (3, 5, 9):
            if mode == 3:
                source.read(16)
            elif mode == 9:
                source.read(8)
            continue
        if mode == 1:
            chunks.append(
                _read_numeric(source, source.read(COUNT_BITS["numeric"][group]))
            )
        elif mode == 2:
            chunks.append(_read_alnum(source, source.read(COUNT_BITS["alnum"][group])))
        elif mode == 4:
            chunks.append(
                _read_byte(source, source.read(COUNT_BITS["byte"][group]), eci)
            )
        elif mode == 8:
            chunks.append(_read_kanji(source, source.read(COUNT_BITS["kanji"][group])))
        else:
            raise QrDecodeError(f"不支持的数据模式: {mode}")
    return "".join(chunks)


def _read_numeric(source: BitSource, count: int) -> str:
    digits: list[str] = []
    while count >= 3:
        digits.append(f"{source.read(10):03d}")
        count -= 3
    if count == 2:
        digits.append(f"{source.read(7):02d}")
    elif count == 1:
        digits.append(f"{source.read(4):01d}")
    return "".join(digits)


def _read_alnum(source: BitSource, count: int) -> str:
    chars: list[str] = []
    while count >= 2:
        value = source.read(11)
        chars.append(ALNUM_CHARS[value // 45])
        chars.append(ALNUM_CHARS[value % 45])
        count -= 2
    if count == 1:
        chars.append(ALNUM_CHARS[source.read(6)])
    return "".join(chars)


def _read_byte(source: BitSource, count: int, eci: int) -> str:
    raw = bytes(source.read(8) for _ in range(count))
    for encoding in _eci_encodings(eci):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace")


def _eci_encodings(eci: int) -> tuple[str, ...]:
    if eci == 20:
        return ("shift_jis", "utf-8")
    if eci in (25, 26):
        return ("utf-8", "latin-1")
    if eci == 3:
        return ("latin-1", "utf-8")
    return ("utf-8", "latin-1")


def _read_kanji(source: BitSource, count: int) -> str:
    raw = bytearray()
    for _ in range(count):
        value = source.read(13)
        assembled = (value // 0xC0) << 8 | (value % 0xC0)
        assembled += 0x8140
        raw.extend((assembled >> 8, assembled & 0xFF))
    try:
        return raw.decode("shift_jis")
    except UnicodeDecodeError:
        return raw.decode("shift_jis", errors="replace")


# ---------------------------------------------------------------------------
# Island APK 与二维码输入源（图片 / 剪贴板 / URL）
# ---------------------------------------------------------------------------


def _apk_summary(path: Path) -> tuple[int, str]:
    """返回 APK 的 (字节数, SHA-256)，并校验 PK 魔数。"""
    if not path.is_file():
        raise ToolError(f"APK 不存在: {path}")
    data = path.read_bytes()
    if not data.startswith(APK_MAGIC):
        raise ToolError(f"不是合法 APK/ZIP（魔数不是 PK\\x03\\x04）: {path}")
    return len(data), hashlib.sha256(data).hexdigest()


def find_latest_island_apk(directory: Path | None = None) -> Path:
    """取目录内版本号最大的 Island APK。"""
    directory = directory or ISLAND_APK_DIR
    if not directory.is_dir():
        raise ToolError(f"Island APK 目录不存在: {directory}")
    apks = sorted(
        (item for item in directory.iterdir() if item.suffix.lower() == ".apk"),
        key=lambda item: _version_key(item.name),
        reverse=True,
    )
    if not apks:
        raise ToolError(f"未在 {directory} 找到任何 .apk 文件")
    return apks[0]


def _version_key(name: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", name)
    return tuple(int(item) for item in numbers) if numbers else (0,)


def extract_url(text: str) -> str:
    """从任意文本中提取并校验 http(s) 链接。"""
    match = re.search(r"https?://[^\s\"'<>]+", text)
    if not match:
        raise SafetyError(f"未能从输入中提取到 http(s) 链接: {text[:120]!r}")
    url = match.group(0).rstrip(").,;]")
    if not re.match(r"^https?://[^\s/$.?#].[^\s]*$", url, re.IGNORECASE):
        raise SafetyError(f"二维码内容不是合法 URL: {url!r}")
    return url


def _clipboard_windows() -> tuple[str | None, bytes | None]:
    """读取 Windows 剪贴板（CF_UNICODETEXT / PNG / CF_DIB），零外部依赖。"""
    windll: Any = getattr(ctypes, "windll", None)
    if windll is None:
        return (None, None)
    user32: Any = windll.user32
    kernel32: Any = windll.kernel32
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.CloseClipboard.restype = ctypes.c_bool
    user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
    user32.IsClipboardFormatAvailable.restype = ctypes.c_bool
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
    user32.RegisterClipboardFormatW.restype = ctypes.c_uint
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.c_bool
    kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    cf_unicode_text = 13
    cf_dib = 8
    cf_png = user32.RegisterClipboardFormatW(ctypes.c_wchar_p("PNG"))

    for _attempt in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return (None, None)

    text: str | None = None
    image: bytes | None = None
    try:
        if user32.IsClipboardFormatAvailable(cf_unicode_text):
            handle = user32.GetClipboardData(cf_unicode_text)
            if handle:
                pointer = kernel32.GlobalLock(handle)
                if pointer:
                    try:
                        text = ctypes.wstring_at(pointer)
                    finally:
                        kernel32.GlobalUnlock(handle)
        for clip_format in (cf_png, cf_dib):
            if image is not None or not clip_format:
                continue
            if not user32.IsClipboardFormatAvailable(clip_format):
                continue
            handle = user32.GetClipboardData(clip_format)
            if not handle:
                continue
            size = int(kernel32.GlobalSize(handle))
            pointer = kernel32.GlobalLock(handle)
            if not pointer or size <= 0:
                if pointer:
                    kernel32.GlobalUnlock(handle)
                continue
            try:
                image = ctypes.string_at(pointer, size)
            finally:
                kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
    return (text, image)


def _clipboard_powershell() -> bytes | None:
    """ctypes 失败时用 PowerShell（STA）把剪贴板图片存成临时 PNG。"""
    if os.name != "nt" or shutil.which("pwsh") is None:
        return None
    temp_dir = Path(tempfile.mkdtemp(prefix="qrclip-"))
    target = temp_dir / "clipboard.png"
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$img=[System.Windows.Forms.Clipboard]::GetImage();"
        f"if($img){{$img.Save('{target.as_posix()}');'ok'}}"
    )
    try:
        result = default_runner(
            ["pwsh", "-NoProfile", "-STA", "-Command", script], 30.0
        )
        if result.ok and target.is_file():
            return target.read_bytes()
    finally:
        for leftover in temp_dir.glob("*"):
            leftover.unlink(missing_ok=True)
        temp_dir.rmdir()
    return None


def read_clipboard_payload() -> tuple[str | None, bytes | None]:
    text, image = _clipboard_windows()
    if image is None and text is None:
        image = _clipboard_powershell()
    return (text, image)


def resolve_url_from_sources(
    *,
    qr_image: str | None = None,
    use_clipboard: bool = False,
    url: str | None = None,
    backends: Sequence[str] | None = None,
) -> tuple[str, str]:
    """按输入源取出二维码 URL，返回 (url, 来源描述)。"""
    if url:
        return (extract_url(url), "--url 参数")
    if qr_image:
        path = Path(qr_image).expanduser()
        if not path.is_file():
            raise ToolError(f"二维码图片不存在: {path}")
        text = decode_qr_from_image(path.read_bytes(), backends=backends)
        return (extract_url(text), f"图片 {path}")
    if use_clipboard:
        clip_text, clip_image = read_clipboard_payload()
        if clip_image:
            try:
                decoded = decode_qr_from_image(clip_image, backends=backends)
                return (extract_url(decoded), "剪贴板图片")
            except QrDecodeError:
                if not clip_text:
                    raise
        if clip_text:
            return (extract_url(clip_text), "剪贴板文本")
        raise ToolError("剪贴板为空，既没有图片也没有文本")
    raise ToolError("请指定 --qr-image <图片> 或 --clipboard 或 --url <链接>")


# ---------------------------------------------------------------------------
# verify 全链路：注入 → 实时监控 → 汇总报告
# ---------------------------------------------------------------------------


@dataclass
class VerifyConfig:
    url: str
    source: str = "unknown"
    user_id: int | None = None
    timeout: float = 90.0
    interval: float = 1.5
    allow_main_user: bool = False
    confirm: bool = False


@dataclass(frozen=True)
class MonitorSample:
    index: int
    elapsed: float
    focus: WindowFocus
    focus_packages: tuple[str, ...]
    user_processes: tuple[ProcessEntry, ...]
    gms_processes: tuple[ProcessEntry, ...]
    android_id: str
    log_events: tuple[str, ...]


@dataclass
class VerificationReport:
    url: str
    source: str
    target: SpaceIdentity
    main: SpaceIdentity
    resolved_component: str | None
    samples: list[MonitorSample] = field(default_factory=list)
    log_events: list[str] = field(default_factory=list)
    completed: bool = False
    completion_reason: str = ""
    elapsed: float = 0.0

    @property
    def main_user_mode(self) -> bool:
        """目标与主空间为同一用户（``--allow-main-user`` 基线模式）。"""
        return self.target.user_id == self.main.user_id

    @property
    def isolation_ok(self) -> bool:
        if self.main_user_mode:
            return False
        if main_id_hidden_for_work_profile(self.main, self.target):
            return True
        return (
            self.target.android_id_valid
            and self.main.android_id_valid
            and not self.target.same_android_id(self.main)
        )

    @property
    def evidence_processes(self) -> list[str]:
        found: list[str] = []
        for sample in self.samples:
            for entry in sample.gms_processes:
                label = f"{entry.user} (pid {entry.pid}, {entry.name})"
                if label not in found:
                    found.append(label)
        return found

    @property
    def focus_trail(self) -> list[str]:
        trail: list[str] = []
        for sample in self.samples:
            component = sample.focus.component
            if component and component not in trail:
                trail.append(component)
        return trail

    def render(self) -> str:
        target, main = self.target, self.main
        gsf_known = target.gsf is not None and main.gsf is not None
        gsf_isolated = gsf_known and not target.same_gsf_id(main)
        if self.main_user_mode:
            isolation = "不适用（主空间基线模式，无隔离证明）"
        else:
            isolation = f"ANDROID_ID {'PASS' if self.isolation_ok else 'FAIL'} / " + (
                f"GSF ID {'PASS' if gsf_isolated else 'FAIL'}"
                if gsf_known
                else "GSF ID 未知"
            )
        lines: list[str] = [
            "=" * 78,
            "实机验证设备身份与完成报告",
            "=" * 78,
            f"验证链接        : {self.url}",
            f"二维码来源      : {self.source}",
            f"目标空间        : User {target.user_id} ({target.user_name})",
            f"接管组件        : {self.resolved_component or '(未解析到)'}",
            f"实测发送方进程  : {', '.join(self.evidence_processes) or '(未捕获)'}",
            f"生效 ANDROID_ID : {target.android_id or '(空)'}",
            f"生效 GSF ID     : {target.gsf or '(未取到)'}",
            f"主空间 ANDROID_ID: {main.android_id or '(空)'}",
            f"主空间 GSF ID   : {main.gsf or '(未取到)'}",
            f"身份隔离        : {isolation}",
            f"验证状态        : {'完成' if self.completed else '未完成'} ({self.completion_reason})",
            f"耗时/采样       : {self.elapsed:.1f}s / {len(self.samples)} 次",
            "-" * 78,
            "前台轨迹:",
        ]
        if main_id_hidden_for_work_profile(main, target):
            lines.append(
                "说明            : 主空间 ANDROID_ID 受系统隐私保护隐藏；"
                "目标工作资料拥有独立受管 ANDROID_ID。"
            )
        if self.focus_trail:
            lines.extend(f"  - {component}" for component in self.focus_trail)
        else:
            lines.append("  (未捕获到前台窗口)")
        lines.append("关键 Logcat 事件:")
        if self.log_events:
            lines.extend(f"  {event}" for event in self.log_events[-20:])
        else:
            lines.append("  (无匹配事件)")
        lines.append("=" * 78)
        return "\n".join(lines)


def run_verification(
    adb: AdbClient,
    cfg: VerifyConfig,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    writer: Callable[[str], None] = print,
    confirm_reader: Callable[[], str] | None = None,
) -> VerificationReport:
    """执行 verify 全链路：预检 → 注入 → 监控 → 报告。"""
    users = adb.list_users()
    if not users:
        raise SafetyError("未解析到任何 Android 用户，请确认设备已连接且已授权调试。")
    target_user = select_work_profile(
        users, requested_user=cfg.user_id, allow_main_user=cfg.allow_main_user
    )
    main_user = next((user for user in users if user.is_primary), None)
    if main_user is None:
        raise SafetyError("未找到主空间 User 0，无法做隔离对比。")

    writer(
        f"[1/4] 读取空间身份：User {main_user.user_id} 与 User {target_user.user_id} ..."
    )
    main_identity = read_space_identity(adb, main_user)
    target_identity = read_space_identity(adb, target_user)
    writer(
        format_identities(
            main_identity, target_identity, allow_main_user=cfg.allow_main_user
        )
    )
    assert_space_isolated(
        main_identity, target_identity, allow_main_user=cfg.allow_main_user
    )
    if not target_identity.gms_installed and not target_user.is_primary:
        raise SafetyError(
            f"目标空间 User {target_identity.user_id} 未安装 com.google.android.gms，"
            "无法在该空间完成验证。"
        )

    writer("[2/4] 清空 logcat，解析接管组件并注入二维码链接 ...")
    adb.logcat_clear()
    resolved = adb.resolve_activity(cfg.url, target_user.user_id)
    writer(f"  resolve-activity -> {resolved or '(未解析到，仍尝试 am start)'}")
    start = adb.start_view(cfg.url, target_user.user_id)
    start_text = start.output.strip()
    if not start.ok:
        raise AdbError(
            f"am start 失败 ({start.returncode}): {start_text or '(无输出)'}"
        )
    if "Error:" in start_text:
        raise AdbError(f"am start 返回错误: {start_text}")

    report = VerificationReport(
        url=cfg.url,
        source=cfg.source,
        target=target_identity,
        main=main_identity,
        resolved_component=resolved,
    )

    writer(
        f"[3/4] 实时监控 {cfg.timeout:.0f}s（间隔 {cfg.interval:.1f}s）："
        "前台窗口 / 进程空间归属 / ANDROID_ID 回跳 / logcat ..."
    )
    started = clock()
    seen_target_focus = False
    consecutive_off = 0
    max_samples = int(cfg.timeout / max(cfg.interval, 0.1)) + 5
    completed = False
    completion_reason = f"轮询超时 {cfg.timeout:.0f}s"
    index = 0
    hint_printed = False
    while True:
        elapsed = clock() - started
        if elapsed > cfg.timeout or index >= max_samples:
            break
        index += 1
        focus = adb.window_focus()
        entries = adb.ps_entries()
        user_procs = tuple(find_user_processes(entries, target_user.user_id))
        gms_procs = tuple(
            find_user_processes(entries, target_user.user_id, names=(*VERIFY_PACKAGES,))
        )
        android_id = adb.android_id(target_user.user_id)
        events = tuple(extract_verification_events(adb.logcat_dump(), limit=8))
        sample = MonitorSample(
            index=index,
            elapsed=elapsed,
            focus=focus,
            focus_packages=(focus.package,) if focus.package else (),
            user_processes=user_procs,
            gms_processes=gms_procs,
            android_id=android_id,
            log_events=events,
        )
        report.samples.append(sample)
        for event in events:
            if event not in report.log_events:
                report.log_events.append(event)

        if android_id and android_id != target_identity.android_id:
            if android_id == main_identity.android_id:
                raise SafetyError(
                    f"检测到 ANDROID_ID 回跳到主空间（{android_id}），立即中止验证。"
                )
            raise SafetyError(
                f"检测到 ANDROID_ID 漂移到非目标空间（{target_identity.android_id}"
                f" -> {android_id}），证据已失效，立即中止验证。"
            )

        focus_pkg = focus.package
        focus_user = focus.user_id
        focus_is_verify = bool(focus_pkg and focus_pkg in VERIFY_PACKAGES)
        if (
            focus_is_verify
            and focus_user is not None
            and focus_user != target_user.user_id
        ):
            raise SafetyError(
                f"检测到验证窗口跳出目标工作资料空间：前台焦点属于 User "
                f"{focus_user} 的 {focus_pkg}，已中止。"
            )
        target_app_procs = tuple(
            entry
            for entry in gms_procs
            if entry.user.startswith(f"u{target_user.user_id}_a")
        )
        if focus_user == target_user.user_id:
            on_target = focus_is_verify
        elif focus_user is None:
            on_target = focus_is_verify and bool(target_app_procs)
        else:
            on_target = False
        if on_target:
            seen_target_focus = True
            consecutive_off = 0
        elif seen_target_focus:
            consecutive_off += 1

        writer(_sample_line(sample, target_user.user_id))
        if seen_target_focus and consecutive_off >= 2:
            completed = True
            completion_reason = "验证界面已从前台关闭"
            break
        if cfg.confirm and seen_target_focus:
            if not hint_printed:
                writer("提示：手机端完成后按回车确认（Ctrl+C 中止监控）。")
                hint_printed = True
            if _enter_pressed(confirm_reader):
                completed = True
                completion_reason = "用户按回车确认完成"
                break
        sleeper(cfg.interval)

    report.completed = completed
    report.completion_reason = completion_reason
    report.elapsed = clock() - started
    writer("[4/4] 汇总报告 ...")
    return report


def _enter_pressed(confirm_reader: Callable[[], str] | None) -> bool:
    """确认完成：测试可注入 confirm_reader，真实运行用非阻塞回车检测。"""
    if confirm_reader is not None:
        try:
            confirm_reader()
        except (EOFError, KeyboardInterrupt):
            return False
        return True
    return _stdin_enter_pressed()


def _stdin_enter_pressed() -> bool:
    """非阻塞检测回车键，避免 confirm 模式阻塞轮询。"""
    try:
        if os.name == "nt":
            import msvcrt

            while msvcrt.kbhit():
                if msvcrt.getwch() in ("\r", "\n"):
                    return True
            return False
        import select

        ready, _write, _error = select.select([sys.stdin], [], [], 0)
        if ready:
            sys.stdin.readline()
            return True
    except (OSError, ValueError, ImportError):
        return False
    return False


def _sample_line(sample: MonitorSample, target_uid: int) -> str:
    focus = sample.focus.component or "(无前台)"
    procs = ", ".join(
        f"{entry.user}:{entry.name}" for entry in sample.gms_processes[:3]
    )
    missing = "" if sample.android_id else " ANDROID_ID=空!"
    return (
        f"  #{sample.index:<3} {sample.elapsed:6.1f}s focus={focus} "
        f"uid={target_uid} procs={procs or '(无)'}{missing}"
    )


# ---------------------------------------------------------------------------
# setup-island：把主空间 GMS / 浏览器 / 代理映射进壶中界（User 10）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PackageStep:
    """单条 ``install-existing`` / ``enable`` / ``browser-role`` 操作结果。"""

    package: str
    action: str
    ok: bool
    detail: str

    def render(self) -> str:
        mark = "OK  " if self.ok else "FAIL"
        return f"  {mark} {self.package:<32} {self.action:<17} {self.detail}"


@dataclass(frozen=True)
class IslandSetupPlan:
    """依据主空间实际安装状态推导出的映射清单。"""

    core: tuple[str, ...]
    optional_core: tuple[str, ...]
    browsers: tuple[str, ...]
    vpn: tuple[str, ...]
    absent_core: tuple[str, ...]


@dataclass
class IslandSetupReport:
    main_user_id: int
    target_user_id: int
    plan: IslandSetupPlan
    steps: list[PackageStep] = field(default_factory=list)
    enabled_packages: tuple[str, ...] = ()
    missing_core: tuple[str, ...] = ()
    missing_browsers: tuple[str, ...] = ()
    default_browser: str | None = None
    resolved_component: str | None = None
    vpn_uncovered: tuple[str, ...] = ()
    gsf: GsfId | None = None

    @property
    def core_ready(self) -> bool:
        return bool(self.plan.core) and not self.missing_core

    @property
    def browser_ready(self) -> bool:
        return browser_component_ready(self.resolved_component)

    @property
    def ok(self) -> bool:
        return self.core_ready and self.browser_ready

    def render(self) -> str:
        enabled = set(self.enabled_packages)
        core_line = ", ".join(
            f"{pkg}{'' if pkg in enabled else '(未启用)'}" for pkg in self.plan.core
        )
        lines: list[str] = [
            "=" * 78,
            "Island（壶中界）依赖映射报告",
            "=" * 78,
            f"主空间         : User {self.main_user_id}",
            f"目标空间       : User {self.target_user_id}",
            f"核心组件       : {core_line or '(无)'}",
            f"浏览器         : {', '.join(self.plan.browsers) or '(无)'}",
            f"活跃 VPN       : {', '.join(self.plan.vpn) or '(未检测到)'}",
            "-" * 78,
            "执行步骤:",
        ]
        lines.extend(step.render() for step in self.steps)
        lines.extend(
            [
                "-" * 78,
                "校验:",
                f"  GMS 核心启用 : {'PASS' if self.core_ready else 'FAIL'}"
                + (
                    f"（缺失: {', '.join(self.missing_core)}）"
                    if self.missing_core
                    else ""
                ),
                f"  默认浏览器   : {self.default_browser or '(未设置)'}",
                f"  resolve-activity: {self.resolved_component or '(无法解析)'}",
                f"  https 接管   : {'PASS' if self.browser_ready else 'FAIL'}"
                + (
                    "（仍是系统选择器，链接会弹选择框）"
                    if self.resolved_component == RESOLVER_COMPONENT
                    else ""
                ),
                f"  GSF ID       : {self.gsf or '(未取到，需 GMS 初始化后由系统生成)'}",
            ]
        )
        if self.vpn_uncovered:
            lines.append("  VPN 覆盖     : 未覆盖 " + ", ".join(self.vpn_uncovered))
            lines.append(
                "                 提示: 在主空间把梯子断开重连一次，让 VPN 重新收集"
                "壶中界的 UID；无需在壶中界再开第二个 VPN（同时只能生效一个）。"
            )
        elif self.plan.vpn:
            lines.append("  VPN 覆盖     : 主空间 VPN 已覆盖目标空间的上述应用")
        lines.append("-" * 78)
        lines.append(
            f"结论: {'PASS，壶中界已具备 Google 服务与浏览器接管能力' if self.ok else 'FAIL，请按上面缺失项处理'}"
        )
        lines.append("=" * 78)
        return "\n".join(lines)


STEP_PROBLEM_MARKERS: tuple[str, ...] = (
    "Exception",
    "Error",
    "Unknown package",
    "not installed",
    "Failure",
)


def _first_line(text: str) -> str:
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped:
            return stripped[:160]
    return ""


def _command_problem(result: CommandResult) -> str | None:
    """把 adb shell 的失败（非零退出 / 异常文本）归一成一行原因。"""
    if not result.ok:
        return _first_line(result.output) or f"returncode={result.returncode}"
    for marker in STEP_PROBLEM_MARKERS:
        if marker in result.output:
            line = next(
                (item.strip() for item in result.output.splitlines() if marker in item),
                "",
            )
            return (line or _first_line(result.output))[:160]
    return None


def _map_package(
    adb: AdbClient,
    package: str,
    user_id: int,
    *,
    unhide: bool,
) -> list[PackageStep]:
    """依次执行 ``install-existing`` → ``enable``（可选 ``unhide``）。"""
    steps: list[PackageStep] = []
    installed = adb.install_existing(package, user_id)
    problem = _command_problem(installed)
    steps.append(
        PackageStep(
            package,
            "install-existing",
            problem is None,
            problem or _first_line(installed.output) or "ok",
        )
    )
    if problem is not None:
        return steps
    enabled = adb.enable_package(package, user_id)
    problem = _command_problem(enabled)
    steps.append(
        PackageStep(
            package,
            "enable",
            problem is None,
            problem or _first_line(enabled.output) or "ok",
        )
    )
    if unhide:
        hidden = adb.unhide_package(package, user_id)
        problem = _command_problem(hidden)
        steps.append(
            PackageStep(
                package,
                "unhide",
                problem is None,
                problem or _first_line(hidden.output) or "ok",
            )
        )
    return steps


def build_island_plan(
    main_packages: Sequence[str],
    vpn_packages: Sequence[str],
    *,
    include_vpn: bool = True,
    browser: str | None = None,
) -> IslandSetupPlan:
    """按主空间实装情况推导需要映射进壶中界的包。"""
    installed = set(main_packages)
    absent_core = tuple(pkg for pkg in ISLAND_REQUIRED_CORE if pkg not in installed)
    if PKG_GMS in absent_core or PKG_GSF in absent_core:
        raise ToolError(
            "主空间 User 0 缺少 Google 服务框架（com.google.android.gms / "
            "com.google.android.gsf），无法为壶中界映射 GMS。"
        )
    if browser is not None:
        if browser not in installed:
            available = [pkg for pkg in BROWSER_PREFERENCES if pkg in installed]
            raise ToolError(
                f"主空间未安装指定浏览器 {browser}；可用: "
                f"{', '.join(available) or '(无)'}"
            )
        browsers = (browser,)
    else:
        browsers = tuple(pkg for pkg in BROWSER_PREFERENCES if pkg in installed)
    if not browsers:
        raise ToolError(
            "主空间未找到受支持的浏览器（Chrome / Edge / Firefox / Via / OPPO 浏览器），"
            "无法在壶中界接管 https 链接。"
        )
    vpn = tuple(pkg for pkg in vpn_packages if pkg in installed) if include_vpn else ()
    return IslandSetupPlan(
        core=tuple(pkg for pkg in ISLAND_REQUIRED_CORE if pkg in installed),
        optional_core=tuple(pkg for pkg in ISLAND_OPTIONAL_CORE if pkg in installed),
        browsers=browsers,
        vpn=vpn,
        absent_core=absent_core,
    )


def run_island_setup(
    adb: AdbClient,
    *,
    user_id: int | None = None,
    include_vpn: bool = True,
    browser: str | None = None,
    unhide: bool = False,
    writer: Callable[[str], None] = print,
) -> IslandSetupReport:
    """探测主空间依赖 → 在壶中界映射启用 → 设置默认浏览器 → 校验接管。"""
    users = adb.list_users()
    if not users:
        raise SafetyError("未解析到任何 Android 用户，请确认设备已连接且已授权调试。")
    target = select_work_profile(users, requested_user=user_id)
    main = next((user for user in users if user.is_primary), None)
    if main is None:
        raise SafetyError("未找到主空间 User 0，无法确定要映射的依赖来源。")

    writer(f"[1/5] 扫描主空间 User {main.user_id} 的 GMS / 浏览器 / 活跃 VPN ...")
    main_packages = adb.list_package_names(main.user_id)
    vpn_packages = adb.active_vpn_packages()
    plan = build_island_plan(
        main_packages,
        vpn_packages,
        include_vpn=include_vpn,
        browser=browser,
    )
    writer(f"  核心组件: {', '.join(plan.core) or '(无)'}")
    writer(f"  浏览器  : {', '.join(plan.browsers)}")
    writer(f"  活跃 VPN: {', '.join(plan.vpn) or '(未检测到)'}")
    if plan.absent_core:
        writer(f"  注意    : 主空间缺少 {', '.join(plan.absent_core)}（跳过）")

    report = IslandSetupReport(
        main_user_id=main.user_id,
        target_user_id=target.user_id,
        plan=plan,
    )
    writer(f"[2/5] 为 User {target.user_id} 映射并启用依赖 ...")
    for package in (*plan.core, *plan.optional_core, *plan.browsers, *plan.vpn):
        steps = _map_package(adb, package, target.user_id, unhide=unhide)
        report.steps.extend(steps)
        for step in steps:
            writer(step.render())

    writer(f"[3/5] 设置 User {target.user_id} 默认浏览器 ...")
    if plan.browsers:
        role = adb.grant_browser_role(plan.browsers[0], target.user_id)
        problem = _command_problem(role)
        report.steps.append(
            PackageStep(
                plan.browsers[0],
                "browser-role",
                problem is None,
                problem or "default browser",
            )
        )
        holders = adb.browser_role_holders(target.user_id)
        report.default_browser = holders[0] if holders else None
    writer(f"  默认浏览器: {report.default_browser or '(未设置)'}")

    writer(f"[4/5] 校验 User {target.user_id} 的启用状态与 https 接管 ...")
    report.enabled_packages = tuple(
        adb.list_package_names(target.user_id, enabled_only=True)
    )
    enabled = set(report.enabled_packages)
    report.missing_core = tuple(pkg for pkg in plan.core if pkg not in enabled)
    report.missing_browsers = tuple(pkg for pkg in plan.browsers if pkg not in enabled)
    report.resolved_component = adb.resolve_activity(ISLAND_PROBE_URL, target.user_id)
    report.gsf = adb.gsf_id(target.user_id)

    uid_map = adb.package_uids(target.user_id)
    vpn_ranges = adb.vpn_uid_ranges()
    if vpn_ranges:
        report.vpn_uncovered = tuple(
            pkg
            for pkg in (*plan.core, *plan.browsers)
            if (uid := uid_map.get(pkg)) is not None
            and not uid_in_ranges(uid, vpn_ranges)
        )

    writer("[5/5] 汇总报告 ...")
    return report


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------


def cmd_devices(args: argparse.Namespace) -> int:
    adb = AdbClient(args.adb_path, serial=args.serial)
    devices = adb.list_devices()
    if not devices:
        print("未发现任何 ADB 设备。请检查 USB 调试或先执行 connect 子命令。")
        return 1
    print(f"{'序列号':<30}{'状态':<12}可用")
    print("-" * 54)
    for device in devices:
        print(f"{device.serial:<30}{device.state:<12}{'是' if device.usable else '否'}")
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    adb = AdbClient(args.adb_path, serial=None)
    host = (args.host or "").strip() or None
    pair_addr = normalize_adb_address(args.pair, host)
    connect_addr = normalize_adb_address(args.connect_addr, host)
    if not pair_addr and not connect_addr:
        raise ToolError(
            "请指定 --pair <ip:port|端口> --code <code> 或 --connect <ip:port|端口>"
            "（可配合 --host <手机局域网IP> 只填端口）"
        )
    if pair_addr and not args.code:
        raise ToolError("--pair 必须同时提供 --code <配对码>")
    if pair_addr and not _adb_supports_pair(str(adb.adb_path)):
        raise ToolError(
            f"当前 adb 不支持 pair 命令（platform-tools < "
            f"{ADB_PAIR_MIN_PLATFORM_VERSION}）: {adb.adb_path}\n"
            "请升级 platform-tools，或用 --adb-path 指向新版 adb。"
        )
    failures: list[str] = []
    if pair_addr:
        print(f"配对 {pair_addr} ...")
        output = adb.pair(pair_addr, args.code)
        print(output)
        if _adb_action_failed(output):
            failures.append(pair_addr)
    if connect_addr:
        print(f"连接 {connect_addr} ...")
        output = adb.connect(connect_addr)
        print(output)
        if _adb_action_failed(output):
            failures.append(connect_addr)
    devices = adb.list_devices()
    for device in devices:
        print(f"  {device.serial}\t{device.state}")
    if host is None:
        for address in failures:
            hint = vpn_host_hint(address)
            if hint:
                print(hint, file=sys.stderr)
    return 0 if (devices or not failures) else 1


def cmd_install_island(args: argparse.Namespace) -> int:
    apk = Path(args.apk).expanduser() if args.apk else find_latest_island_apk()
    size, digest = _apk_summary(apk)
    print(f"APK   : {apk}")
    print(f"大小  : {size} 字节")
    print(f"SHA256: {digest}")
    adb = AdbClient(args.adb_path, serial=args.serial)
    print("安装中（adb install -r）...")
    output = adb.install(apk)
    print(output)
    return 0 if "Success" in output else 1


def cmd_inspect_ids(args: argparse.Namespace) -> int:
    adb = AdbClient(args.adb_path, serial=args.serial)
    users = adb.list_users()
    if not users:
        raise SafetyError("未解析到任何 Android 用户，请确认设备已连接。")
    print("已发现用户空间:")
    for user in users:
        print(f"  - {user.label}  flags={user.flags or '-'}")
    main_user = next((user for user in users if user.is_primary), None)
    if main_user is None:
        raise SafetyError("未找到主空间 User 0。")
    work_user = next((user for user in users if user.is_work_profile), None)
    if work_user is None:
        raise SafetyError(
            "未找到 Island 工作资料（如 User 10）。请先在 Island 中创建并启用工作资料。"
        )
    print()
    main_identity = read_space_identity(adb, main_user)
    work_identity = read_space_identity(adb, work_user)
    print(format_identities(main_identity, work_identity))
    isolated = work_identity.android_id_valid and (
        not work_identity.same_android_id(main_identity)
        or main_id_hidden_for_work_profile(main_identity, work_identity)
    )
    return 0 if isolated else 1


def cmd_setup_island(args: argparse.Namespace) -> int:
    adb = AdbClient(args.adb_path, serial=args.serial)
    report = run_island_setup(
        adb,
        user_id=args.user,
        include_vpn=not args.no_vpn,
        browser=args.browser,
        unhide=args.unhide,
    )
    print()
    print(report.render())
    return 0 if report.ok else 1


def cmd_verify(args: argparse.Namespace) -> int:
    url, source = resolve_url_from_sources(
        qr_image=args.qr_image,
        use_clipboard=args.clipboard,
        url=args.url,
        backends=args.backend or None,
    )
    print(f"二维码链接: {url}")
    print(f"来源      : {source}")
    cfg = VerifyConfig(
        url=url,
        source=source,
        user_id=args.user,
        timeout=args.timeout,
        interval=args.interval,
        allow_main_user=args.allow_main_user,
        confirm=args.confirm,
    )
    adb = AdbClient(args.adb_path, serial=args.serial)
    report = run_verification(adb, cfg)
    print()
    print(report.render())
    return 0 if report.completed else 2


# ---------------------------------------------------------------------------
# argparse 入口
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_qr_adb.py",
        description=(
            "Island（炼妖壶）工作资料 + ADB 实机 verify-account 全链路工具："
            "二维码解码、双空间身份对比、注入挑战链接、实时取证与完成报告。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python tools/verify_qr_adb.py devices\n"
            "  python tools/verify_qr_adb.py connect --pair 192.168.1.7:41234 --code 123456\n"
            "  python tools/verify_qr_adb.py connect --host 192.168.1.100"
            " --pair 41234 --code 123456 --connect 41235\n"
            "  python tools/verify_qr_adb.py setup-island\n"
            "  python tools/verify_qr_adb.py inspect-ids\n"
            "  python tools/verify_qr_adb.py verify --qr-image qr.png\n"
            "  python tools/verify_qr_adb.py verify --clipboard --timeout 120\n"
        ),
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--adb-path", default=None, help="adb 可执行文件路径（默认自动查找）"
    )
    common.add_argument(
        "-s",
        "--serial",
        default=argparse.SUPPRESS,
        help="指定设备序列号（多设备时使用；也可写在子命令前）",
    )
    parser.add_argument(
        "-s",
        "--serial",
        default=argparse.SUPPRESS,
        help="指定设备序列号（多设备时使用；也可写在子命令后）",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    devices = subparsers.add_parser(
        "devices", parents=[common], help="列出已连接的 ADB 设备"
    )
    devices.set_defaults(func=cmd_devices)

    connect = subparsers.add_parser(
        "connect",
        parents=[common],
        help="无线调试配对 / 连接（--pair + --code 或 --connect）",
    )
    connect.add_argument(
        "--host",
        default=None,
        help=(
            "手机真实局域网 IP（如 192.168.1.100）；指定后 --pair/--connect 可只填端口，"
            "或自动把手机 VPN 虚拟网卡 IP 替换成该 IP"
        ),
    )
    connect.add_argument("--pair", default=None, help="配对地址 ip:port 或端口号")
    connect.add_argument("--code", default=None, help="无线调试配对码")
    connect.add_argument(
        "--connect", dest="connect_addr", default=None, help="连接地址 ip:port 或端口号"
    )
    connect.set_defaults(func=cmd_connect)

    install = subparsers.add_parser(
        "install-island",
        parents=[common],
        help="安装 data/tools/island 下最新的 Island APK",
    )
    install.add_argument(
        "--apk", default=None, help="指定 APK 路径（默认取目录内最新版）"
    )
    install.set_defaults(func=cmd_install_island)

    inspect_ids = subparsers.add_parser(
        "inspect-ids",
        parents=[common],
        help="对比主空间与 Island 工作资料的 ANDROID_ID / GSF ID",
    )
    inspect_ids.set_defaults(func=cmd_inspect_ids)

    setup_island = subparsers.add_parser(
        "setup-island",
        parents=[common],
        help="把主空间 GMS / 浏览器 / 活跃 VPN 映射并启用进壶中界（User 10）",
    )
    setup_island.add_argument(
        "--user", type=int, default=None, help="目标空间 uid（默认自动找工作资料）"
    )
    setup_island.add_argument(
        "--browser", default=None, help="指定浏览器包名（默认按优先级自动选择）"
    )
    setup_island.add_argument(
        "--no-vpn", action="store_true", help="不映射当前活跃的 VPN / 代理应用"
    )
    setup_island.add_argument(
        "--unhide",
        action="store_true",
        help="额外执行 cmd package unhide（ColorOS 无 MANAGE_USERS，失败可忽略）",
    )
    setup_island.set_defaults(func=cmd_setup_island)

    verify = subparsers.add_parser(
        "verify",
        parents=[common],
        help="全链路：解码二维码 → 注入工作资料 → 实时取证 → 报告",
    )
    verify.add_argument("--qr-image", default=None, help="二维码图片路径（PNG/BMP）")
    verify.add_argument(
        "--clipboard", action="store_true", help="从 Windows 剪贴板读取截图或链接"
    )
    verify.add_argument("--url", default=None, help="直接给二维码中的链接（跳过解码）")
    verify.add_argument(
        "--user", type=int, default=None, help="目标空间 uid（默认自动找工作资料）"
    )
    verify.add_argument(
        "--timeout", type=float, default=90.0, help="监控时长（秒，默认 90）"
    )
    verify.add_argument(
        "--interval", type=float, default=1.5, help="采样间隔（秒，默认 1.5）"
    )
    verify.add_argument(
        "--allow-main-user",
        action="store_true",
        help="允许目标空间为主空间 User 0（默认禁止）",
    )
    verify.add_argument(
        "--confirm", action="store_true", help="进入验证界面后等待回车确认完成"
    )
    verify.add_argument(
        "--backend",
        action="append",
        choices=sorted(DECODE_BACKENDS),
        help="指定二维码解码后端（可多次指定；默认全部尝试）",
    )
    verify.set_defaults(func=cmd_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not hasattr(args, "serial"):
        args.serial = None
    try:
        return int(args.func(args))
    except ToolError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
