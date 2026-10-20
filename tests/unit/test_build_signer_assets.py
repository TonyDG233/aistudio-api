"""WP4a asset smoke tests: build_signer.js + Build-surface blocklist split.

Static assertions over the injected Build-host signer script and the Network
blocklist split (chat keeps the editor-library group; the Build surface drops it).
These run offline (no browser/CDP) as a guard for the frozen WP4a contract.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from aistudio_api.infrastructure.browser.cdp_client import (
    BLOCKED_URL_PATTERNS,
    BUILD_SURFACE_BLOCKED_URL_PATTERNS,
    EDITOR_LIBRARY_BLOCKED_URL_PATTERNS,
)
from aistudio_api.infrastructure.browser.scripts import BUILD_SIGNER_JS


def _signer_js() -> str:
    assert BUILD_SIGNER_JS is not None
    return BUILD_SIGNER_JS


JS_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "aistudio_api"
    / "infrastructure"
    / "browser"
    / "js"
    / "build_signer.js"
)


# ------------------------------------------------------------------ JS asset smoke


def test_build_signer_asset_loads():
    """The JS asset is present on disk and loaded by scripts.py."""
    assert JS_PATH.is_file(), f"missing asset: {JS_PATH}"
    assert len(_signer_js().strip()) > 0
    assert JS_PATH.read_text(encoding="utf-8") == _signer_js()


def test_build_signer_frozen_contract_surface():
    """Frozen contract surface: global name, methods, and binding push."""
    for token in (
        "window.__buildSigner",
        "__aistudio_stream_push__",
        "ready()",
        "lastError()",
        "execute(taskId, model, jsonBody)",
        "harvest()",
    ):
        assert token in _signer_js(), f"missing contract token: {token!r}"


def test_build_signer_install_and_state_machine():
    """Install guard, top-frame guard, and the five-state enum are all present."""
    assert "if (window.__buildSigner) return" in _signer_js()
    assert "window.top" in _signer_js()
    for state in ("installing", "harvesting", "warmup-captured", "ready", "failed"):
        assert f'"{state}"' in _signer_js(), f"missing state: {state!r}"


def test_build_signer_push_event_types():
    """Binding events carry rid/type/status/text/message per transport.py demux."""
    for token in (
        'type: "status"',
        'type: "chunk"',
        'type: "done"',
        'type: "error"',
        "rid: rid",
        "status: resp.status",
        "text: raw",
        "message:",
    ):
        assert token in _signer_js(), f"missing push token: {token!r}"


def test_build_signer_port_fidelity_markers():
    """Key Ark port anchors: hook targets, warmup swallow, RPC endpoint, codec."""
    for token in (
        "hookMakerSuite",
        "installFetchHarvest",
        "nativeFetch",
        "discoverSignerCandidates",
        "tokenLike",
        "kvc",
        "getMetadata",
        "RPC_HEADER_ALLOWLIST",
        "__warmup__",
        "Authorization",  # gRPC header; not a token leak (header name only)
        "scanArrayElements",
        "codecFactory",
        "ProxyStreamedCall",
        "alkalimakersuite-pa.clients6.google.com",
        'credentials: "include"',
    ):
        assert token in _signer_js(), f"missing port marker: {token!r}"


def test_build_signer_key_source_is_harvested_header():
    """E8: x-goog-api-key must come from the harvested header allowlist, never tb.mb.nK/jK."""
    assert '"x-goog-api-key"' in _signer_js()
    # The harvest allowlist must carry the key header name.
    allowlist_idx = _signer_js().index("RPC_HEADER_ALLOWLIST = [")
    allowlist_block = _signer_js()[allowlist_idx : allowlist_idx + 400]
    assert '"x-goog-api-key"' in allowlist_block


# ------------------------------------------------------------- blocklist split


def _matches_any(patterns: list[str], url: str) -> bool:
    """Approximate Chromium Network.setBlockedURLs wildcard matching ('*' glob)."""
    return any(fnmatch.fnmatchcase(url, pattern) for pattern in patterns)


def test_editor_library_group_is_subset_of_chat_list():
    assert EDITOR_LIBRARY_BLOCKED_URL_PATTERNS
    for pattern in EDITOR_LIBRARY_BLOCKED_URL_PATTERNS:
        assert pattern in BLOCKED_URL_PATTERNS


def test_chat_blocklist_unchanged():
    """Chat blocklist keeps the editor-library group and the pre-existing entries."""
    assert "*vs/editor*" in BLOCKED_URL_PATTERNS
    assert "*monaco-editor*" in BLOCKED_URL_PATTERNS
    assert "*.png" in BLOCKED_URL_PATTERNS
    assert "*SavePrompt*" in BLOCKED_URL_PATTERNS
    assert "*CreatePrompt*" in BLOCKED_URL_PATTERNS


def test_build_blocklist_drops_editor_library_group():
    """Build surface list is exactly the chat list minus the editor-library group."""
    assert "*vs/editor*" not in BUILD_SURFACE_BLOCKED_URL_PATTERNS
    for pattern in EDITOR_LIBRARY_BLOCKED_URL_PATTERNS:
        assert pattern not in BUILD_SURFACE_BLOCKED_URL_PATTERNS
    assert [
        p for p in BLOCKED_URL_PATTERNS if p not in EDITOR_LIBRARY_BLOCKED_URL_PATTERNS
    ] == BUILD_SURFACE_BLOCKED_URL_PATTERNS
    # Base protections survive the split (only editor libs are relaxed).
    assert "*.png" in BUILD_SURFACE_BLOCKED_URL_PATTERNS
    assert "*google-analytics.com*" in BUILD_SURFACE_BLOCKED_URL_PATTERNS
    assert "*CreatePrompt*" in BUILD_SURFACE_BLOCKED_URL_PATTERNS


def test_monaco_url_blocked_on_chat_but_allowed_on_build():
    """A Monaco editor URL that was previously blocked is no longer blocked for Build."""
    url = "https://aistudio.google.com/monaco-editor/editor.main.js"
    assert _matches_any(BLOCKED_URL_PATTERNS, url) is True
    assert _matches_any(BUILD_SURFACE_BLOCKED_URL_PATTERNS, url) is False

    vs_url = "https://aistudio.google.com/genai/vs/editor/editorMain.js"
    assert _matches_any(BLOCKED_URL_PATTERNS, vs_url) is True
    assert _matches_any(BUILD_SURFACE_BLOCKED_URL_PATTERNS, vs_url) is False
