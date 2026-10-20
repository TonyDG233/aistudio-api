"""Offline unit tests for the Build surface protocol layer (WP4b).

All fixtures are hermetic: the Holder frames reproduce the two real
``ProxyStreamedCall`` chunks captured in ``spikeP-evidence.json`` (see
``docs/dev/PRE-P1-EXPERIMENTS-20261018.md`` E1) and are wrapped in the observed
nested-array envelope locally. No network, browser or model is touched; the CDP
page/client are duck-typed fakes.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage
from aistudio_api.infrastructure.gateway import build_surface as bs

# --------------------------------------------------------------------------- #
# Real Holder fixtures (spikeP-evidence.json → frames_full[0] / frames_full[1]).
# --------------------------------------------------------------------------- #

FRAME0_CHUNK: dict[str, object] = {
    "candidates": [
        {
            "content": {"parts": [{"text": "OK"}], "role": "model"},
            "index": 0,
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 7,
        "candidatesTokenCount": 1,
        "totalTokenCount": 153,
        "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 7}],
        "thoughtsTokenCount": 145,
        "serviceTier": "SERVICE_TIER_STANDARD",
        "rawPromptTokenCount": 38,
    },
    "modelVersion": "gemini-3.8-flash",
    "responseId": "SWrUao2MIJ6W9MoPm6fosQE",
    "turnToken": "v1_ChdTV3JVYXBhNkhMcXYtOFlQb3VfMDBRTRIXU1dyVWFvMk1JSjZXOU1vUG02Zm9zUUU",
}

FRAME1_SIGNATURE = (
    "ErYFCrMFAWkUfROkEbIr7LReqLAepldi+Fnc3WVXth2PvQKLPm34SPEriUVzDA4GmSeSdrs/0HCOpboBXoKXi"
    "KpiYF4vhIMb6rj/5sqedAkgExYn8i4tVo+HDeXGAOScpyDhcZYdEmxTC4SATq4x0apWvkEBnmb0hdibnKj1e3r1"
    "bsrVRo9jXkdGKg5O8HvB/aLi9KBxaDvq4u2tf0xz29OApHIt326hcw6LqHsuxPF0IoFUHS7diSClMThyMwNyN9mN"
    "N3G3taIX/l6JHvCNPX4Tv/9HBbckEnBVEJGH78Zp3AV9nYo7NbZcG2U5hRhi4ly79OTdmZ5GyVqGaEtdLIBAFKN"
    "LwrBIt6kxHb0ZZkD8qYiYV08qiW6j1podW9IijcGfEM5DlJ6aT9xoSqFs1iNWe/ySOBKEoBE+Ifj03pdPX8ABOE5"
    "0qOV0q8bhLaadKhDLH5vyvQL/A+dObt+hBY5RJ2+D+m6TTGgTw7Z+rFfR39CeboCdt/spWT+2WRE7ajMgLOCBVcC"
    "cWmeLrHhjyghJoo2a9/+vx1xBVIOGOTg2OEgOGl+jiQckkxyG0h/xgfJMnA6dAsa56pIVL9rLd02pWJb+iYZhnMz"
    "CBMhB88wMcf9O/8m6/88OxO61XeHUPrTWyzVD+Abi7MuVp3gBboKqQB78Z/JObjbPH0zqbnT+LcAL3nGJHc3FluK"
    "hLPBCJXh4uJm/TSPk+xfaBWSNM8hRIFZ+yNwrP0Xlh0uyim8xNE0MbcLS5l4kbjPt3pU4RLyx6KpXCP3rmjPoFnJ"
    "bDSfKTwtk6J/JEwLoXe5iWlaYEKsD65F15mLHMPXFJMT95wFo7u5K50lX1d8ffhbDmHAE7YBvsO3hUHB3jje3v5w"
    "pRIfWrKz1topu0NjLlY00x3wA1dTi50fhvsDnlBvzM7NfdMoBaxCZ9w=="
)

FRAME1_CHUNK: dict[str, object] = {
    "candidates": [
        {
            "content": {
                "parts": [{"text": "", "thoughtSignature": FRAME1_SIGNATURE}],
                "role": "model",
            },
            "finishReason": "STOP",
            "index": 0,
            "detailedFinishReason": {
                "recipeRunnerFinishReason": "GRANULAR_FINISH_REASON_NATURAL_STOP"
            },
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 7,
        "candidatesTokenCount": 1,
        "totalTokenCount": 153,
        "thoughtsTokenCount": 145,
        "rawPromptTokenCount": 38,
    },
    "modelVersion": "gemini-3.8-flash",
    "responseId": "SWrUao2MIJ6W9MoPm6fosQE",
    "turnToken": "v1_ChdTV3JVYXBhNkhMcXYtOFlQb3VfMDBRTRIXU1dyVWFvMk1JSjZXOU1vUG02Zm9zUUU",
}


def _holder(chunk: dict[str, object], depth: int = 2) -> str:
    """Wrap a REST chunk the way ProxyStreamedCall does (2–3 nested arrays)."""
    payload: object = json.dumps(chunk, ensure_ascii=False)
    for _ in range(depth - 1):
        payload = [payload]
    return json.dumps(payload, ensure_ascii=False)


def _text_chunk(text: str, response_id: str) -> dict[str, object]:
    return {
        "candidates": [
            {"content": {"parts": [{"text": text}], "role": "model"}, "index": 0}
        ],
        "responseId": response_id,
    }


# --------------------------------------------------------------------------- #
# Duck-typed fakes.
# --------------------------------------------------------------------------- #


class FakePage:
    def __init__(
        self,
        *,
        states: list[object] | None = None,
        detect: dict[str, object] | None = None,
        events_for: Callable[[str], list[dict[str, object]]] | None = None,
        closed: bool = False,
        close_on_dispatch: bool = False,
    ) -> None:
        self.has_build_surface_binding = False
        self._closed = closed
        self._close_on_dispatch = close_on_dispatch
        self._states = list(states or [])
        self._last_state: object = self._states[-1] if self._states else None
        self._detect = detect
        self._events_for = events_for
        self.binding_listener: Callable[[str], object] | None = None
        self.added_bindings: list[str] = []
        self.goto_url = ""
        self.blocked: list[str] = []
        self.cookies: list[object] = []
        self.cdp = MagicMock()
        self.cdp.send = AsyncMock()

    def is_closed(self) -> bool:
        return self._closed

    async def set_cookies(self, cookies: list[object]) -> None:
        self.cookies = list(cookies)

    async def set_blocked_urls(self, patterns: list[str]) -> None:
        self.blocked = list(patterns)

    async def add_binding(self, name: str) -> None:
        self.added_bindings.append(name)

    def on_binding(self, name: str, callback: Callable[[str], object]) -> None:
        if name == bs.STREAM_PUSH_BINDING:
            self.binding_listener = callback

    async def goto(
        self, url: str, timeout_s: float = 30.0, wait_until: str = "domcontentloaded"
    ) -> None:
        self.goto_url = url

    async def close(self) -> None:
        self._closed = True

    async def evaluate(
        self,
        expression: str,
        args: object = None,
        await_promise: bool = True,
        return_by_value: bool = True,
        timeout_s: float = 30.0,
    ) -> object:
        if expression is bs.BUILD_SIGNER_STATE_JS:
            if self._states:
                self._last_state = self._states.pop(0)
            return self._last_state
        if expression is bs.BUILD_SURFACE_DETECT_JS:
            return self._detect
        if expression is bs.BUILD_SIGNER_EXECUTE_JS:
            rid = str(args.get("taskId")) if isinstance(args, dict) else ""
            if self._close_on_dispatch:
                self._closed = True
            if self._events_for is not None and self.binding_listener is not None:
                for event in self._events_for(rid):
                    self.binding_listener(json.dumps(event))
            return None
        return None


class FakeCDPClient:
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.create_new_page = AsyncMock(return_value=page)


def _auth_file(tmp_path: Path) -> str:
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps(
            {"cookies": [{"name": "SID", "value": "x", "domain": ".google.com"}]}
        ),
        encoding="utf-8",
    )
    return str(path)


def _build_surface(page: FakePage) -> bs.BuildSurface:
    return bs.BuildSurface(cast(CDPClient, FakeCDPClient(page)))


def _surface(page: FakePage) -> bs.BuildSurface:
    surface = _build_surface(page)
    surface.state.page = cast(CDPPage, page)
    surface.state.ready = True
    surface.state.ready_state = "ready"
    return surface


@pytest.fixture
def fast_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip interstitial/gate loops in unit tests (covered by live acceptance)."""

    async def _noop(self: object, page: object, **_kwargs: object) -> None:
        return None

    async def _app_connected(self: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(bs.BuildSurface, "_dismiss_interstitials", _noop)
    monkeypatch.setattr(bs.BuildSurface, "_pass_human_gate", _noop)
    monkeypatch.setattr(bs.BuildSurface, "_wait_app_connect", _app_connected)


def _holder_events(
    frame_text: str,
) -> Callable[[str], list[dict[str, object]]]:
    def factory(rid: str) -> list[dict[str, object]]:
        return [
            {"rid": rid, "type": "status", "status": 200},
            {"rid": rid, "type": "chunk", "text": frame_text},
            {"rid": rid, "type": "done"},
        ]

    return factory


async def _collect(
    gen: AsyncGenerator[tuple[str, object], None],
) -> list[tuple[str, object]]:
    return [event async for event in gen]


# --------------------------------------------------------------------------- #
# 1. Holder unwrapping + wire parsing.
# --------------------------------------------------------------------------- #


def test_decode_holder_three_level_nested_array():
    chunks, dropped = bs.decode_holder_frame(_holder(FRAME0_CHUNK, depth=3))
    assert dropped == []
    assert len(chunks) == 1
    wire = bs.conv_chunk(chunks[0])
    assert wire is not None
    candidate = bs.parse_response_chunk(wire)
    assert candidate.text == "OK"
    usage = bs.parse_chunk_usage(wire)
    assert usage["total_tokens"] == 153
    assert usage["prompt_tokens"] == 7
    details = cast(dict[str, object], usage["completion_tokens_details"])
    assert details["reasoning_tokens"] == 145


def test_decode_holder_two_level_and_direct_string():
    for frame_text in (
        _holder(FRAME0_CHUNK, depth=2),
        json.dumps(FRAME0_CHUNK, ensure_ascii=False),  # bare leaf, already unwrapped
        json.dumps(json.dumps(FRAME0_CHUNK)),  # single-escaped literal
    ):
        chunks, dropped = bs.decode_holder_frame(frame_text)
        assert dropped == [], frame_text
        wire = bs.conv_chunk(chunks[0])
        assert wire is not None
        assert bs.parse_response_chunk(wire).text == "OK"


def test_conv_chunk_multi_leaf_frame_parses_finish_and_signature():
    frame_text = json.dumps(
        [[json.dumps(FRAME0_CHUNK), json.dumps(FRAME1_CHUNK)]], ensure_ascii=False
    )
    chunks, dropped = bs.decode_holder_frame(frame_text)
    assert dropped == []
    assert len(chunks) == 2
    w0 = bs.conv_chunk(chunks[0])
    w1 = bs.conv_chunk(chunks[1])
    assert w0 is not None
    assert w1 is not None
    first = bs.parse_response_chunk(w0)
    second = bs.parse_response_chunk(w1)
    assert first.text == "OK"
    assert second.finish_reason == 1  # STOP
    assert second.thought_signature == FRAME1_SIGNATURE
    usage = bs.parse_chunk_usage(w1)
    assert usage["completion_tokens"] == 146  # 1 visible + 145 reasoning
    assert w1[7] == "SWrUao2MIJ6W9MoPm6fosQE"


def test_decode_holder_reports_undecodable_leaf_without_silent_drop():
    frame_text = json.dumps([["not json at all", json.dumps(FRAME0_CHUNK)]])
    chunks, dropped = bs.decode_holder_frame(frame_text)
    assert len(chunks) == 1
    assert dropped and "not json" in dropped[0]


def test_decode_holder_invalid_frame_is_diagnostic_only():
    chunks, dropped = bs.decode_holder_frame("<<< not json >>>")
    assert chunks == []
    assert dropped


def test_conv_chunk_jspb_position_array_compatibility_branch():
    candidate = [[[[None, "hi"]], "model"], 1]
    wire = bs.conv_chunk([[candidate]])
    assert wire is not None
    parsed = bs.parse_response_chunk(wire)
    assert parsed.text == "hi"
    assert parsed.finish_reason == 1


def test_conv_chunk_upstream_error_dict_raises():
    with pytest.raises(bs.BuildSurfaceError) as excinfo:
        bs.conv_chunk({"error": {"message": "quota exceeded"}})
    assert excinfo.value.code == "UPSTREAM_ERROR"
    assert "quota exceeded" in str(excinfo.value)


def test_conv_chunk_rpc_error_record_raises():
    with pytest.raises(bs.BuildSurfaceError) as excinfo:
        bs.conv_chunk([3, "invalid argument", []])
    assert excinfo.value.code == "UPSTREAM_RPC_ERROR"
    assert "rpc_code=3" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 2. app id resolution.
# --------------------------------------------------------------------------- #


def test_resolve_app_id_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "AISTUDIO_APP_URL",
        "https://aistudio.google.com/apps/3b85b4ed-f945-422c-ba25-395d1feef528?x=1",
    )
    assert bs.resolve_build_app_id() == "3b85b4ed-f945-422c-ba25-395d1feef528"


def test_resolve_app_id_defaults_to_ark(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AISTUDIO_APP_URL", raising=False)
    monkeypatch.setattr(
        "aistudio_api.infrastructure.gateway.model_defaults.get_features_config",
        lambda: {"channels": {"build": {"app_url": ""}}},
    )
    assert bs.resolve_build_app_id() == bs.DEFAULT_BUILD_APP_ID


def test_resolve_app_id_from_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AISTUDIO_APP_URL", raising=False)
    monkeypatch.setattr(
        "aistudio_api.infrastructure.gateway.model_defaults.get_features_config",
        lambda: {
            "channels": {
                "build": {
                    "app_url": "https://aistudio.google.com/apps/"
                    "3b85b4ed-f945-422c-ba25-395d1feef528"
                }
            }
        },
    )
    assert bs.resolve_build_app_id() == "3b85b4ed-f945-422c-ba25-395d1feef528"


def test_resolve_app_id_accepts_raw_id(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AISTUDIO_APP_URL", "3b85b4ed-f945-422c-ba25-395d1feef528")
    monkeypatch.setattr(
        "aistudio_api.infrastructure.gateway.model_defaults.get_features_config",
        lambda: {"channels": {"build": {"app_url": ""}}},
    )
    assert bs.resolve_build_app_id() == "3b85b4ed-f945-422c-ba25-395d1feef528"


# --------------------------------------------------------------------------- #
# 3. ensure_ready success + failure branches.
# --------------------------------------------------------------------------- #


async def test_ensure_ready_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_gate: None
):
    monkeypatch.setattr(bs, "_load_build_signer_js", lambda: "(() => {})()")
    monkeypatch.setattr(bs, "_load_build_blocked_patterns", lambda: ["*analytics*"])
    page = FakePage(
        states=[
            {"state": "loading", "harvest": None, "lastError": ""},
            {
                "state": "ready",
                "harvest": {"tb": {"mb": {}}},
                "lastError": "",
            },
        ]
    )
    surface = _build_surface(page)
    state = await surface.ensure_ready(_auth_file(tmp_path), "0", account_id="acc-1")
    assert state.ready is True
    assert state.ready_state == "ready"
    assert state.harvest == {"tb": {"mb": {}}}
    assert state.app_id == bs.DEFAULT_BUILD_APP_ID
    assert page.blocked == ["*analytics*"]
    assert page.goto_url == (
        f"https://aistudio.google.com/apps/{bs.DEFAULT_BUILD_APP_ID}"
        "?showAssistant=true&showPreview=true"
    )
    assert any(
        call.args[0] == "Page.addScriptToEvaluateOnNewDocument"
        for call in page.cdp.send.await_args_list
    )


async def test_ensure_ready_keeps_native_when_makersuite_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_gate: None
):
    monkeypatch.setattr(bs, "_load_build_signer_js", lambda: "(() => {})()")
    monkeypatch.setattr(bs, "_load_build_blocked_patterns", list)
    page = FakePage(
        states=[{"state": "loading", "lastError": ""}],
        detect={
            "code": "MAKERSUITE_MISSING",
            "message": "window.default_MakerSuite 不存在",
        },
    )
    surface = _build_surface(page)
    state = await surface.ensure_ready(_auth_file(tmp_path), ready_timeout_s=0.01)
    assert state.native_ready is True
    assert state.ready is True
    assert state.signer_ready is False
    assert "default_MakerSuite" in state.signer_error


async def test_ensure_ready_keeps_native_when_signer_funcs_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_gate: None
):
    monkeypatch.setattr(bs, "_load_build_signer_js", lambda: "(() => {})()")
    monkeypatch.setattr(bs, "_load_build_blocked_patterns", list)
    page = FakePage(
        states=[{"state": "loading", "lastError": ""}],
        detect={"code": "SIGNER_MISSING", "message": "签名脚本未注入"},
    )
    surface = _build_surface(page)
    state = await surface.ensure_ready(_auth_file(tmp_path), ready_timeout_s=0.01)
    assert state.native_ready is True
    assert state.signer_ready is False
    assert "签名脚本未注入" in state.signer_error


async def test_ensure_ready_keeps_native_when_tb_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_gate: None
):
    monkeypatch.setattr(bs, "_load_build_signer_js", lambda: "(() => {})()")
    monkeypatch.setattr(bs, "_load_build_blocked_patterns", list)
    page = FakePage(
        states=[{"state": "loading", "lastError": ""}],
        detect={"code": "TB_MISSING", "message": "tb 实例未捕获"},
    )
    surface = _build_surface(page)
    state = await surface.ensure_ready(_auth_file(tmp_path), ready_timeout_s=0.01)
    assert state.native_ready is True
    assert state.ready is True
    assert state.signer_ready is False
    assert "tb 实例未捕获" in state.signer_error


async def test_ensure_ready_records_signer_failure_without_blocking_native(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_gate: None
):
    monkeypatch.setattr(bs, "_load_build_signer_js", lambda: "(() => {})()")
    monkeypatch.setattr(bs, "_load_build_blocked_patterns", list)
    page = FakePage(states=[{"state": "failed", "lastError": "hook target missing"}])
    surface = _build_surface(page)
    state = await surface.ensure_ready(_auth_file(tmp_path))
    assert state.native_ready is True
    assert state.ready is True
    assert state.signer_ready is False
    assert state.signer_error == "hook target missing"


async def test_ensure_ready_signer_script_missing_still_enables_native(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_gate: None
):
    monkeypatch.setattr(bs, "_load_build_signer_js", lambda: None)
    monkeypatch.setattr(bs, "_load_build_blocked_patterns", list)
    page = FakePage(states=[{"state": "loading"}])
    surface = _build_surface(page)
    state = await surface.ensure_ready(_auth_file(tmp_path))
    assert state.native_ready is True
    assert state.ready is True
    assert state.signer_ready is False
    assert "build_signer.js" in state.signer_error


async def test_ensure_ready_cookies_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bs, "_load_build_signer_js", lambda: "(() => {})()")
    monkeypatch.setattr(bs, "_load_build_blocked_patterns", list)
    page = FakePage(states=[{"state": "loading"}])
    surface = _build_surface(page)
    with pytest.raises(bs.BuildSurfaceError) as excinfo:
        await surface.ensure_ready(None)
    assert excinfo.value.code == "COOKIES_MISSING"


# --------------------------------------------------------------------------- #
# 4. stream_generate event mapping + failure semantics.
# --------------------------------------------------------------------------- #


async def test_stream_generate_maps_holder_to_chat_events():
    page = FakePage(events_for=_holder_events(_holder(FRAME0_CHUNK, depth=3)))
    surface = _surface(page)
    events = await _collect(
        surface.stream_generate(model="gemini-3.8-flash", json_body='{"contents":[]}')
    )
    assert ("status", 200) in events
    assert ("body", "OK") in events
    assert events[-1] == ("done", None)
    assert ("response_id", "SWrUao2MIJ6W9MoPm6fosQE") in events
    usage_events = [value for name, value in events if name == "usage"]
    assert usage_events and isinstance(usage_events[0], dict)
    assert usage_events[0]["total_tokens"] == 153
    assert surface.state.in_flight_count == 0


async def test_stream_generate_explicit_signer_transport(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AISTUDIO_BUILD_TRANSPORT", "signer")
    page = FakePage(events_for=_holder_events(_holder(FRAME0_CHUNK, depth=3)))
    surface = _surface(page)
    surface.state.native_ready = True
    surface.state.signer_ready = True
    native_calls = {"count": 0}

    async def fail_native(
        request_id: str, model: str, json_body: str
    ) -> asyncio.Queue[dict[str, object]] | None:
        native_calls["count"] += 1
        return None

    monkeypatch.setattr(surface, "_dispatch_native", fail_native)
    events = await _collect(surface.stream_generate(model="m", json_body="{}"))
    assert native_calls["count"] == 0
    assert ("body", "OK") in events


async def test_stream_generate_native_transport_when_signer_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    page = FakePage()
    surface = _surface(page)
    surface.state.native_ready = True
    surface.state.signer_ready = False

    sse = "data: " + json.dumps(FRAME0_CHUNK, ensure_ascii=False) + "\n\n"

    async def fake_dispatch(
        request_id: str, model: str, json_body: str
    ) -> asyncio.Queue[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        queue.put_nowait({"event_type": "response_headers", "status": 200})
        queue.put_nowait({"event_type": "chunk", "data": sse[:20]})
        queue.put_nowait({"event_type": "chunk", "data": sse[20:]})
        queue.put_nowait({"event_type": "stream_close"})
        return queue

    monkeypatch.setattr(surface, "_dispatch_native", fake_dispatch)
    events = await _collect(
        surface.stream_generate(model="gemini-3.8-flash", json_body='{"contents":[]}')
    )
    assert ("status", 200) in events
    assert ("body", "OK") in events
    assert events[-1] == ("done", None)


async def test_stream_generate_native_error_falls_back_to_signer(
    monkeypatch: pytest.MonkeyPatch,
):
    page = FakePage(events_for=_holder_events(_holder(FRAME0_CHUNK, depth=3)))
    surface = _surface(page)
    surface.state.native_ready = True
    surface.state.signer_ready = True

    async def fake_dispatch(
        request_id: str, model: str, json_body: str
    ) -> asyncio.Queue[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        queue.put_nowait(
            {"event_type": "error", "message": "native 9998 fetch rejected"}
        )
        return queue

    monkeypatch.setattr(surface, "_dispatch_native", fake_dispatch)
    events = await _collect(surface.stream_generate(model="m", json_body="{}"))
    assert ("body", "OK") in events
    assert events[-1] == ("done", None)


async def test_stream_generate_native_error_without_signer_raises(
    monkeypatch: pytest.MonkeyPatch,
):
    page = FakePage()
    surface = _surface(page)
    surface.state.native_ready = True
    surface.state.signer_ready = False

    async def fake_dispatch(
        request_id: str, model: str, json_body: str
    ) -> asyncio.Queue[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        queue.put_nowait({"event_type": "error", "message": "native exploded"})
        return queue

    monkeypatch.setattr(surface, "_dispatch_native", fake_dispatch)
    with pytest.raises(bs.BuildSurfaceError) as excinfo:
        await _collect(surface.stream_generate(model="m", json_body="{}"))
    assert excinfo.value.code == "NATIVE_ERROR"
    assert "native exploded" in str(excinfo.value)


async def test_stream_generate_finish_reason_and_signature():
    frame_text = json.dumps(
        [[json.dumps(FRAME0_CHUNK), json.dumps(FRAME1_CHUNK)]], ensure_ascii=False
    )
    page = FakePage(events_for=_holder_events(frame_text))
    surface = _surface(page)
    events = await _collect(surface.stream_generate(model="m", json_body="{}"))
    assert ("body", "OK") in events
    assert ("thought_signature", FRAME1_SIGNATURE) in events
    assert ("finish_reason", 1) in events


async def test_stream_generate_error_event_raises():
    def factory(rid: str) -> list[dict[str, object]]:
        return [
            {"rid": rid, "type": "status", "status": 200},
            {"rid": rid, "type": "error", "message": "signer exploded"},
        ]

    surface = _surface(FakePage(events_for=factory))
    with pytest.raises(bs.BuildSurfaceError) as excinfo:
        await _collect(surface.stream_generate(model="m", json_body="{}"))
    assert excinfo.value.code == "SIGNER_ERROR"
    assert "signer exploded" in str(excinfo.value)


async def test_stream_generate_timeout_raises():
    surface = _surface(FakePage(events_for=lambda rid: []))
    with pytest.raises(bs.BuildSurfaceError) as excinfo:
        await _collect(
            surface.stream_generate(model="m", json_body="{}", timeout_s=0.05)
        )
    assert excinfo.value.code == "TIMEOUT"


async def test_stream_generate_page_closed_raises():
    surface = _surface(FakePage(events_for=lambda rid: [], close_on_dispatch=True))
    with pytest.raises(bs.BuildSurfaceError) as excinfo:
        await _collect(surface.stream_generate(model="m", json_body="{}"))
    assert excinfo.value.code == "PAGE_CLOSED"


async def test_stream_generate_no_convertible_frames_raises():
    frame_text = json.dumps([["definitely not a chunk"]])
    surface = _surface(FakePage(events_for=_holder_events(frame_text)))
    with pytest.raises(bs.BuildSurfaceError) as excinfo:
        await _collect(surface.stream_generate(model="m", json_body="{}"))
    assert excinfo.value.code == "NO_CONVERTIBLE_FRAMES"


async def test_stream_generate_keeps_valid_frames_alongside_dropped():
    frame_text = json.dumps([["junk", json.dumps(FRAME0_CHUNK)]])
    surface = _surface(FakePage(events_for=_holder_events(frame_text)))
    events = await _collect(surface.stream_generate(model="m", json_body="{}"))
    assert ("body", "OK") in events
    assert events[-1] == ("done", None)


async def test_stream_generate_requires_ready_surface():
    page = FakePage()
    surface = _build_surface(page)
    with pytest.raises(bs.BuildSurfaceError) as excinfo:
        await _collect(surface.stream_generate(model="m", json_body="{}"))
    assert excinfo.value.code == "PAGE_NOT_READY"

    surface.state.page = cast(CDPPage, page)  # page present but not ready
    with pytest.raises(bs.BuildSurfaceError) as excinfo2:
        await _collect(surface.stream_generate(model="m", json_body="{}"))
    assert excinfo2.value.code == "SURFACE_NOT_READY"


async def test_stream_generate_rid_isolation():
    counter = {"n": 0}

    def factory(rid: str) -> list[dict[str, object]]:
        counter["n"] += 1
        text = "OK" if counter["n"] == 1 else "SECOND"
        frame_text = json.dumps(
            [[json.dumps(_text_chunk(text, f"rid-{counter['n']}"))]]
        )
        return [
            {"rid": rid, "type": "status", "status": 200},
            {"rid": rid, "type": "chunk", "text": frame_text},
            {"rid": rid, "type": "done"},
        ]

    surface = _surface(FakePage(events_for=factory))

    async def run(json_body: str) -> list[tuple[str, object]]:
        return await _collect(surface.stream_generate(model="m", json_body=json_body))

    first, second = await asyncio.gather(run("a"), run("b"))
    first_bodies = [value for name, value in first if name == "body"]
    second_bodies = [value for name, value in second if name == "body"]
    assert len(first_bodies) == 1
    assert len(second_bodies) == 1
    assert sorted(cast(list[str], first_bodies + second_bodies)) == ["OK", "SECOND"]
    assert surface.state.in_flight_count == 0


async def test_abort_stream_by_rid():
    surface = _surface(FakePage())
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    surface.state.task_queues["deadbeef"] = queue
    assert await surface.abort_stream("deadbeef", "client_disconnected") is True
    assert await surface.abort_stream("missing") is False
    event = queue.get_nowait()
    assert event["type"] == "error"
    assert event["message"] == "client_disconnected"
