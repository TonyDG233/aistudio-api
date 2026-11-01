"""Deterministic per-account browser fingerprint profiles (P3-F).

The browser launches with a mobile/software renderer fallback
(``--use-angle=swiftshader`` in :mod:`browser_engine`). That is fine for the
network stack, but ``WEBGL_debug_renderer_info`` would expose SwiftShader unless
every account page receives a realistic desktop profile before navigation.

Profiles are derived exclusively from the normalized account identifier (or the
auth file identity: parent directory + file name when no account id is
available), so the same account always gets the same Windows desktop fingerprint
while a different account gets a different one. No real browser is required to
build a profile or its JS payload.

Failure policy (consistent with session startup):
    * ``Page.addScriptToEvaluateOnNewDocument`` is fail-closed. If the script
      cannot be registered the session bootstrap raises, the page is closed and
      the pool marks the instance invalid. Continuing would silently expose a
      SwiftShader renderer to upstream scripts.
    * ``Emulation.setDeviceMetricsOverride`` is best-effort. The JS payload
      already overrides ``screen``/``devicePixelRatio``; metrics only keep the
      native layout viewport coherent, so a failure is logged and ignored.
    * When ``browser.fingerprint_spoof_enabled`` is explicitly false, nothing is
      injected at all (no script, no metrics override).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, TypeVar

from aistudio_api.config import resolve_fingerprint_spoof_enabled
from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.browser.scripts import FINGERPRINT_SPOOF_JS
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("browser-fingerprint")

_SEED_NAMESPACE = "aistudio-browser-fingerprint:v1"
_DEFAULT_ACCOUNT_KEY = "default-account"

# Any of these strings in a fingerprint means the profile would expose a
# software renderer. They must never reach a generated profile.
FORBIDDEN_RENDERER_TOKENS: tuple[str, ...] = (
    "swiftshader",
    "mesa",
    "llvmpipe",
    "software rasterizer",
    "basic render driver",
)

T = TypeVar("T")


class _GpuProfile(NamedTuple):
    vendor: str
    renderer: str
    family: str


class _ScreenProfile(NamedTuple):
    width: int
    height: int
    taskbar_height: int
    device_pixel_ratio: float


# Real Windows desktop ANGLE/D3D11 combinations. Vendor and renderer always come
# from the same family so fingerprinting checks see a coherent pair.
_GPU_PROFILES: tuple[_GpuProfile, ...] = (
    _GpuProfile(
        "Google Inc. (NVIDIA)",
        "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "nvidia",
    ),
    _GpuProfile(
        "NVIDIA Corporation",
        "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "nvidia",
    ),
    _GpuProfile(
        "Google Inc. (NVIDIA)",
        "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "nvidia",
    ),
    _GpuProfile(
        "Google Inc. (Intel)",
        "ANGLE (Intel, Intel(R) UHD Graphics 630 (0x00003E9B) Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "intel",
    ),
    _GpuProfile(
        "Intel",
        "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics (0x00009A49) Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "intel",
    ),
    _GpuProfile(
        "Google Inc. (AMD)",
        "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "amd",
    ),
    _GpuProfile(
        "AMD",
        "ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "amd",
    ),
)

_SCREEN_PROFILES: tuple[_ScreenProfile, ...] = (
    _ScreenProfile(1920, 1080, 40, 1.0),
    _ScreenProfile(2560, 1440, 40, 1.0),
    _ScreenProfile(1680, 1050, 40, 1.0),
    _ScreenProfile(1920, 1200, 40, 1.0),
    _ScreenProfile(2560, 1440, 40, 1.5),
)

_HARDWARE_CONCURRENCY_POOL: tuple[int, ...] = (8, 12, 16, 24)
_DEVICE_MEMORY_POOL: tuple[int, ...] = (8, 16, 32)
_CHROME_MAJOR_POOL: tuple[int, ...] = (131, 132, 133, 134, 135, 136)
_WINDOWS_PLATFORM_VERSION_POOL: tuple[str, ...] = ("15.0.0", "10.0.0")
_LANGUAGE_POOL: tuple[tuple[str, ...], ...] = (
    ("en-US", "en"),
    ("en-US", "en-GB", "en"),
    ("en-US", "en-CA", "en"),
)


@dataclass(frozen=True, slots=True)
class PluginInfo:
    """One navigator.plugins entry."""

    name: str
    filename: str
    description: str
    mime_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BrowserFingerprintProfile:
    """Deterministic Windows desktop fingerprint for one account."""

    fingerprint_id: str
    seed: str
    account_key: str
    webgl_vendor: str
    webgl_renderer: str
    gpu_family: str
    hardware_concurrency: int
    device_memory: int
    screen_width: int
    screen_height: int
    avail_width: int
    avail_height: int
    device_pixel_ratio: float
    color_depth: int
    pixel_depth: int
    inner_width: int
    inner_height: int
    outer_width: int
    outer_height: int
    platform: str
    vendor: str
    user_agent: str
    ua_major: int
    ua_full_version: str
    ua_brands: tuple[tuple[str, str], ...]
    ua_full_version_list: tuple[tuple[str, str], ...]
    platform_version: str
    languages: tuple[str, ...]
    plugins: tuple[PluginInfo, ...]
    canvas_noise_seed: int
    audio_noise_seed: int
    webdriver: bool = False
    max_touch_points: int = 0


_PLUGIN_SPECS: tuple[PluginInfo, ...] = (
    PluginInfo(
        "PDF Viewer",
        "internal-pdf-viewer",
        "Portable Document Format",
        ("application/pdf", "text/pdf"),
    ),
    PluginInfo(
        "Chrome PDF Viewer",
        "internal-pdf-viewer",
        "Portable Document Format",
        ("application/pdf",),
    ),
    PluginInfo(
        "Chromium PDF Viewer",
        "internal-pdf-viewer",
        "Portable Document Format",
        ("application/pdf",),
    ),
)


def _digest(seed_hex: str, label: str) -> bytes:
    return hashlib.sha256(f"{seed_hex}:{label}".encode()).digest()


def _pick(seed_hex: str, label: str, pool: Sequence[T]) -> T:
    if not pool:
        raise ValueError(f"fingerprint pool {label!r} must not be empty")
    index = int.from_bytes(_digest(seed_hex, label)[:4], "big") % len(pool)
    return pool[index]


def _pick_uint32(seed_hex: str, label: str) -> int:
    return int.from_bytes(_digest(seed_hex, label)[:4], "big")


def _auth_file_identity(auth_file: str) -> str:
    """Return a stable account identity for an auth file.

    Auth files are commonly all named ``auth.json`` inside per-account
    directories, so the parent directory name is part of the identity.
    """
    path = Path(str(auth_file))
    name = path.name or str(auth_file)
    parent = path.parent.name
    if parent and parent not in (".", "..", path.anchor):
        return f"{parent}/{name}"
    return name


def normalize_account_key(
    account_id: str | None = None,
    auth_file: str | None = None,
) -> str:
    """Normalize the account identity used as fingerprint seed input.

    ``account_id`` wins. When it is absent the auth file identity (parent
    directory + file name) is used, so the same account keeps one fingerprint
    regardless of the absolute directory the auth file lives in.
    """
    raw = (account_id or "").strip()
    if not raw and auth_file:
        raw = _auth_file_identity(auth_file)
    if not raw:
        raw = _DEFAULT_ACCOUNT_KEY
    return raw.casefold()


def build_fingerprint_seed(account_key: str) -> str:
    """Return the SHA-256 seed hex for a normalized account key."""
    return hashlib.sha256(
        f"{_SEED_NAMESPACE}:{account_key}".encode()
    ).hexdigest()


def build_fingerprint_profile(
    account_id: str | None = None,
    auth_file: str | None = None,
) -> BrowserFingerprintProfile:
    """Build the stable desktop profile for one account."""
    account_key = normalize_account_key(account_id=account_id, auth_file=auth_file)
    seed_hex = build_fingerprint_seed(account_key)

    gpu = _pick(seed_hex, "gpu", _GPU_PROFILES)
    screen = _pick(seed_hex, "screen", _SCREEN_PROFILES)
    ua_major = _pick(seed_hex, "chrome-major", _CHROME_MAJOR_POOL)
    ua_build = _pick(seed_hex, "chrome-build", tuple(range(6100, 7200)))
    ua_patch = _pick(seed_hex, "chrome-patch", tuple(range(220)))
    ua_full_version = f"{ua_major}.0.{ua_build}.{ua_patch}"

    avail_height = screen.height - screen.taskbar_height
    inner_width = max(800, screen.width - 16)
    inner_height = max(600, avail_height - 88)

    profile = BrowserFingerprintProfile(
        fingerprint_id=seed_hex[:16],
        seed=seed_hex,
        account_key=account_key,
        webgl_vendor=gpu.vendor,
        webgl_renderer=gpu.renderer,
        gpu_family=gpu.family,
        hardware_concurrency=_pick(
            seed_hex, "hardware-concurrency", _HARDWARE_CONCURRENCY_POOL
        ),
        device_memory=_pick(seed_hex, "device-memory", _DEVICE_MEMORY_POOL),
        screen_width=screen.width,
        screen_height=screen.height,
        avail_width=screen.width,
        avail_height=avail_height,
        device_pixel_ratio=screen.device_pixel_ratio,
        color_depth=24,
        pixel_depth=24,
        inner_width=inner_width,
        inner_height=inner_height,
        outer_width=screen.width,
        outer_height=avail_height,
        platform="Win32",
        vendor="Google Inc.",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{ua_major}.0.0.0 Safari/537.36"
        ),
        ua_major=ua_major,
        ua_full_version=ua_full_version,
        ua_brands=(
            ("Chromium", str(ua_major)),
            ("Google Chrome", str(ua_major)),
            ("Not_A Brand", "24"),
        ),
        ua_full_version_list=(
            ("Chromium", ua_full_version),
            ("Google Chrome", ua_full_version),
            ("Not_A Brand", "24.0.0.0"),
        ),
        platform_version=_pick(
            seed_hex, "windows-platform-version", _WINDOWS_PLATFORM_VERSION_POOL
        ),
        languages=_pick(seed_hex, "languages", _LANGUAGE_POOL),
        plugins=_PLUGIN_SPECS,
        canvas_noise_seed=_pick_uint32(seed_hex, "canvas-noise"),
        audio_noise_seed=_pick_uint32(seed_hex, "audio-noise"),
    )
    _validate_profile(profile)
    return profile


def _validate_profile(profile: BrowserFingerprintProfile) -> None:
    haystack = (
        f"{profile.webgl_vendor} {profile.webgl_renderer} {profile.user_agent}"
    ).casefold()
    for token in FORBIDDEN_RENDERER_TOKENS:
        if token in haystack:
            raise RuntimeError(
                "拒绝生成软件渲染器指纹：发现禁用标记 "
                f"{token!r} (account={profile.account_key})"
            )
    vendor_family = profile.webgl_vendor.casefold()
    renderer_family = profile.webgl_renderer.casefold()
    family = profile.gpu_family.casefold()
    if family not in vendor_family and family not in renderer_family:
        raise RuntimeError(
            "WebGL vendor/renderer 不属于同一硬件家族："
            f"vendor={profile.webgl_vendor!r} renderer={profile.webgl_renderer!r}"
        )
    if "direct3d11" not in renderer_family:
        raise RuntimeError(
            f"WebGL renderer 缺少 Direct3D11 标记: {profile.webgl_renderer!r}"
        )


def build_fingerprint_payload(
    profile: BrowserFingerprintProfile,
) -> dict[str, object]:
    """Serialize a profile into the JSON payload consumed by the JS hook."""
    return {
        "fingerprintId": profile.fingerprint_id,
        "webglVendor": profile.webgl_vendor,
        "webglRenderer": profile.webgl_renderer,
        "hardwareConcurrency": profile.hardware_concurrency,
        "deviceMemory": profile.device_memory,
        "screenWidth": profile.screen_width,
        "screenHeight": profile.screen_height,
        "availWidth": profile.avail_width,
        "availHeight": profile.avail_height,
        "devicePixelRatio": profile.device_pixel_ratio,
        "colorDepth": profile.color_depth,
        "pixelDepth": profile.pixel_depth,
        "innerWidth": profile.inner_width,
        "innerHeight": profile.inner_height,
        "outerWidth": profile.outer_width,
        "outerHeight": profile.outer_height,
        "platform": profile.platform,
        "vendor": profile.vendor,
        "userAgent": profile.user_agent,
        "platformVersion": profile.platform_version,
        "languages": list(profile.languages),
        "uaBrands": [
            {"brand": brand, "version": version}
            for brand, version in profile.ua_brands
        ],
        "uaFullVersion": profile.ua_full_version,
        "uaFullVersionList": [
            {"brand": brand, "version": version}
            for brand, version in profile.ua_full_version_list
        ],
        "webdriver": profile.webdriver,
        "maxTouchPoints": profile.max_touch_points,
        "plugins": [
            {
                "name": plugin.name,
                "filename": plugin.filename,
                "description": plugin.description,
                "mimeTypes": list(plugin.mime_types),
            }
            for plugin in profile.plugins
        ],
        "canvasNoiseSeed": profile.canvas_noise_seed,
        "audioNoiseSeed": profile.audio_noise_seed,
    }


def build_device_metrics(
    profile: BrowserFingerprintProfile,
) -> dict[str, object]:
    """Build ``Emulation.setDeviceMetricsOverride`` parameters for a profile."""
    return {
        "width": profile.inner_width,
        "height": profile.inner_height,
        "deviceScaleFactor": profile.device_pixel_ratio,
        "mobile": False,
        "screenWidth": profile.screen_width,
        "screenHeight": profile.screen_height,
        "positionX": 0,
        "positionY": 0,
        "dontSetVisibleSize": False,
        "screenOrientation": {"type": "landscapePrimary", "angle": 0},
    }


def build_fingerprint_script(profile: BrowserFingerprintProfile) -> str:
    """Return the self-contained JS source registered for every new document."""
    payload = build_fingerprint_payload(profile)
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"({FINGERPRINT_SPOOF_JS.strip()})({serialized});"


async def apply_fingerprint(
    page: CDPPage,
    profile: BrowserFingerprintProfile,
    *,
    enabled: bool | None = None,
) -> bool:
    """Apply the profile to ``page`` before its first navigation.

    Script registration is fail-closed; device metrics are best-effort. Returns
    ``True`` when the script was registered, ``False`` when spoofing is disabled.
    """
    spoof_enabled = (
        resolve_fingerprint_spoof_enabled() if enabled is None else enabled
    )
    if not spoof_enabled:
        log.debug(
            "浏览器指纹伪装已关闭，跳过注入 (account=%s)", profile.account_key
        )
        return False

    script = build_fingerprint_script(profile)
    try:
        await page.cdp.send(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": script},
        )
    except Exception as exc:
        raise RuntimeError(
            "浏览器指纹脚本注册失败（可能导致 SwiftShader 指纹泄露）: "
            f"account={profile.account_key} error={exc}"
        ) from exc

    try:
        await page.cdp.send(
            "Emulation.setDeviceMetricsOverride",
            build_device_metrics(profile),
        )
    except Exception as exc:
        log.warning(
            "浏览器指纹屏幕指标覆盖失败（JS screen 伪装仍生效）: account=%s error=%s",
            profile.account_key,
            exc,
        )

    log.info(
        "已注入浏览器指纹: id=%s account=%s gpu=%s concurrency=%d screen=%dx%d@%s",
        profile.fingerprint_id,
        profile.account_key,
        profile.gpu_family,
        profile.hardware_concurrency,
        profile.screen_width,
        profile.screen_height,
        profile.device_pixel_ratio,
    )
    return True


__all__ = [
    "FORBIDDEN_RENDERER_TOKENS",
    "BrowserFingerprintProfile",
    "PluginInfo",
    "apply_fingerprint",
    "build_device_metrics",
    "build_fingerprint_payload",
    "build_fingerprint_profile",
    "build_fingerprint_script",
    "build_fingerprint_seed",
    "normalize_account_key",
]
