"""``tools/verify_qr_adb.py`` 的离线单元测试。

覆盖：``pm list users`` 解析、ANDROID_ID / GSF ID 解析、主副空间防串味拦截、
``ps -A`` 进程归属核验、``dumpsys window`` 前台焦点解析、二维码图片解码
（内置纯 Python 解码器 + RS 纠错 + 旋转）以及 verify 全链路状态机。
"""

from __future__ import annotations

import base64
import importlib.util
import struct
import sys
import zlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

TOOL_PATH = Path(__file__).resolve().parents[2] / "tools" / "verify_qr_adb.py"
_SPEC = importlib.util.spec_from_file_location("verify_qr_adb", TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
tool: Any = importlib.util.module_from_spec(_SPEC)
sys.modules["verify_qr_adb"] = tool
_SPEC.loader.exec_module(tool)

QR_A_URL = (
    "https://accounts.google.com/signin/verify-account"
    "?continue=https%3A%2F%2Faistudio.google.com&challenge=abc123XYZ"
)
QR_A_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAKsAAACrAQAAAAAxk1G0AAACkUlEQVR42u1XMa6dMBBc4oIOLoDka7jzlfAF"
    "nuECcCV3voYlXwA6FxabWX4RRfopsm2Cnl5hidV4d3ZmIP7uKfRvH19EsS1D5ofMneudaODyg759/nBMfHP/"
    "jPUaaeaFXEG1W1EkLSvZzduLzOV5zxRURQJ38mUa+5zMqS8inaGxfFxlXRHugevjKWa7UY+qngBDyMuvn2o6"
    "eG6WSwHJ3cr8Tv7ve0IfKmgsUZ/cMieKCiTZPCRUeTxg1MtbBZJHJst3xojrRmVIZldcp9ltBD3q2cyd6s1W"
    "VQQ8MZz6Sn1uGE1X9ARUPxMIXzf01qPaMmsaiy1GbzFoe9ASmo4nBkRFV0HdydkbfNMUeXfH8+ZKREGsj2LE"
    "DiIgZPt4QYJ7KabDkLVWPqO9kyBhrooFhCruuQ8ZRMUmCrBdw9gSmggatHEi4YlKlOrhe0h8MshWH7KnhmzA"
    "YLkVGoEKeqtS+2Qv7GDuMUEh684aJJhOaG9XU4/N7iqyYYtleVuJmWZ0mFV6IjqPGy1E9aACD9o1Qi1qQCCt"
    "o6DVWCjb6qDSwvYJMFJR9OSCuopt0TpiuPUYVQv42pYw1ot5QW818gj7c+UDOUrioeuoW0BayRyuzyxIgEcT"
    "LbLBW+T4cgg5ZXWsQoJxmDOVN+TIPp4axlaGicOIk0AKKsZeiFjNcIZTwDtQTWUZ4ptV1gfeAS92ZdbwpATc"
    "BaPxWAHMWsOTR9YHgl8l8mVIpcbQX0VaYsMu9+k9irqklF9FwpSbpIJTldlihm2hTj0ltqnyiXgEkgnFNxso"
    "8wkSNfy3dai9ZAyl2kvyHDJUhYYm6WLWFWl8SFrjjYCq6L4yvnx8GyUV4D8oEzUcBwYkqQAatat48v9D8rfj"
    "nygtjeOePjzzAAAAAElFTkSuQmCC"
)
QR_B_URL = "https://g.co/verify?c=ZXCVBNM123"
QR_B_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAHsAAAB7AQAAAAB8vMLSAAABWElEQVR42t1VPY6GIBB9rgXd5wVIuAYdV9IL"
    "6HoBvRId15iEC2BHQZwdvm53m7HcJcYYwgPez4zg74PwlycKMBu+w8gRA9MHfoxfE+CL2xzakBwn+dZBol19"
    "PitNCYseEvKZ7PAIAouATQ/htjFNlQY1F1FsSbY/asX6iLRUd75V10BuUbiKYg2BdyXE5IIGTwNrL1aCFfrob"
    "ztpFXN75Rvjqacf8fK5hHwbLRdZfHhaDRdDei5TzBdDUKcS4p34MiVXgj4wsn8+jJ29VUPGK2GIbUq0qTO2"
    "pfEGvUDKU27vdnZHaDDYtL6I9QSf+w21JZZvb+fAnKzylCvRkiQw/Bm0vlxR1ucrugNaLqWzFke6dPqqvCLN"
    "Xrhrw9+7pR+Lke6nr5e2+iY+SlVuD7olXgYz9L6IHe5MmesDyBpoi7T6cVdzWVhi3JaKB0mu4rtA2qTvlv/j"
    "9/oF54rBlyWCu9wAAAAASUVORK5CYII="
)


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body))
        + kind
        + body
        + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    )


def _png_build(
    width: int, height: int, color_type: int, raw: bytes, bit_depth: int = 8
) -> bytes:
    return (
        tool.PNG_SIGNATURE
        + _png_chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0),
        )
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def _png_bytes(width: int, height: int, pixels: bytes) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * width : (y + 1) * width])
    return _png_build(width, height, 0, bytes(raw))


def _png_channel_bytes(gray: Any, color_type: int, samples: Any) -> bytes:
    raw = bytearray()
    for y in range(gray.height):
        raw.append(0)
        for x in range(gray.width):
            raw.extend(samples(gray.pixels[y * gray.width + x]))
    return _png_build(gray.width, gray.height, color_type, bytes(raw))


def _png_channel16_bytes(gray: Any, color_type: int, samples: Any) -> bytes:
    raw = bytearray()
    for y in range(gray.height):
        raw.append(0)
        for x in range(gray.width):
            for sample in samples(gray.pixels[y * gray.width + x]):
                raw.extend(struct.pack(">H", sample * 257))
    return _png_build(gray.width, gray.height, color_type, bytes(raw), bit_depth=16)


def _bmp_bytes(width: int, height: int, pixels: bytes) -> bytes:
    stride = ((width * 3 + 3) // 4) * 4
    body = bytearray()
    for row in range(height - 1, -1, -1):
        line = bytearray()
        for column in range(width):
            value = pixels[row * width + column]
            line.extend((value, value, value))
        line.extend(b"\x00" * (stride - len(line)))
        body.extend(line)
    header = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(body), 0, 0, 54)
    info = struct.pack(
        "<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(body), 0, 0, 0, 0
    )
    return header + info + bytes(body)


def _flip_blocks(gray: Any, count: int) -> bytes:
    """在图像中部翻转若干 4x4 像素块，避开定位图形与静区。"""
    width, height = gray.width, gray.height
    pixels = bytearray(gray.pixels)
    margin = max(6, width // 5)
    span = max(1, width - 2 * margin - 4)
    for offset in range(count):
        left = margin + (offset * 13) % span
        top = margin + (offset * 29) % span
        for y in range(top, min(top + 4, height)):
            for x in range(left, min(left + 4, width)):
                index = y * width + x
                pixels[index] = 255 - pixels[index]
    return _png_bytes(width, height, bytes(pixels))


def _rotate180(gray: Any) -> bytes:
    width, height = gray.width, gray.height
    rotated = bytearray(width * height)
    for y in range(height):
        row = gray.pixels[y * width : (y + 1) * width]
        target = (height - 1 - y) * width
        rotated[target : target + width] = row[::-1]
    return _png_bytes(width, height, bytes(rotated))


def _ok(stdout: str = "") -> Any:
    return tool.CommandResult(args=(), returncode=0, stdout=stdout, stderr="")


USERS_OUTPUT = """Users:
\tUserInfo{0:机主:c13} running
\tUserInfo{10:工作资料:1030} running
\tUserInfo{11:访客:400} running
"""

MAIN_ANDROID_ID = "0123456789abcdef"
WORK_ANDROID_ID = "fedcba9876543210"


def _focus(user: int, pkg: str, activity: str) -> str:
    return (
        f"  mCurrentFocus=Window{{1b2c3d4 u{user} {pkg}/{activity}}}\n"
        f"  mFocusedApp=ActivityRecord{{5e6f7a8 u{user} {pkg}/{activity} t42}}\n"
    )


LAUNCHER_FOCUS = _focus(0, "com.miui.home", "com.miui.home.launcher.Launcher")
GMS_FOCUS = _focus(
    10, "com.google.android.gms", "com.google.android.gms.auth.ui.HtmlActivity"
)
MAIN_GMS_FOCUS = _focus(
    0, "com.google.android.gms", "com.google.android.gms.auth.ui.HtmlActivity"
)
UNLABELED_GMS_FOCUS = (
    "  mCurrentFocus=Window{1b2c3d4 com.google.android.gms/"
    "com.google.android.gms.auth.ui.HtmlActivity}\n"
)
THIRD_PARTY_GMS_FOCUS = _focus(
    11, "com.google.android.gms", "com.google.android.gms.auth.ui.HtmlActivity"
)

PS_OUTPUT = """USER           PID  PPID     VSZ    RSS WCHAN            ADDR S NAME
root             1     0 1234567   9876 0                   0 S init
u0_a123       2345   631 2345678  98765 0                   0 S com.google.android.gms
u10_a231      3456   640 2345678  98765 0                   0 S com.google.android.gms
u10_a232      3457   640 2345678  98765 0                   0 S com.google.android.gms.unstable
u10_system    3500   640 2345678  98765 0                   0 S com.android.chrome
"""

PS_OUTPUT_NO_WORK = """USER           PID  PPID     VSZ    RSS WCHAN            ADDR S NAME
root             1     0 1234567   9876 0                   0 S init
u0_a123       2345   631 2345678  98765 0                   0 S com.google.android.gms
u0_a124       2346   631 2345678  98765 0                   0 S com.android.chrome
"""

LOGCAT_OUTPUT = """05-01 10:00:00.000  3456  3456 I DroidGuard: Loading VM
05-01 10:00:01.000  2345  2345 W Recaptcha: unrelated main space
05-01 10:00:02.000   640   640 I ActivityTaskManager: Displayed com.google.android.gms/.auth.ui.HtmlActivity
"""


class FakeAdb:
    """按命令行返回固定输出的假 adb，可编程模拟前台切换与 ANDROID_ID 回跳。"""

    def __init__(
        self,
        *,
        users: str = USERS_OUTPUT,
        main_id: str = MAIN_ANDROID_ID,
        work_id: str = WORK_ANDROID_ID,
        focus_sequence: Sequence[str] | None = None,
        drift_after: int | None = None,
        am_start_output: str = "Status: ok\nActivity: com.android.chrome/.Main\n",
        ps_output: str = PS_OUTPUT,
        user0_readable: bool = True,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.users = users
        self.main_id = main_id
        self.work_id = work_id
        self.focus_sequence = list(
            focus_sequence or [GMS_FOCUS] * 2 + [LAUNCHER_FOCUS] * 4
        )
        self.drift_after = drift_after
        self.am_start_output = am_start_output
        self.ps_output = ps_output
        self.user0_readable = user0_readable
        self.focus_index = 0
        self.work_id_reads = 0

    def __call__(self, args: Sequence[str], timeout: float) -> Any:
        argv = [str(item) for item in args]
        self.calls.append(tuple(argv))
        rest = argv[1:]
        if rest[:1] == ["-s"]:
            rest = rest[2:]
        if rest == ["devices"]:
            return _ok("List of devices attached\nR58M12345\tdevice\n")
        if rest[:1] != ["shell"]:
            return _ok("")
        return self._shell(rest[1])

    def _shell(self, command: str) -> Any:
        if command == "getprop ro.serialno":
            return _ok("R58M12345\n")
        if command == "pm list users":
            return _ok(self.users)
        if (
            command == "pm list packages --user 10"
            or command == "pm list packages --user 0"
        ):
            return _ok("package:com.google.android.gms\npackage:com.android.chrome\n")
        if command.startswith("dumpsys package com.google.android.gms"):
            return _ok("Packages:\n  versionName=25.18.32 (190400-123456789)\n")
        if command == "ps -A":
            return _ok(self.ps_output)
        if command == "logcat -c":
            return _ok("")
        if command.startswith("logcat -d"):
            return _ok(LOGCAT_OUTPUT)
        if command == "dumpsys window":
            index = min(self.focus_index, len(self.focus_sequence) - 1)
            self.focus_index += 1
            return _ok(self.focus_sequence[index])
        if command.startswith("settings --user 0 get secure android_id"):
            if not self.user0_readable:
                return _ok("\n")
            return _ok(self.main_id + "\n")
        if command == "settings get secure android_id":
            return _ok(self.main_id + "\n")
        if command.startswith("settings --user 10 get secure android_id"):
            self.work_id_reads += 1
            if self.drift_after is not None and self.work_id_reads > self.drift_after:
                return _ok(self.main_id + "\n")
            return _ok(self.work_id + "\n")
        if "content query" in command and "content://settings/secure" in command:
            if not self.main_id:
                return _ok("No result found.\n")
            return _ok(
                "Row: 0 _id=40, name=android_id, "
                f"value={self.main_id}, is_preserved_in_restore=false\n"
            )
        if "content query" in command:
            if "--user 0" in command:
                return _ok("Row: 0 value=1111111111111111111\n")
            return _ok("Row: 0 value=2222222222222222222\n")
        if "resolve-activity" in command:
            return _ok(
                "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true\n"
                "com.android.chrome/com.google.android.apps.chrome.Main\n"
            )
        if command.startswith("am start"):
            return _ok(self.am_start_output)
        return _ok("")


def _client(runner: FakeAdb) -> Any:
    return tool.AdbClient(Path("adb"), runner=runner, timeout=1.0)


def _clock():
    state = {"now": 0.0}

    def clock() -> float:
        return state["now"]

    def sleeper(seconds: float) -> None:
        state["now"] += seconds

    return clock, sleeper, state


# ---------------------------------------------------------------------------
# 解析：用户 / 身份 / 进程 / 前台窗口
# ---------------------------------------------------------------------------


def test_parse_pm_list_users_detects_work_profile() -> None:
    users = tool.parse_pm_list_users(USERS_OUTPUT)
    assert [user.user_id for user in users] == [0, 10, 11]
    assert users[0].is_primary and not users[0].is_work_profile
    assert users[1].is_work_profile, users[1]
    assert users[2].raw_flags == 0x400
    assert not users[2].is_work_profile
    assert "工作资料" in users[1].label


def test_parse_pm_list_users_handles_empty_output() -> None:
    assert tool.parse_pm_list_users("Users:\n") == []
    assert tool.parse_pm_list_users("") == []


def test_parse_android_id_normalizes_and_rejects_placeholder() -> None:
    assert tool.parse_android_id("0123456789ABCDEF\n") == "0123456789abcdef"
    assert tool.parse_android_id("null\n") == ""
    assert tool.parse_android_id("unknown\n") == ""
    assert tool.parse_android_id("0\n") == ""
    assert tool.parse_android_id("") == ""
    assert tool.parse_android_id("garbage android id with spaces\n") == ""
    assert tool.parse_android_id("Error: no settings provider\n") == ""
    assert tool.parse_android_id("0123456\n") == ""


def test_parse_android_id_accepts_coloros_short_hex() -> None:
    assert tool.parse_android_id("0123456789abcde\n") == "0123456789abcde"
    assert tool.parse_android_id("FEDCBA9876543210\n") == "fedcba9876543210"


def test_parse_android_id_value_field_from_content_query() -> None:
    row = (
        "Row: 0 _id=40, name=android_id, value=0123456789abcde, "
        "is_preserved_in_restore=false\n"
    )
    assert tool.parse_android_id_value_field(row) == "0123456789abcde"
    assert tool.parse_android_id_value_field("No result found.") == ""


def test_android_id_falls_back_for_protected_user_zero() -> None:
    runner = FakeAdb(user0_readable=False)
    assert _client(runner).android_id(0) == MAIN_ANDROID_ID


def test_android_id_falls_back_to_content_query_when_settings_empty() -> None:
    class ContentOnly(FakeAdb):
        def _shell(self, command: str) -> Any:
            if command.startswith("settings --user 0 get secure android_id"):
                return _ok("\n")
            if command == "settings get secure android_id":
                return _ok("null\n")
            return super()._shell(command)

    runner = ContentOnly()
    assert _client(runner).android_id(0) == MAIN_ANDROID_ID


def test_android_id_does_not_fall_back_for_work_profile() -> None:
    class WorkEmpty(FakeAdb):
        def _shell(self, command: str) -> Any:
            if command.startswith("settings --user 10 get secure android_id"):
                return _ok("\n")
            return super()._shell(command)

    assert _client(WorkEmpty()).android_id(10) == ""


def test_parse_gsf_id_decimal_and_hex() -> None:
    decimal = tool.parse_gsf_id("Row: 0 value=1234567890123456789")
    assert decimal is not None
    assert decimal.value == 1234567890123456789
    assert decimal.decimal == "1234567890123456789"
    assert decimal.hex == hex(1234567890123456789)

    hex_value = tool.parse_gsf_id("Row: 0 android_id=0x1a2b3c4d5e")
    assert hex_value is not None
    assert hex_value.value == 0x1A2B3C4D5E

    bare_hex = tool.parse_gsf_id("Row: 0 android_id=9ee1b76f1a2b3c4d")
    assert bare_hex is not None
    assert bare_hex.value == int("9ee1b76f1a2b3c4d", 16)


def test_parse_gsf_id_returns_none_for_null_or_missing() -> None:
    assert tool.parse_gsf_id("Row: 0 value=null") is None
    assert tool.parse_gsf_id("No result found.") is None


def test_parse_ps_a_and_user_process_ownership() -> None:
    entries = tool.parse_ps_a(PS_OUTPUT)
    assert any(
        entry.name == "com.google.android.gms" and entry.user == "u10_a231"
        for entry in entries
    )
    work = tool.find_user_processes(entries, 10, names=("com.google.android.gms",))
    assert {(entry.user, entry.name) for entry in work} == {
        ("u10_a231", "com.google.android.gms"),
        ("u10_a232", "com.google.android.gms.unstable"),
    }
    assert all(not entry.user.startswith("u0_") for entry in work)
    assert (
        tool.find_user_processes(entries, 0, names=("com.google.android.gms",))[0].user
        == "u0_a123"
    )
    assert tool.find_user_processes(entries, 11) == []


def test_parse_window_focus_extracts_package_and_user() -> None:
    focus = tool.parse_window_focus(GMS_FOCUS)
    assert focus.package == "com.google.android.gms"
    assert focus.user_id == 10
    assert focus.activity == "com.google.android.gms.auth.ui.HtmlActivity"
    assert focus.component is not None


def test_parse_window_focus_handles_missing_window() -> None:
    focus = tool.parse_window_focus("WINDOW MANAGER\n  mFocusedApp=null\n")
    assert focus.component is None
    assert focus.package is None


def test_parse_resolved_component() -> None:
    output = (
        "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true\n"
        "com.android.chrome/com.google.android.apps.chrome.Main\n"
    )
    assert tool.parse_resolved_component(output) == (
        "com.android.chrome/com.google.android.apps.chrome.Main"
    )
    assert tool.parse_resolved_component("No activity found") is None


def test_parse_adb_devices() -> None:
    devices = tool.parse_adb_devices(
        "List of devices attached\nR58M12345\tdevice\n10.0.0.2:5555\toffline\n\n"
    )
    assert [(item.serial, item.state, item.usable) for item in devices] == [
        ("R58M12345", "device", True),
        ("10.0.0.2:5555", "offline", False),
    ]


def _transport_runner(serialno_by_transport: dict[str, str], devices_text: str) -> Any:
    def runner(args: Sequence[str], timeout: float) -> Any:
        rest = [str(item) for item in args][1:]
        transport: str | None = None
        if rest[:1] == ["-s"]:
            transport = rest[1]
            rest = rest[2:]
        if rest == ["devices"]:
            return _ok(devices_text)
        if rest == ["shell", "getprop ro.serialno"] and transport:
            return _ok(serialno_by_transport.get(transport, "") + "\n")
        return _ok("")

    return runner


def test_adb_auto_selects_ip_transport_for_same_phone() -> None:
    devices = (
        "List of devices attached\n"
        "192.168.1.100:41234\tdevice\n"
        "adb-R58M12345-1a2b3c._adb-tls-connect._tcp\tdevice\n"
    )
    runner = _transport_runner(
        {
            "192.168.1.100:41234": "R58M12345",
            "adb-R58M12345-1a2b3c._adb-tls-connect._tcp": "R58M12345",
        },
        devices,
    )
    adb = tool.AdbClient(Path("adb"), runner=runner, timeout=1.0)
    adb._ensure_serial()
    assert adb.serial == "192.168.1.100:41234"


def test_adb_auto_select_uses_single_online_device() -> None:
    runner = _transport_runner(
        {"R58M12345": "R58M12345"},
        "List of devices attached\nR58M12345\tdevice\n",
    )
    adb = tool.AdbClient(Path("adb"), runner=runner, timeout=1.0)
    adb._ensure_serial()
    assert adb.serial == "R58M12345"


def test_adb_auto_select_rejects_different_phones() -> None:
    devices = "List of devices attached\nR58M12345\tdevice\n10.0.0.2:5555\tdevice\n"
    runner = _transport_runner(
        {"R58M12345": "PHONE-A", "10.0.0.2:5555": "PHONE-B"}, devices
    )
    adb = tool.AdbClient(Path("adb"), runner=runner, timeout=1.0)
    with pytest.raises(tool.AdbError, match="多台不同"):
        adb._ensure_serial()


def test_adb_auto_select_errors_without_devices() -> None:
    runner = _transport_runner({}, "List of devices attached\n\n")
    adb = tool.AdbClient(Path("adb"), runner=runner, timeout=1.0)
    with pytest.raises(tool.AdbError, match="未发现可用"):
        adb._ensure_serial()


# ---------------------------------------------------------------------------
# 主副空间隔离与目标空间选择
# ---------------------------------------------------------------------------


def _identity(user_id: int, android_id: str, gsf: int | None = 1) -> Any:
    return tool.SpaceIdentity(
        user_id=user_id,
        user_name="工作资料" if user_id else "机主",
        android_id=android_id,
        gsf=tool.GsfId(raw=str(gsf), value=gsf) if gsf is not None else None,
        gms_installed=True,
        gms_version="25.18.32",
        is_work_profile=user_id != 0,
    )


def test_assert_space_isolated_rejects_same_android_id() -> None:
    main = _identity(0, "a" * 16, 11)
    work = _identity(10, "a" * 16, 22)
    with pytest.raises(tool.SafetyError, match="完全相同"):
        tool.assert_space_isolated(main, work)


def test_assert_space_isolated_rejects_empty_android_id() -> None:
    with pytest.raises(tool.SafetyError, match="为空"):
        tool.assert_space_isolated(_identity(0, "a" * 16), _identity(10, ""))


def test_assert_space_isolated_passes_for_distinct_ids() -> None:
    tool.assert_space_isolated(_identity(0, "a" * 16, 11), _identity(10, "b" * 16, 22))


def test_assert_space_isolated_rejects_hidden_main_for_primary_target() -> None:
    main = _identity(0, "")
    with pytest.raises(tool.SafetyError, match="主空间"):
        tool.assert_space_isolated(main, main)


def test_assert_space_isolated_passes_when_main_hidden_for_work_profile() -> None:
    main = _identity(0, "")
    work = _identity(10, "fedcba9876543210")
    tool.assert_space_isolated(main, work)
    rendered = tool.format_identities(main, work)
    assert "已隔离 (PASS)" in rendered
    assert "受系统隐私保护隐藏" in rendered
    assert "独立受管 ANDROID_ID" in rendered


def test_assert_space_isolated_rejects_hidden_main_for_non_work_user() -> None:
    main = _identity(0, "")
    guest = tool.SpaceIdentity(
        user_id=11,
        user_name="访客",
        android_id="b" * 16,
        gsf=None,
        gms_installed=True,
        gms_version=None,
        is_work_profile=False,
    )
    with pytest.raises(tool.SafetyError, match="主空间"):
        tool.assert_space_isolated(main, guest)


def test_verification_report_accepts_hidden_main_for_work_profile() -> None:
    report = tool.VerificationReport(
        url="https://example.test/verify",
        source="单元测试",
        target=_identity(10, "fedcba9876543210"),
        main=_identity(0, ""),
        resolved_component=None,
    )
    assert report.isolation_ok is True
    assert "受系统隐私保护隐藏" in report.render()


def test_assert_space_isolated_allow_main_user_checks_only_target() -> None:
    target = _identity(0, "a" * 16, 11)
    tool.assert_space_isolated(target, target, allow_main_user=True)
    with pytest.raises(tool.SafetyError, match="为空或非法"):
        tool.assert_space_isolated(
            _identity(0, "a" * 16), _identity(0, ""), allow_main_user=True
        )
    with pytest.raises(tool.SafetyError, match="完全相同"):
        tool.assert_space_isolated(target, target)


def test_format_identities_reports_pass_and_fail() -> None:
    passed = tool.format_identities(
        _identity(0, "a" * 16, 11), _identity(10, "b" * 16, 22)
    )
    assert "已隔离 (PASS)" in passed
    assert "完全隔离" in passed
    failed = tool.format_identities(
        _identity(0, "a" * 16, 11), _identity(10, "a" * 16, 11)
    )
    assert "未隔离 (FAIL)" in failed
    assert "请勿" in failed


def test_select_work_profile_prefers_work_profile() -> None:
    users = tool.parse_pm_list_users(USERS_OUTPUT)
    assert tool.select_work_profile(users).user_id == 10


def test_select_work_profile_refuses_main_user_by_default() -> None:
    users = tool.parse_pm_list_users(USERS_OUTPUT)
    with pytest.raises(tool.SafetyError, match="主空间"):
        tool.select_work_profile(users, requested_user=0)
    assert (
        tool.select_work_profile(users, requested_user=0, allow_main_user=True).user_id
        == 0
    )


def test_select_work_profile_errors_without_work_profile() -> None:
    users = tool.parse_pm_list_users("\tUserInfo{0:机主:c13} running\n")
    with pytest.raises(tool.SafetyError, match="未找到 Island 工作资料"):
        tool.select_work_profile(users)
    with pytest.raises(tool.SafetyError, match="不存在"):
        tool.select_work_profile(users, requested_user=10)


def test_select_work_profile_refuses_non_work_secondary_user() -> None:
    users = tool.parse_pm_list_users(USERS_OUTPUT)
    with pytest.raises(tool.SafetyError, match="不是 Island 工作资料"):
        tool.select_work_profile(users, requested_user=11)
    assert (
        tool.select_work_profile(users, requested_user=11, allow_main_user=True).user_id
        == 11
    )


# ---------------------------------------------------------------------------
# 二维码解码（内置纯 Python 解码器）
# ---------------------------------------------------------------------------


def test_decode_builtin_png_fixture() -> None:
    assert (
        tool.decode_with_backend("builtin", base64.b64decode(QR_A_PNG_B64)) == QR_A_URL
    )
    assert (
        tool.decode_with_backend("builtin", base64.b64decode(QR_B_PNG_B64)) == QR_B_URL
    )


def test_decode_builtin_png_rgba_and_la_channels() -> None:
    gray = tool.load_gray_image(base64.b64decode(QR_B_PNG_B64))
    variants = (
        _png_channel_bytes(gray, 6, lambda value: (value, value, value, 255)),
        _png_channel_bytes(gray, 4, lambda value: (value, 255)),
        _png_channel16_bytes(gray, 6, lambda value: (value, value, value, 255)),
        _png_channel16_bytes(gray, 4, lambda value: (value, 255)),
    )
    for data in variants:
        decoded = tool.load_gray_image(data)
        assert decoded.pixels == gray.pixels
        assert tool.decode_builtin(data) == QR_B_URL


def test_decode_png_rgba_composites_transparency_to_white() -> None:
    transparent_black = _png_channel_bytes(
        tool.GrayImage(width=1, height=1, pixels=bytearray([0])),
        6,
        lambda _value: (0, 0, 0, 0),
    )
    opaque_black = _png_channel_bytes(
        tool.GrayImage(width=1, height=1, pixels=bytearray([0])),
        6,
        lambda _value: (0, 0, 0, 255),
    )
    opaque_gray = _png_channel_bytes(
        tool.GrayImage(width=1, height=1, pixels=bytearray([128])),
        6,
        lambda value: (value, value, value, 255),
    )
    assert tool.load_gray_image(transparent_black).pixels[0] == 255
    assert tool.load_gray_image(opaque_black).pixels[0] == 0
    assert tool.load_gray_image(opaque_gray).pixels[0] == 128


def test_decode_png_rejects_truncated_or_oversized_headers() -> None:
    short_ihdr = tool.PNG_SIGNATURE + _png_chunk(b"IHDR", b"\x00" * 8)
    with pytest.raises(tool.ImageDecodeError, match="IHDR"):
        tool.load_gray_image(short_ihdr)
    oversized = _png_build(9000, 1, 0, b"\x00" * 9001)
    with pytest.raises(tool.ImageDecodeError, match="尺寸超出上限"):
        tool.load_gray_image(oversized)
    bomb = _png_build(2, 2, 0, b"\x00" * 100)
    with pytest.raises(tool.ImageDecodeError, match="解压"):
        tool.decode_png(bomb)


def test_decode_segments_mode9_consumes_application_indicator() -> None:
    bits: list[int] = []

    def put(value: int, count: int) -> None:
        bits.extend((value >> (count - 1 - index)) & 1 for index in range(count))

    put(9, 4)
    put(0xAB, 8)
    put(4, 4)
    put(1, 8)
    put(ord("A"), 8)
    put(0, 4)
    data = bytearray()
    for start in range(0, len(bits), 8):
        byte = 0
        for bit in bits[start : start + 8]:
            byte = (byte << 1) | bit
        data.append(byte)
    assert tool.decode_segments(bytes(data), 1) == "A"


def test_decode_qr_from_image_backend_chain_includes_rs() -> None:
    data = base64.b64decode(QR_B_PNG_B64)
    assert tool.decode_qr_from_image(data, backends=["builtin"]) == QR_B_URL
    assert "builtin" in tool.available_backends()
    assert tool.decode_qr_from_image(data) == QR_B_URL


def test_decode_builtin_recovers_damaged_modules() -> None:
    gray = tool.load_gray_image(base64.b64decode(QR_A_PNG_B64))
    damaged = _flip_blocks(gray, 10)
    assert tool.decode_builtin(damaged) == QR_A_URL


def test_decode_builtin_handles_rotated_image() -> None:
    gray = tool.load_gray_image(base64.b64decode(QR_B_PNG_B64))
    assert tool.decode_builtin(_rotate180(gray)) == QR_B_URL


def test_decode_builtin_supports_bmp() -> None:
    gray = tool.load_gray_image(base64.b64decode(QR_B_PNG_B64))
    assert (
        tool.decode_builtin(_bmp_bytes(gray.width, gray.height, bytes(gray.pixels)))
        == QR_B_URL
    )


def test_normalize_image_bytes_wraps_raw_clipboard_dib() -> None:
    gray = tool.load_gray_image(base64.b64decode(QR_B_PNG_B64))
    bmp = _bmp_bytes(gray.width, gray.height, bytes(gray.pixels))
    wrapped = tool.normalize_image_bytes(bmp[14:])
    assert wrapped.startswith(b"BM")
    assert tool.load_gray_image(wrapped).pixels == gray.pixels
    assert tool.decode_builtin(wrapped) == QR_B_URL
    png = base64.b64decode(QR_B_PNG_B64)
    assert tool.normalize_image_bytes(png) == png


def test_decode_builtin_rejects_non_qr_image() -> None:
    blank = _png_bytes(64, 64, bytes([255] * 64 * 64))
    with pytest.raises(tool.QrDecodeError):
        tool.decode_builtin(blank)
    with pytest.raises(tool.QrDecodeError, match="无法识别"):
        tool.load_gray_image(b"not an image at all")


def test_extract_url_validates_scheme() -> None:
    assert (
        tool.extract_url("扫码结果: https://g.co/verify?c=1 。")
        == "https://g.co/verify?c=1"
    )
    assert tool.extract_url("http://example.com/x") == "http://example.com/x"
    with pytest.raises(tool.SafetyError):
        tool.extract_url("ftp://example.com/x")
    with pytest.raises(tool.SafetyError):
        tool.extract_url("no link here")


def test_resolve_url_from_sources_uses_url_and_image(tmp_path: Path) -> None:
    assert tool.resolve_url_from_sources(url=QR_B_URL)[0] == QR_B_URL
    image = tmp_path / "qr.png"
    image.write_bytes(base64.b64decode(QR_B_PNG_B64))
    assert tool.resolve_url_from_sources(qr_image=str(image), backends=["builtin"]) == (
        QR_B_URL,
        f"图片 {image}",
    )
    with pytest.raises(tool.ToolError, match="请指定"):
        tool.resolve_url_from_sources()


def test_resolve_url_from_sources_clipboard_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tool, "read_clipboard_payload", lambda: (f"复制的链接 {QR_B_URL}", None)
    )
    assert tool.resolve_url_from_sources(use_clipboard=True) == (QR_B_URL, "剪贴板文本")


def test_resolve_url_from_sources_clipboard_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tool,
        "read_clipboard_payload",
        lambda: (None, base64.b64decode(QR_B_PNG_B64)),
    )
    assert tool.resolve_url_from_sources(use_clipboard=True, backends=["builtin"]) == (
        QR_B_URL,
        "剪贴板图片",
    )


def test_enter_pressed_handles_injected_reader() -> None:
    def boom() -> str:
        raise EOFError

    assert tool._enter_pressed(boom) is False
    assert tool._enter_pressed(lambda: "") is True


# ---------------------------------------------------------------------------
# ADB 路径 / APK 校验
# ---------------------------------------------------------------------------


def test_resolve_adb_path_prefers_explicit(tmp_path: Path) -> None:
    fake_adb = tmp_path / "adb.exe"
    fake_adb.write_bytes(b"\x00")
    assert tool.resolve_adb_path(str(fake_adb)) == fake_adb


def test_resolve_adb_path_uses_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_adb = tmp_path / "adb-env.exe"
    fake_adb.write_bytes(b"\x00")
    monkeypatch.setenv("ADB_PATH", f'"{fake_adb}"')
    assert tool.resolve_adb_path() == fake_adb


def test_resolve_adb_path_errors_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADB_PATH", "")
    monkeypatch.setattr(tool.shutil, "which", lambda _name: None)
    monkeypatch.setattr(tool, "_sdk_roots", list)
    monkeypatch.setattr(tool, "PROJECT_ROOT", Path("Z:/definitely/missing"))
    with pytest.raises(tool.AdbError, match="未找到 adb"):
        tool.resolve_adb_path()


def test_resolve_adb_path_uses_android_sdk_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sdk_root = tmp_path / "sdk"
    adb = sdk_root / "platform-tools" / tool.ADB_EXE_NAME
    adb.parent.mkdir(parents=True)
    adb.write_bytes(b"\x00")
    monkeypatch.setenv("ANDROID_SDK_ROOT", str(sdk_root))
    monkeypatch.setenv("ADB_PATH", "")
    monkeypatch.setattr(tool.shutil, "which", lambda _name: None)
    monkeypatch.setattr(tool, "PROJECT_ROOT", tmp_path / "missing-project")
    assert tool.resolve_adb_path() == adb


def test_parse_adb_platform_version_old_and_new() -> None:
    old = "Android Debug Bridge version 1.0.39\nVersion 0.0.1-4500957"
    new = "Android Debug Bridge version 1.0.41\nVersion 37.0.1-15733141"
    assert tool.parse_adb_platform_version(old) == 0
    assert tool.parse_adb_platform_version(new) == 37
    assert tool.parse_adb_platform_version("garbage") is None


def test_adb_supports_pair_rejects_old_platform_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_runner(args: Sequence[str], timeout: float) -> Any:
        return tool.CommandResult(
            args=tuple(args),
            returncode=0,
            stdout="Android Debug Bridge version 1.0.39\nVersion 0.0.1-4500957",
            stderr="",
        )

    monkeypatch.setattr(tool, "default_runner", fake_runner)
    assert tool._adb_supports_pair("adb.exe") is False
    monkeypatch.setattr(
        tool,
        "default_runner",
        lambda args, timeout: tool.CommandResult(
            args=tuple(args),
            returncode=0,
            stdout="Android Debug Bridge version 1.0.41\nVersion 37.0.1-1",
            stderr="",
        ),
    )
    assert tool._adb_supports_pair("adb.exe") is True


def test_resolve_adb_path_skips_adb_without_pair_support(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    new_adb = project / "data" / "tools" / "platform-tools" / tool.ADB_EXE_NAME
    old_adb = tmp_path / "old-tools" / tool.ADB_EXE_NAME
    for path in (new_adb, old_adb):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\x00")
    versions = {
        str(old_adb): "Android Debug Bridge version 1.0.39\nVersion 0.0.1-4500957",
        str(new_adb): "Android Debug Bridge version 1.0.41\nVersion 37.0.1-15733141",
    }

    def fake_runner(args: Sequence[str], timeout: float) -> Any:
        return tool.CommandResult(
            args=tuple(args),
            returncode=0,
            stdout=versions.get(str(args[0]), ""),
            stderr="",
        )

    monkeypatch.setenv("ADB_PATH", str(old_adb))
    monkeypatch.setattr(tool, "PROJECT_ROOT", project)
    monkeypatch.setattr(tool.shutil, "which", lambda _name: None)
    monkeypatch.setattr(tool, "_sdk_roots", list)
    monkeypatch.setattr(tool, "default_runner", fake_runner)
    assert tool.resolve_adb_path() == new_adb


def test_resolve_adb_path_prefers_local_properties_over_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    (project / "android").mkdir(parents=True)
    sdk_root = tmp_path / "sdk"
    sdk_adb = sdk_root / "platform-tools" / tool.ADB_EXE_NAME
    sdk_adb.parent.mkdir(parents=True)
    sdk_adb.write_bytes(b"\x00")
    (project / "android" / "local.properties").write_text(
        f"sdk.dir={sdk_root.as_posix()}\n", encoding="utf-8"
    )
    path_adb = tmp_path / "path-adb.exe"
    path_adb.write_bytes(b"\x00")
    monkeypatch.setenv("ADB_PATH", "")
    monkeypatch.setattr(tool, "PROJECT_ROOT", project)
    monkeypatch.setattr(tool.shutil, "which", lambda _name: str(path_adb))
    monkeypatch.setattr(tool, "_sdk_roots", list)
    monkeypatch.setattr(
        tool,
        "default_runner",
        lambda args, timeout: tool.CommandResult(
            args=tuple(args),
            returncode=0,
            stdout="Android Debug Bridge version 1.0.41\nVersion 37.0.1-1",
            stderr="",
        ),
    )
    assert tool.resolve_adb_path() == sdk_adb


def test_normalize_adb_address_uses_host_for_port_only() -> None:
    assert tool.normalize_adb_address("41234", "192.168.1.100") == "192.168.1.100:41234"
    assert tool.normalize_adb_address("41235", "192.168.1.100") == "192.168.1.100:41235"
    assert tool.normalize_adb_address(None, "192.168.1.100") is None


def test_normalize_adb_address_rewrites_vpn_ip_with_host() -> None:
    assert tool.normalize_adb_address("10.0.0.1:41234", "192.168.1.100") == (
        "192.168.1.100:41234"
    )
    assert tool.normalize_adb_address("10.0.0.1:41235", "192.168.1.100") == (
        "192.168.1.100:41235"
    )
    assert tool.normalize_adb_address("10.0.0.1:41234", None) == "10.0.0.1:41234"
    assert (
        tool.normalize_adb_address("192.168.1.100:41234", None) == "192.168.1.100:41234"
    )


def test_normalize_adb_address_requires_host_for_bare_port() -> None:
    with pytest.raises(tool.ToolError, match="--host"):
        tool.normalize_adb_address("41234", None)


def test_vpn_host_hint_detects_virtual_adapter_ips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool, "_local_lan_ip", lambda: "192.168.1.101")
    assert tool._looks_like_vpn_ip("10.0.0.1")
    assert tool._looks_like_vpn_ip("172.20.3.4")
    assert tool._looks_like_vpn_ip("198.18.1.2")
    assert not tool._looks_like_vpn_ip("192.168.1.100")
    hint = tool.vpn_host_hint("10.0.0.1:41234")
    assert hint is not None
    assert "VPN 虚拟网卡" in hint
    assert "--host 192.168.1.100" in hint
    assert tool.vpn_host_hint("192.168.1.100:41234") is None
    monkeypatch.setattr(tool, "_local_lan_ip", lambda: None)
    assert tool.vpn_host_hint("10.0.0.1:41234") is None


def test_cmd_connect_rewrites_addresses_with_host(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, str, str | None]] = []

    class StubClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.adb_path = Path("adb")

        def pair(self, host_port: str, code: str) -> str:
            calls.append(("pair", host_port, code))
            return f"Successfully paired to {host_port}"

        def connect(self, host_port: str) -> str:
            calls.append(("connect", host_port, None))
            return f"connected to {host_port}"

        def list_devices(self) -> list[Any]:
            return []

    monkeypatch.setattr(tool, "AdbClient", StubClient)
    monkeypatch.setattr(tool, "_adb_supports_pair", lambda _path: True)
    code = tool.main(
        [
            "connect",
            "--host",
            "192.168.1.100",
            "--pair",
            "10.0.0.1:41234",
            "--code",
            "123456",
            "--connect",
            "41235",
        ]
    )
    assert code == 0
    assert calls == [
        ("pair", "192.168.1.100:41234", "123456"),
        ("connect", "192.168.1.100:41235", None),
    ]
    assert "192.168.1.100:41234" in capsys.readouterr().out


def test_shell_quote_protects_special_urls() -> None:
    tricky = "https://x.test/v?a=1&b=2%2B3;c=4+d"
    assert tool.AdbClient._shell_quote(tricky) == f"'{tricky}'"
    assert tool.AdbClient._shell_quote("a'b") == "'a'\\''b'"

    runner = FakeAdb()
    adb = _client(runner)
    adb.resolve_activity(tricky, 10)
    adb.start_view(tricky, 10)
    commands = [call[-1] for call in runner.calls if call[-2:-1] == ("shell",)]
    assert len(commands) == 2
    assert all(f"-d '{tricky}'" in command for command in commands)
    assert any("resolve-activity" in command for command in commands)
    assert any("am start --user 10" in command for command in commands)


def test_read_java_properties_unescapes_windows_path(tmp_path: Path) -> None:
    props = tmp_path / "local.properties"
    props.write_text("sdk.dir=D\\:\\\\work\\\\android-sdk\n", encoding="utf-8")
    assert tool._read_java_properties(props) == {"sdk.dir": "D:\\work\\android-sdk"}
    assert tool._read_java_properties(tmp_path / "missing.properties") == {}


def test_apk_summary_and_latest_version(tmp_path: Path) -> None:
    for name in ("Island.v6.1.9.apk", "Island.v6.2.1.apk", "notes.txt"):
        (tmp_path / name).write_bytes(b"PK\x03\x04" + name.encode())
    latest = tool.find_latest_island_apk(tmp_path)
    assert latest.name == "Island.v6.2.1.apk"
    size, digest = tool._apk_summary(latest)
    assert size == len(latest.read_bytes())
    assert len(digest) == 64
    with pytest.raises(tool.ToolError, match="不是合法 APK"):
        broken = tmp_path / "broken.apk"
        broken.write_bytes(b"not a zip")
        tool._apk_summary(broken)
    with pytest.raises(tool.ToolError, match="未在"):
        empty = tmp_path / "empty"
        empty.mkdir()
        tool.find_latest_island_apk(empty)


# ---------------------------------------------------------------------------
# verify 全链路状态机
# ---------------------------------------------------------------------------


def test_run_verification_completes_and_reports_evidence() -> None:
    runner = FakeAdb()
    clock, sleeper, _state = _clock()
    lines: list[str] = []
    cfg = tool.VerifyConfig(url=QR_A_URL, source="单元测试", timeout=20.0, interval=1.5)
    report = tool.run_verification(
        _client(runner), cfg, clock=clock, sleeper=sleeper, writer=lines.append
    )
    assert report.completed is True
    assert report.completion_reason == "验证界面已从前台关闭"
    assert report.isolation_ok is True
    assert report.target.android_id == WORK_ANDROID_ID
    assert (
        report.resolved_component
        == "com.android.chrome/com.google.android.apps.chrome.Main"
    )
    assert any("u10_a231" in item for item in report.evidence_processes)
    assert not any("u0_a123" in item for item in report.evidence_processes)
    assert (
        report.focus_trail[0]
        == "com.google.android.gms/com.google.android.gms.auth.ui.HtmlActivity"
    )
    assert any("DroidGuard" in event for event in report.log_events)
    rendered = report.render()
    assert "实机验证设备身份与完成报告" in rendered
    assert WORK_ANDROID_ID in rendered
    assert "验证状态        : 完成" in rendered
    assert any("[3/4]" in line for line in lines)
    assert any(line.startswith("  #1") for line in lines)

    commands = [call[-1] for call in runner.calls if "shell" in call]
    assert "logcat -c" in commands
    assert any(call.startswith("am start --user 10") for call in commands)
    assert any("--user 10" in call for call in commands if "resolve-activity" in call)


def test_run_verification_allows_main_user_with_explicit_flag() -> None:
    runner = FakeAdb(
        focus_sequence=[MAIN_GMS_FOCUS] * 2 + [LAUNCHER_FOCUS] * 4,
    )
    clock, sleeper, _state = _clock()
    cfg = tool.VerifyConfig(
        url=QR_A_URL,
        timeout=20.0,
        interval=1.5,
        user_id=0,
        allow_main_user=True,
    )
    report = tool.run_verification(
        _client(runner), cfg, clock=clock, sleeper=sleeper, writer=lambda _line: None
    )
    assert report.completed is True
    assert report.main_user_mode is True
    assert report.target.user_id == 0
    rendered = report.render()
    assert "主空间基线模式" in rendered
    assert "不适用" in rendered


def test_run_verification_rejects_non_work_user_without_flag() -> None:
    runner = FakeAdb()
    cfg = tool.VerifyConfig(url=QR_A_URL, timeout=5.0, user_id=11)
    with pytest.raises(tool.SafetyError, match="不是 Island 工作资料"):
        tool.run_verification(
            _client(runner),
            cfg,
            clock=lambda: 0.0,
            sleeper=lambda _s: None,
            writer=lambda _line: None,
        )


def test_run_verification_does_not_trust_unlabeled_focus_without_processes() -> None:
    runner = FakeAdb(
        focus_sequence=[UNLABELED_GMS_FOCUS] * 3 + [LAUNCHER_FOCUS] * 6,
        ps_output=PS_OUTPUT_NO_WORK,
    )
    clock, sleeper, _state = _clock()
    cfg = tool.VerifyConfig(url=QR_A_URL, timeout=5.0, interval=1.0)
    report = tool.run_verification(
        _client(runner), cfg, clock=clock, sleeper=sleeper, writer=lambda _line: None
    )
    assert report.completed is False
    assert "超时" in report.completion_reason


def test_run_verification_trusts_unlabeled_focus_with_target_processes() -> None:
    runner = FakeAdb(focus_sequence=[UNLABELED_GMS_FOCUS] * 2 + [LAUNCHER_FOCUS] * 4)
    clock, sleeper, _state = _clock()
    cfg = tool.VerifyConfig(url=QR_A_URL, timeout=20.0, interval=1.5)
    report = tool.run_verification(
        _client(runner), cfg, clock=clock, sleeper=sleeper, writer=lambda _line: None
    )
    assert report.completed is True
    assert report.completion_reason == "验证界面已从前台关闭"


def test_run_verification_aborts_when_focus_jumps_to_other_user() -> None:
    runner = FakeAdb(focus_sequence=[THIRD_PARTY_GMS_FOCUS] * 2)
    clock, sleeper, _state = _clock()
    cfg = tool.VerifyConfig(url=QR_A_URL, timeout=20.0, interval=1.5)
    with pytest.raises(tool.SafetyError, match="跳出目标工作资料空间"):
        tool.run_verification(
            _client(runner),
            cfg,
            clock=clock,
            sleeper=sleeper,
            writer=lambda _line: None,
        )


def test_run_verification_blocks_same_android_id_without_launching() -> None:
    runner = FakeAdb(work_id=MAIN_ANDROID_ID)
    clock, sleeper, _state = _clock()
    cfg = tool.VerifyConfig(url=QR_A_URL, timeout=10.0)
    with pytest.raises(tool.SafetyError, match="完全相同"):
        tool.run_verification(
            _client(runner),
            cfg,
            clock=clock,
            sleeper=sleeper,
            writer=lambda _line: None,
        )
    assert not any("am start" in " ".join(call) for call in runner.calls)


def test_run_verification_blocks_without_work_profile() -> None:
    runner = FakeAdb(users="\tUserInfo{0:机主:c13} running\n")
    cfg = tool.VerifyConfig(url=QR_A_URL, timeout=10.0)
    with pytest.raises(tool.SafetyError, match="未找到 Island 工作资料"):
        tool.run_verification(
            _client(runner),
            cfg,
            clock=lambda: 0.0,
            sleeper=lambda _s: None,
            writer=lambda _line: None,
        )


def test_run_verification_aborts_on_android_id_rollback() -> None:
    runner = FakeAdb(drift_after=1)
    clock, sleeper, _state = _clock()
    cfg = tool.VerifyConfig(url=QR_A_URL, timeout=20.0, interval=1.5)
    with pytest.raises(tool.SafetyError, match="回跳"):
        tool.run_verification(
            _client(runner),
            cfg,
            clock=clock,
            sleeper=sleeper,
            writer=lambda _line: None,
        )


def test_run_verification_confirm_path_marks_completed_without_focus_loss() -> None:
    runner = FakeAdb(focus_sequence=[GMS_FOCUS] * 10)
    clock, sleeper, _state = _clock()
    cfg = tool.VerifyConfig(url=QR_A_URL, timeout=5.0, interval=1.0, confirm=True)
    report = tool.run_verification(
        _client(runner),
        cfg,
        clock=clock,
        sleeper=sleeper,
        writer=lambda _line: None,
        confirm_reader=lambda: "",
    )
    assert report.completed is True
    assert report.completion_reason == "用户按回车确认完成"


def test_run_verification_reports_timeout_when_never_focused() -> None:
    runner = FakeAdb(focus_sequence=[LAUNCHER_FOCUS] * 20)
    clock, sleeper, _state = _clock()
    cfg = tool.VerifyConfig(url=QR_A_URL, timeout=4.0, interval=1.5)
    report = tool.run_verification(
        _client(runner), cfg, clock=clock, sleeper=sleeper, writer=lambda _line: None
    )
    assert report.completed is False
    assert "超时" in report.completion_reason
    assert report.samples, "应至少采集一次样本"


def test_run_verification_raises_on_am_start_error() -> None:
    runner = FakeAdb(am_start_output="Error: Activity not started\n")
    clock, sleeper, _state = _clock()
    cfg = tool.VerifyConfig(url=QR_A_URL, timeout=5.0)
    with pytest.raises(tool.AdbError, match="am start"):
        tool.run_verification(
            _client(runner),
            cfg,
            clock=clock,
            sleeper=sleeper,
            writer=lambda _line: None,
        )


# ---------------------------------------------------------------------------
# CLI 与命令实现
# ---------------------------------------------------------------------------


def test_cli_serial_flag_works_before_and_after_subcommand() -> None:
    parser = tool.build_parser()
    assert parser.parse_args(["-s", "AAA", "inspect-ids"]).serial == "AAA"
    assert parser.parse_args(["--serial", "AAA", "inspect-ids"]).serial == "AAA"
    assert parser.parse_args(["inspect-ids", "-s", "BBB"]).serial == "BBB"
    assert parser.parse_args(["inspect-ids", "--serial", "BBB"]).serial == "BBB"
    assert not hasattr(parser.parse_args(["inspect-ids"]), "serial")


def test_cli_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        tool.main(["--help"])
    assert excinfo.value.code == 0
    assert "inspect-ids" in capsys.readouterr().out


def test_cli_devices_lists_runner_output(capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeAdb()
    adb_path = Path("adb")
    original = tool.AdbClient

    class PatchedClient:
        def __init__(
            self, *_args: Any, serial: str | None = None, **_kwargs: Any
        ) -> None:
            self.inner = original(adb_path, serial=serial, runner=runner, timeout=1.0)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

    tool.AdbClient = PatchedClient
    try:
        assert tool.main(["devices", "--adb-path", str(adb_path)]) == 0
    finally:
        tool.AdbClient = original
    output = capsys.readouterr().out
    assert "R58M12345" in output
    assert "是" in output


def test_cmd_inspect_ids_reports_isolation(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeAdb()
    original = tool.AdbClient

    def fake_client(*_args: Any, serial: str | None = None, **_kwargs: Any) -> Any:
        return original(Path("adb"), serial=serial, runner=runner, timeout=1.0)

    monkeypatch.setattr(tool, "AdbClient", fake_client)
    assert tool.main(["inspect-ids"]) == 0
    output = capsys.readouterr().out
    assert "User 10" in output
    assert "已隔离 (PASS)" in output
    assert WORK_ANDROID_ID in output


def test_cmd_install_island_uses_latest_apk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apk_dir = tmp_path / "island"
    apk_dir.mkdir()
    apk = apk_dir / "Island.v9.9.9.apk"
    apk.write_bytes(b"PK\x03\x04" + b"payload")
    monkeypatch.setattr(tool, "ISLAND_APK_DIR", apk_dir)
    installed: list[Path] = []

    class StubClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def install(self, path: Path, **_kwargs: Any) -> str:
            installed.append(path)
            return "Success"

    monkeypatch.setattr(tool, "AdbClient", StubClient)
    assert tool.main(["install-island"]) == 0
    assert installed == [apk]
    output = capsys.readouterr().out
    assert "SHA256" in output
    assert "Success" in output


# ---------------------------------------------------------------------------
# setup-island：包清单 / VPN / 映射与校验
# ---------------------------------------------------------------------------

SETUP_MAIN_PACKAGES = """package:com.google.android.gms
package:com.google.android.gsf
package:com.android.vending
package:com.android.chrome
package:mark.via
package:com.heytap.browser
package:com.v2ray.ang
"""

SETUP_USER10_UIDS = """package:com.google.android.gms uid:1010098
package:com.google.android.gsf uid:1010099
package:com.android.vending uid:1010085
package:com.android.chrome uid:1010370
package:mark.via uid:1010398
package:com.heytap.browser uid:1010205
package:com.v2ray.ang uid:1010401
"""

RESOLVED_CHROME = (
    "priority=0 preferredOrder=0 match=0x208000 specificIndex=-1 isDefault=true\n"
    "com.android.chrome/com.google.android.apps.chrome.IntentDispatcher\n"
)


def _err(text: str) -> Any:
    return tool.CommandResult(args=(), returncode=255, stdout="", stderr=text + "\n")


def _vpn_dumpsys(ranges: str) -> str:
    return (
        "VPNs:\n"
        "  0: com.v2ray.ang\n"
        "    Active package name: com.v2ray.ang\n"
        "    NetworkCapabilities: [ Transports: VPN Capabilities: NOT_METERED "
        f"Uids: <{{{ranges}}}> OwnerUid: 10401 AdminUids: [10401] "
        "UnderlyingNetworks: Null]\n"
    )


class SetupAdb:
    """模拟 ``setup-island`` 所需全部 adb 命令的可编程假设备。"""

    def __init__(
        self,
        *,
        main_packages: str = SETUP_MAIN_PACKAGES,
        vpn_ranges: str = "0-10022, 10024-10407, 20024-20407, 20409-99999",
        failing_install: Sequence[str] = (),
        role_holder: str = "com.android.chrome",
        resolved: str = RESOLVED_CHROME,
        enabled_user10: str | None = None,
        enabled_extra: Sequence[str] = (),
    ) -> None:
        self.main_packages = main_packages
        self.enabled_packages = enabled_user10
        self.enabled_extra = tuple(enabled_extra)
        self.vpn_ranges = vpn_ranges
        self.failing_install = set(failing_install)
        self.role_holder = role_holder
        self.resolved = resolved
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args: Sequence[str], timeout: float) -> Any:
        argv = [str(item) for item in args]
        self.calls.append(tuple(argv))
        rest = argv[1:]
        if rest[:1] == ["-s"]:
            rest = rest[2:]
        if rest == ["devices"]:
            return _ok("List of devices attached\nR58M12345\tdevice\n")
        if rest[:1] != ["shell"]:
            return _ok("")
        return self._shell(rest[1])

    @property
    def shell_commands(self) -> list[str]:
        return [call[-1] for call in self.calls if "shell" in call]

    def _enabled(self) -> str:
        if self.enabled_packages is not None:
            return self.enabled_packages
        names = [
            line.split(":")[1]
            for line in self.main_packages.splitlines()
            if line.startswith("package:")
            and line.split(":")[1] not in self.failing_install
        ]
        names.extend(self.enabled_extra)
        return "".join(f"package:{name}\n" for name in names)

    def _shell(self, command: str) -> Any:
        if command == "getprop ro.serialno":
            return _ok("R58M12345\n")
        if command == "pm list users":
            return _ok(USERS_OUTPUT)
        if command == "pm list packages --user 0":
            return _ok(self.main_packages)
        if command == "pm list packages --user 10 -e":
            return _ok(self._enabled())
        if command == "pm list packages --user 10 -U":
            return _ok(SETUP_USER10_UIDS)
        if command == "pm list packages --user 10":
            return _ok(self.main_packages)
        if command == "dumpsys vpn_management":
            return _ok(_vpn_dumpsys(self.vpn_ranges))
        if command.startswith("pm install-existing --user 10 "):
            package = command.rsplit(" ", 1)[1]
            if package in self.failing_install:
                return _err(
                    "Exception occurred while executing 'install-existing': "
                    f"java.lang.IllegalArgumentException: Unknown package: {package}"
                )
            return _ok(f"Package {package} installed for user: 10\n")
        if command.startswith("pm enable --user 10 "):
            package = command.rsplit(" ", 1)[1]
            return _ok(f"Package {package} new state: enabled\n")
        if command.startswith("cmd package unhide --user 10 "):
            return _err(
                "java.lang.SecurityException: Access denied, requires: "
                "android.permission.MANAGE_USERS"
            )
        if command.startswith("cmd role add-role-holder --user 10 "):
            return _ok("")
        if command.startswith("cmd role get-role-holders --user 10 "):
            return _ok(f"{self.role_holder}\n" if self.role_holder else "")
        if "resolve-activity" in command:
            return _ok(self.resolved)
        if command.startswith("content query"):
            return _ok("No result found.\n")
        return _ok("")


def _setup_client(runner: SetupAdb) -> Any:
    return tool.AdbClient(Path("adb"), runner=runner, timeout=1.0)


def test_parse_package_list_and_uids() -> None:
    assert tool.parse_package_list(
        "package:com.example.a\npackage:com.example.b\npackage:com.example.a\n"
    ) == ["com.example.a", "com.example.b"]
    assert tool.parse_package_uids(SETUP_USER10_UIDS)["com.android.chrome"] == 1010370
    assert tool.parse_package_uids("garbage\n") == {}


def test_parse_plain_package_names() -> None:
    assert tool.parse_plain_package_names("com.android.chrome\n") == [
        "com.android.chrome"
    ]
    assert tool.parse_plain_package_names("No holders\n") == []


def test_parse_active_vpn_and_uid_ranges() -> None:
    text = _vpn_dumpsys("0-10022, 20024-20407")
    assert tool.parse_active_vpn_packages(text) == ["com.v2ray.ang"]
    assert tool.parse_vpn_uid_ranges(text) == [(0, 10022), (20024, 20407)]
    assert tool.parse_active_vpn_packages("VPNs:\n") == []
    assert tool.parse_vpn_uid_ranges("no uids here") == []


def test_parallel_space_uid_coverage() -> None:
    ranges = [(0, 10022), (20024, 20407)]
    assert tool.parallel_uid_candidates(10370) == (10370,)
    assert tool.parallel_uid_candidates(1010370) == (1010370, 20370)
    assert tool.uid_in_ranges(1010370, ranges)
    assert not tool.uid_in_ranges(1010500, ranges)
    assert tool.uid_in_ranges(10401, [(0, 10407)])


def test_browser_component_ready_rejects_resolver() -> None:
    assert tool.browser_component_ready("com.android.chrome/.Main")
    assert not tool.browser_component_ready(tool.RESOLVER_COMPONENT)
    assert not tool.browser_component_ready(None)


def test_build_island_plan_prefers_browsers_in_order() -> None:
    plan = tool.build_island_plan(
        [
            "com.heytap.browser",
            "mark.via",
            "com.android.chrome",
            "com.google.android.gsf",
            "com.google.android.gms",
            "com.android.vending",
            "com.v2ray.ang",
        ],
        ["com.v2ray.ang"],
    )
    assert plan.browsers == (
        "com.android.chrome",
        "mark.via",
        "com.heytap.browser",
    )
    assert plan.core == (
        "com.google.android.gms",
        "com.google.android.gsf",
        "com.android.vending",
    )
    assert plan.vpn == ("com.v2ray.ang",)
    assert plan.absent_core == ()


def test_build_island_plan_can_exclude_vpn_and_pin_browser() -> None:
    plan = tool.build_island_plan(
        [
            "com.google.android.gms",
            "com.google.android.gsf",
            "com.android.vending",
            "com.android.chrome",
            "mark.via",
        ],
        ["com.v2ray.ang"],
        include_vpn=False,
        browser="mark.via",
    )
    assert plan.browsers == ("mark.via",)
    assert plan.vpn == ()


def test_build_island_plan_rejects_missing_gms() -> None:
    with pytest.raises(tool.ToolError):
        tool.build_island_plan(["com.android.chrome"], [])


def test_build_island_plan_rejects_missing_browser() -> None:
    with pytest.raises(tool.ToolError):
        tool.build_island_plan(
            ["com.google.android.gms", "com.google.android.gsf", "com.android.vending"],
            [],
        )


def test_run_island_setup_maps_enables_and_verifies() -> None:
    runner = SetupAdb()
    report = tool.run_island_setup(_setup_client(runner), writer=lambda _line: None)
    assert report.ok
    assert report.default_browser == "com.android.chrome"
    assert report.resolved_component.startswith("com.android.chrome/")
    assert report.missing_core == ()
    assert report.vpn_uncovered == ()
    commands = runner.shell_commands
    for package in ("com.google.android.gms", "com.android.chrome", "com.v2ray.ang"):
        assert f"pm install-existing --user 10 {package}" in commands
        assert f"pm enable --user 10 {package}" in commands
    assert (
        "cmd role add-role-holder --user 10 android.app.role.BROWSER com.android.chrome"
        in commands
    )
    rendered = report.render()
    assert "结论: PASS" in rendered
    assert "主空间 VPN 已覆盖" in rendered


def test_run_island_setup_skips_enable_when_install_fails() -> None:
    runner = SetupAdb(failing_install=("com.android.vending",))
    report = tool.run_island_setup(_setup_client(runner), writer=lambda _line: None)
    assert report.missing_core == ("com.android.vending",)
    assert not report.ok
    assert "pm enable --user 10 com.android.vending" not in runner.shell_commands
    failed = [step for step in report.steps if not step.ok]
    assert failed and failed[0].action == "install-existing"
    assert "FAIL" in report.render()


def test_run_island_setup_flags_uncovered_vpn() -> None:
    runner = SetupAdb(vpn_ranges="0-10022")
    report = tool.run_island_setup(_setup_client(runner), writer=lambda _line: None)
    assert "com.google.android.gms" in report.vpn_uncovered
    assert "com.android.chrome" in report.vpn_uncovered
    assert "断开重连" in report.render()


def test_run_island_setup_requires_concrete_browser() -> None:
    runner = SetupAdb(
        role_holder="",
        resolved=("priority=0\nandroid/com.android.internal.app.ResolverActivity\n"),
    )
    report = tool.run_island_setup(_setup_client(runner), writer=lambda _line: None)
    assert report.resolved_component == tool.RESOLVER_COMPONENT
    assert not report.browser_ready
    assert not report.ok
    assert "仍是系统选择器" in report.render()


def test_run_island_setup_can_unhide_best_effort() -> None:
    runner = SetupAdb()
    report = tool.run_island_setup(
        _setup_client(runner), unhide=True, writer=lambda _line: None
    )
    assert report.ok
    unhide = [step for step in report.steps if step.action == "unhide"]
    assert unhide and not unhide[0].ok
    assert (
        "cmd package unhide --user 10 com.google.android.gms" in runner.shell_commands
    )


def test_run_island_setup_rejects_non_work_profile() -> None:
    class Visitor(SetupAdb):
        def _shell(self, command: str) -> Any:
            if command == "pm list users":
                return _ok(
                    "Users:\n\tUserInfo{0:机主:c13} running\n"
                    "\tUserInfo{11:访客:400} running\n"
                )
            return super()._shell(command)

    with pytest.raises(tool.SafetyError):
        tool.run_island_setup(
            _setup_client(Visitor()), user_id=11, writer=lambda _line: None
        )


def test_parser_exposes_setup_island_options() -> None:
    args = tool.build_parser().parse_args(["setup-island"])
    assert args.command == "setup-island"
    assert args.no_vpn is False
    assert args.unhide is False
    assert args.browser is None
    assert args.user is None
