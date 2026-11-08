"""WAA snapshot warmup and prompt-tail regression tests (offline)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.browser.scripts import (
    SNAPSHOT_GENERATE_JS,
    SNAPSHOT_PROMPT_TAIL_CHARS,
    build_snapshot_args,
)
from aistudio_api.infrastructure.gateway.capture import (
    CapturedRequest,
    RequestCaptureService,
)
from aistudio_api.infrastructure.gateway.pooled_session import PooledSession
from aistudio_api.infrastructure.gateway.session import (
    DEFAULT_BOOTSTRAP_TEMPLATE,
    BrowserSession,
)
from aistudio_api.infrastructure.gateway.session_state import (
    SNAPSHOT_WARMUP_IDLE_S,
    SNAPSHOT_WARMUP_PROMPT_BYTES,
    SessionContext,
    should_warm_snapshot,
)
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent, AistudioPart

VALID_SNAPSHOT = "!" + "x" * 1520
ZERO_HASH = "0" * 64


def _contents(text: str) -> list[AistudioContent]:
    return [AistudioContent(role="user", parts=[AistudioPart(text=text)])]


def _recording_page(records: list[dict[str, object]]) -> MagicMock:
    page = MagicMock(spec=CDPPage)
    page.is_closed = MagicMock(return_value=False)
    page.wait_for_timeout = AsyncMock()

    async def fake_evaluate(expr, args=None, *a, **kw):
        del expr, a, kw
        payload = dict(args or {})
        records.append(payload)
        return {
            "snapshot": VALID_SNAPSHOT,
            "warmup_used": bool(payload.get("warmup")),
            "quality_ok": True,
        }

    page.evaluate = AsyncMock(side_effect=fake_evaluate)
    return page


def _browser_session(page: MagicMock) -> BrowserSession:
    session = BrowserSession(port=9222)
    session.set_ready_context(
        SessionContext(
            page=page,
            snap_key="snap",
            auth_user="0",
            auth_file=None,
            base_template=dict(DEFAULT_BOOTSTRAP_TEMPLATE),
        )
    )
    return session


def _pooled_session(page: MagicMock) -> PooledSession:
    return PooledSession(
        context=SessionContext(
            page=page,
            snap_key="snap",
            auth_user="0",
            auth_file=None,
            base_template={"url": "https://example.invalid"},
        ),
        transport=MagicMock(),
        realm_id="realm-1",
    )


def test_build_snapshot_args_tail_truncates_prompt() -> None:
    prompt = "P" * 200_000 + "TAIL-END"
    args = build_snapshot_args(prompt=prompt, content_hash="hash-1", warmup=True)

    assert args["prompt"] == prompt[-SNAPSHOT_PROMPT_TAIL_CHARS:]
    assert len(str(args["prompt"])) <= 256
    assert args["hash"] == "hash-1"
    assert args["warmup"] is True
    assert build_snapshot_args(prompt="", content_hash="hash-2")["prompt"] == ""
    assert build_snapshot_args(prompt="short", content_hash="h")["warmup"] is False


def test_shared_warmup_helper_thresholds() -> None:
    now = 1000.0
    assert should_warm_snapshot(last_snapshot_at=None, now=now, prompt_bytes=0)
    assert should_warm_snapshot(
        last_snapshot_at=now - (SNAPSHOT_WARMUP_IDLE_S + 1),
        now=now,
        prompt_bytes=0,
    )
    assert not should_warm_snapshot(
        last_snapshot_at=now - SNAPSHOT_WARMUP_IDLE_S,
        now=now,
        prompt_bytes=0,
    )
    assert not should_warm_snapshot(
        last_snapshot_at=now,
        now=now,
        prompt_bytes=SNAPSHOT_WARMUP_PROMPT_BYTES - 1,
        payload_bytes=SNAPSHOT_WARMUP_PROMPT_BYTES - 1,
    )
    assert should_warm_snapshot(
        last_snapshot_at=now,
        now=now,
        prompt_bytes=SNAPSHOT_WARMUP_PROMPT_BYTES,
    )
    assert should_warm_snapshot(
        last_snapshot_at=now,
        now=now,
        prompt_bytes=1,
        payload_bytes=SNAPSHOT_WARMUP_PROMPT_BYTES,
    )
    assert should_warm_snapshot(
        last_snapshot_at=now, now=now, prompt_bytes=0, force=True
    )


@pytest.mark.asyncio
async def test_200kb_request_hash_uses_full_prompt_but_forwards_tail() -> None:
    records: list[dict[str, object]] = []
    page = _recording_page(records)
    session = _browser_session(page)
    contents = _contents("P" * 200_000 + "TAIL-END")
    full_prompt = " ".join(
        str(part.text)
        for content in contents
        for part in content.parts
        if part.text is not None
    )

    await session.generate_snapshot(contents)

    args = records[-1]
    assert args["hash"] == sha256(full_prompt.encode("utf-8")).hexdigest()
    assert args["prompt"] == full_prompt[-SNAPSHOT_PROMPT_TAIL_CHARS:]
    assert len(str(args["prompt"])) <= SNAPSHOT_PROMPT_TAIL_CHARS


@pytest.mark.asyncio
async def test_large_tool_history_payload_triggers_warmup() -> None:
    tools = [
        [
            "functionDeclarations",
            [{"name": "history", "description": "x" * 200_000}],
        ]
    ]
    capture_session = _CaptureSession(last_snapshot_at=100.0, warmup=False)
    service = RequestCaptureService(capture_session)  # type: ignore[arg-type]

    captured = await service.capture(prompt="small", tools=tools)

    assert captured is not None
    payload_size = capture_session.payload_sizes[-1]
    assert payload_size is not None
    assert 150 * 1024 <= payload_size <= 260 * 1024

    records: list[dict[str, object]] = []
    page = _recording_page(records)
    session = _browser_session(page)
    session._last_snapshot_at = time.monotonic()

    await session.generate_snapshot(_contents("small"), payload_size=payload_size)

    assert records[-1]["warmup"] is True


@pytest.mark.asyncio
async def test_first_snapshot_warms_up_then_next_call_is_normal() -> None:
    records: list[dict[str, object]] = []
    page = _recording_page(records)
    session = _browser_session(page)

    assert await session.generate_snapshot(_contents("hello")) == VALID_SNAPSHOT
    assert records[-1]["warmup"] is True
    assert session.last_snapshot_warmup is True
    assert session._last_snapshot_at is not None

    assert await session.generate_snapshot(_contents("hello")) == VALID_SNAPSHOT
    assert records[-1]["warmup"] is False
    assert session.last_snapshot_warmup is False


@pytest.mark.asyncio
async def test_long_idle_and_large_prompt_force_next_warmup() -> None:
    records: list[dict[str, object]] = []
    page = _recording_page(records)
    session = _browser_session(page)

    session._last_snapshot_at = time.monotonic() - (SNAPSHOT_WARMUP_IDLE_S + 1)
    await session.generate_snapshot(_contents("idle"))
    assert records[-1]["warmup"] is True

    session._last_snapshot_at = time.monotonic()
    await session.generate_snapshot(_contents("normal"))
    assert records[-1]["warmup"] is False

    session._last_snapshot_at = time.monotonic()
    big_prompt = "x" * (SNAPSHOT_WARMUP_PROMPT_BYTES + 1)
    await session.generate_snapshot(_contents(big_prompt))
    assert records[-1]["warmup"] is True


@pytest.mark.asyncio
async def test_refresh_auth_state_forces_same_account_snapshot_warmup() -> None:
    records: list[dict[str, object]] = []
    page = _recording_page(records)

    async def fake_get_cookies():
        page._cached_cookies = {"SAPISID": "fresh"}
        return [{"name": "SAPISID", "value": "fresh"}]

    page.get_cookies = AsyncMock(side_effect=fake_get_cookies)
    session = _browser_session(page)
    session._last_snapshot_at = time.monotonic()

    assert await session.refresh_auth_state() is True
    assert session._force_snapshot_warmup is True

    await session.generate_snapshot(_contents("retry"))
    assert records[-1]["warmup"] is True
    assert session._force_snapshot_warmup is False
    assert session.last_snapshot_warmup is True

    await session.generate_snapshot(_contents("retry"))
    assert records[-1]["warmup"] is False


@pytest.mark.asyncio
async def test_pooled_session_refresh_forces_warmup_and_tracks_timestamp() -> None:
    records: list[dict[str, object]] = []
    page = _recording_page(records)

    async def fake_get_cookies():
        page._cached_cookies = {"SAPISID": "fresh"}
        return [{"name": "SAPISID", "value": "fresh"}]

    page.get_cookies = AsyncMock(side_effect=fake_get_cookies)
    session = PooledSession(
        context=SessionContext(
            page=page,
            snap_key="snap",
            auth_user="0",
            auth_file=None,
            base_template={"url": "https://example.invalid"},
        ),
        transport=MagicMock(),
        realm_id="realm-1",
    )
    session._last_snapshot_at = time.monotonic()

    assert await session.refresh_auth_state() is True
    assert session._force_snapshot_warmup is True

    await session.generate_snapshot(_contents("retry"))
    assert records[-1]["warmup"] is True
    assert session._force_snapshot_warmup is False
    assert session._last_snapshot_at is not None
    assert session.last_snapshot_warmup is True

    await session.generate_snapshot(_contents("retry"))
    assert records[-1]["warmup"] is False


@pytest.mark.asyncio
async def test_low_quality_snapshot_fails_fast_in_browser_session() -> None:
    page = _recording_page([])
    page.evaluate = AsyncMock(side_effect=RuntimeError("low_quality_snapshot"))
    session = _browser_session(page)

    with pytest.raises(RuntimeError, match="low_quality_snapshot"):
        await session.generate_snapshot(_contents("x"))

    assert page.evaluate.await_count == 1
    assert page.wait_for_timeout.await_count == 0


@pytest.mark.asyncio
async def test_low_quality_snapshot_fails_fast_in_pooled_session() -> None:
    page = _recording_page([])
    page.evaluate = AsyncMock(side_effect=RuntimeError("low_quality_snapshot"))
    session = _pooled_session(page)

    with pytest.raises(RuntimeError, match="low_quality_snapshot"):
        await session.generate_snapshot(_contents("x"))

    assert page.evaluate.await_count == 1
    assert page.wait_for_timeout.await_count == 0


@pytest.mark.asyncio
async def test_forced_warmup_survives_failures_and_clears_on_success() -> None:
    page = _recording_page([])
    page.evaluate = AsyncMock(side_effect=RuntimeError("transient"))
    session = _browser_session(page)
    stamp = time.monotonic()
    session._last_snapshot_at = stamp
    session._force_snapshot_warmup = True

    with pytest.raises(RuntimeError, match="Snapshot generation failed"):
        await session.generate_snapshot(_contents("x"))

    assert page.evaluate.await_count == 5
    assert session._force_snapshot_warmup is True
    assert session._last_snapshot_at == stamp

    page.evaluate = AsyncMock(
        return_value={
            "snapshot": VALID_SNAPSHOT,
            "warmup_used": True,
            "quality_ok": True,
        }
    )
    await session.generate_snapshot(_contents("x"))

    assert session._force_snapshot_warmup is False
    assert session._last_snapshot_at is not None
    assert session._last_snapshot_at != stamp


@pytest.mark.asyncio
async def test_pooled_forced_warmup_survives_failures_and_clears_on_success() -> None:
    page = _recording_page([])
    page.evaluate = AsyncMock(side_effect=RuntimeError("transient"))
    session = _pooled_session(page)
    stamp = time.monotonic()
    session._last_snapshot_at = stamp
    session._force_snapshot_warmup = True

    with pytest.raises(RuntimeError, match="PooledSession snapshot generation failed"):
        await session.generate_snapshot(_contents("x"))

    assert page.evaluate.await_count == 5
    assert session._force_snapshot_warmup is True
    assert session._last_snapshot_at == stamp

    page.evaluate = AsyncMock(
        return_value={
            "snapshot": VALID_SNAPSHOT,
            "warmup_used": True,
            "quality_ok": True,
        }
    )
    await session.generate_snapshot(_contents("x"))

    assert session._force_snapshot_warmup is False
    assert session._last_snapshot_at is not None
    assert session._last_snapshot_at != stamp


def test_set_ready_context_resets_snapshot_timestamp() -> None:
    page = _recording_page([])
    session = BrowserSession(port=9222)
    session._last_snapshot_at = 123.0

    session.set_ready_context(
        SessionContext(
            page=page,
            snap_key="snap",
            auth_user="0",
            auth_file=None,
            base_template=dict(DEFAULT_BOOTSTRAP_TEMPLATE),
        )
    )

    assert session._last_snapshot_at is None


@pytest.mark.asyncio
async def test_transition_to_account_resets_timestamp_and_invalidates_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = BrowserSession(port=9222)
    session._last_snapshot_at = time.monotonic()
    capture = MagicMock()
    session.attach_capture_service(capture)

    fake_page = MagicMock(spec=CDPPage)
    fake_page.is_closed = MagicMock(return_value=False)
    fake_page.target_id = "target-1"
    fake_cdp = MagicMock()
    fake_cdp.connect_page = AsyncMock(return_value=fake_page)
    fake_cdp.create_new_page = AsyncMock(return_value=fake_page)
    fake_cdp.close_target = AsyncMock()

    async def fake_ensure_browser_cdp(*, connect_page: bool = True):
        session._cdp_client = fake_cdp
        return fake_page if connect_page else None

    monkeypatch.setattr(session, "_ensure_browser_cdp", fake_ensure_browser_cdp)
    monkeypatch.setattr(session, "_stop_browser", AsyncMock())
    monkeypatch.setattr(
        "aistudio_api.infrastructure.gateway.session.load_cookies_to_page",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "aistudio_api.infrastructure.gateway.session.navigate_to_aistudio",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "aistudio_api.infrastructure.gateway.session.bootstrap_botguard_and_template",
        AsyncMock(return_value=("snap", dict(DEFAULT_BOOTSTRAP_TEMPLATE))),
    )
    monkeypatch.setattr(
        "aistudio_api.infrastructure.gateway.session.resolve_fingerprint_spoof_enabled",
        lambda: False,
    )

    await session.transition_to_account(None, force_refresh=True)

    assert session._last_snapshot_at is None
    capture.clear_templates.assert_called_once_with(invalidate_base=True)


@pytest.mark.asyncio
async def test_browser_session_close_invalidates_capture_templates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = BrowserSession(port=9222)
    capture = MagicMock()
    session.attach_capture_service(capture)
    monkeypatch.setattr(session, "_stop_browser", AsyncMock())

    await session.close()

    capture.clear_templates.assert_called_once_with(invalidate_base=True)


@pytest.mark.asyncio
async def test_pooled_session_close_invalidates_capture_templates() -> None:
    page = _recording_page([])
    session = _pooled_session(page)
    capture = MagicMock()
    session._capture_service = capture

    await session.close()

    capture.clear_templates.assert_called_once_with(invalidate_base=True)
    page.close.assert_awaited_once()


class _CaptureSession:
    def __init__(self, *, last_snapshot_at: float | None, warmup: bool) -> None:
        self._last_snapshot_at = last_snapshot_at
        self.last_snapshot_warmup = warmup
        self.next_snapshot_at = 112.5
        self.payload_sizes: list[int | None] = []

    async def ensure_ready(self) -> SimpleNamespace:
        return SimpleNamespace(context_id=1)

    async def capture_template(self, model: str) -> dict[str, object]:
        return {
            "url": "https://example.invalid/generate",
            "headers": {"content-type": "application/json"},
            "body": (
                '["models/gemini-3.7-flash",[[[[null,"hi"]],"user"]],'
                'null,[null,null,null,128],"!orig"]'
            ),
            "model": model,
        }

    async def generate_snapshot(
        self, contents: list[AistudioContent], *, payload_size: int | None = None
    ) -> str:
        del contents
        self.payload_sizes.append(payload_size)
        self._last_snapshot_at = self.next_snapshot_at
        return VALID_SNAPSHOT


@pytest.mark.asyncio
async def test_capture_log_includes_prefix_warmup_and_interval(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = _CaptureSession(last_snapshot_at=100.0, warmup=True)
    service = RequestCaptureService(session)  # type: ignore[arg-type]
    caplog.set_level(logging.INFO)

    captured = await service.capture(prompt="SECRET_PROMPT_SENTINEL")

    assert captured is not None
    messages = [
        record.getMessage()
        for record in caplog.records
        if "捕获请求模板成功" in record.getMessage()
    ]
    assert len(messages) == 1
    message = messages[0]
    assert "叹号前缀=True" in message
    assert "预热=True" in message
    assert "距上次快照=12.50s" in message
    assert "SECRET_PROMPT_SENTINEL" not in message


@pytest.mark.asyncio
async def test_capture_log_first_snapshot_reports_first(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = _CaptureSession(last_snapshot_at=None, warmup=False)
    session.next_snapshot_at = 5.0
    service = RequestCaptureService(session)  # type: ignore[arg-type]
    caplog.set_level(logging.INFO)

    await service.capture(prompt="first")

    message = next(
        record.getMessage()
        for record in caplog.records
        if "捕获请求模板成功" in record.getMessage()
    )
    assert "距上次快照=首次" in message


def test_clear_templates_invalidate_base_resets_context_marker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = RequestCaptureService(MagicMock())  # type: ignore[arg-type]
    service._active_ctx_id = 7
    caplog.set_level(logging.INFO)

    assert service.clear_templates(invalidate_base=True) is False
    assert service._active_ctx_id is None
    assert not [r for r in caplog.records if "已清空请求模板缓存" in r.getMessage()]

    service._active_ctx_id = 9
    service._templates["gemini-3.7-flash"] = CapturedRequest(
        url="https://example.invalid", headers={}, body="[]"
    )
    assert service.clear_templates(invalidate_base=True) is True
    assert service._active_ctx_id is None
    assert service.clear_templates(invalidate_base=True) is False
    assert (
        len([r for r in caplog.records if "已清空请求模板缓存" in r.getMessage()]) == 1
    )

    # Request-time clears (403/429) must not invalidate the tenant base template.
    service._active_ctx_id = 11
    service._templates["gemini-3.7-flash"] = CapturedRequest(
        url="https://example.invalid", headers={}, body="[]"
    )
    assert service.clear_templates() is True
    assert service._active_ctx_id == 11


_JS_HARNESS = r"""
const realSetTimeout = globalThis.setTimeout;
const scriptText = __SCRIPT__;
const fn = new Function('return (' + scriptText + ')')();

const writes = [];
const events = [];
const delays = [];
const rafCalls = [];
const snapCalls = [];
const order = [];
const ioOrder = [];
let fastTimeouts = false;

class FakeEvent {
  constructor(type, init) { this.type = type; this.init = init || {}; }
}
class FakeInputEvent extends FakeEvent {}
class FakeTextArea {
  constructor() { this._value = ''; }
  checkVisibility() { return true; }
  dispatchEvent(ev) {
    events.push({ type: ev.type, data: ev.init ? ev.init.data : null });
    return true;
  }
}
Object.defineProperty(FakeTextArea.prototype, 'value', {
  configurable: true,
  get() { return this._value; },
  set(v) {
    this._value = v;
    writes.push(v);
    ioOrder.push({ kind: 'write', value: v });
  },
});
const box = new FakeTextArea();

globalThis.window = globalThis;
window.HTMLTextAreaElement = FakeTextArea;
window.Event = FakeEvent;
window.InputEvent = FakeInputEvent;
window.document = {
  querySelectorAll: (sel) => (String(sel).includes('textarea') ? [box] : []),
};
window.requestAnimationFrame = (cb) => {
  rafCalls.push(1);
  return realSetTimeout(() => cb(0), 0);
};
globalThis.setTimeout = (cb, ms) => {
  delays.push(ms);
  const effective = fastTimeouts && ms >= 20000 ? 0 : ms;
  return realSetTimeout(cb, effective);
};

const ZERO = '0'.repeat(64);
const valid = (tag) => ('!' + tag).padEnd(1520, 'x');
function hasLoneSurrogate(s) {
  for (let i = 0; i < s.length; i += 1) {
    const c = s.charCodeAt(i);
    if (c >= 0xD800 && c <= 0xDBFF) {
      const n = s.charCodeAt(i + 1);
      if (!(n >= 0xDC00 && n <= 0xDFFF)) return true;
      i += 1;
    } else if (c >= 0xDC00 && c <= 0xDFFF) {
      return true;
    }
  }
  return false;
}

function resetState() {
  writes.length = 0;
  events.length = 0;
  delays.length = 0;
  rafCalls.length = 0;
  snapCalls.length = 0;
  order.length = 0;
  ioOrder.length = 0;
  fastTimeouts = false;
  window.__AISTUDIO__ = { service: {}, snapKey: 'snap' };
}

const scenarios = {};

(async () => {
  resetState();
  const prompt = 'A'.repeat(200000) + 'TAIL-END';
  window.default_MakerSuite = {
    snap: async () => valid('ok'),
  };
  const truncateResult = await fn({ prompt, hash: 'realhash', warmup: false });
  scenarios.truncate = {
    result: truncateResult,
    write_lengths: writes.map((v) => v.length),
    first_write: writes[0],
    expected_tail: prompt.slice(-256),
    cleared: writes[writes.length - 1] === '',
    delays: [...delays],
    raf_calls: rafCalls.length,
  };

  resetState();
  window.default_MakerSuite = {
    snap: async (service, hash) => { snapCalls.push(hash); return valid('ok'); },
  };
  const warmupResult = await fn({ prompt: 'hello', hash: 'realhash', warmup: true });
  scenarios.warmup_true = {
    result: warmupResult,
    calls: [...snapCalls],
    delays: [...delays],
  };

  resetState();
  let realCalls = 0;
  window.default_MakerSuite = {
    snap: async (service, hash) => {
      snapCalls.push(hash);
      if (hash === ZERO) return 'warmup-discarded';
      realCalls += 1;
      if (realCalls === 1) return '!' + 'x'.repeat(1511);
      return valid('second');
    },
  };
  const lowResult = await fn({ prompt: 'hello', hash: 'realhash', warmup: false });
  scenarios.low_then_ok = {
    result: lowResult,
    calls: [...snapCalls],
  };

  resetState();
  window.default_MakerSuite = {
    snap: async (service, hash) => {
      snapCalls.push(hash);
      if (hash === ZERO) return 'warmup-discarded';
      return '!' + 'x'.repeat(1511);
    },
  };
  try {
    await fn({ prompt: 'hello', hash: 'realhash', warmup: false });
    scenarios.invalid_after_retry = { threw: false };
  } catch (e) {
    scenarios.invalid_after_retry = {
      threw: true,
      message: e.message,
      calls: [...snapCalls],
    };
  }

  resetState();
  const emojiPrompt = 'A'.repeat(200000) + '😀😀😀';
  window.default_MakerSuite = {
    snap: async () => valid('emoji'),
  };
  await fn({ prompt: emojiPrompt, hash: 'realhash', warmup: false });
  const emojiFirst = writes[0];
  scenarios.emoji_tail = {
    first_write: emojiFirst,
    expected_tail: Array.from(emojiPrompt).slice(-256).join(''),
    codepoints: Array.from(emojiFirst).length,
    utf16_length: emojiFirst.length,
    lone_surrogate: hasLoneSurrogate(emojiFirst),
    ends_with_emoji: emojiFirst.endsWith('😀'),
  };

  resetState();
  window.default_MakerSuite = {
    snap: async (service, hash) => {
      snapCalls.push(hash);
      if (hash === ZERO) throw new Error('warmup_failed');
      return valid('real');
    },
  };
  const warmupThrowResult = await fn({
    prompt: 'hello',
    hash: 'realhash',
    warmup: true,
  });
  scenarios.warmup_throw = {
    result: warmupThrowResult,
    calls: [...snapCalls],
  };

  resetState();
  window.default_MakerSuite = {
    snap: async () => {
      snapCalls.push('low');
      return '!short';
    },
  };
  let lowClearedThrew = false;
  let lowClearedMessage = '';
  try {
    await fn({ prompt: 'hello', hash: 'realhash', warmup: false });
  } catch (e) {
    lowClearedThrew = true;
    lowClearedMessage = e.message;
  }
  scenarios.low_quality_finally = {
    threw: lowClearedThrew,
    message: lowClearedMessage,
    writes: [...writes],
  };

  resetState();
  window.default_MakerSuite = {
    snap: async (service, hash) => {
      snapCalls.push(hash);
      return '!' + 'x'.repeat(1518);
    },
  };
  let boundary1519Threw = false;
  try {
    await fn({ prompt: 'hello', hash: 'realhash', warmup: true });
  } catch (e) {
    boundary1519Threw = e.message.includes('low_quality_snapshot');
  }
  scenarios.boundary_1519 = {
    threw: boundary1519Threw,
    calls: [...snapCalls],
  };

  resetState();
  window.default_MakerSuite = {
    snap: async (service, hash) => {
      snapCalls.push(hash);
      return '!' + 'x'.repeat(1519);
    },
  };
  const boundary1520 = await fn({
    prompt: 'hello',
    hash: 'realhash',
    warmup: false,
  });
  scenarios.boundary_1520 = {
    result: boundary1520,
    calls: [...snapCalls],
    length: boundary1520.snapshot.length,
  };

  resetState();
  const savedRaf = window.requestAnimationFrame;
  delete window.requestAnimationFrame;
  window.default_MakerSuite = {
    snap: async () => valid('noraf'),
  };
  await fn({ prompt: 'hello', hash: 'realhash', warmup: false });
  window.requestAnimationFrame = savedRaf;
  scenarios.no_raf = {
    delays: [...delays],
    raf_calls: rafCalls.length,
  };

  resetState();
  fastTimeouts = true;
  window.default_MakerSuite = {
    snap: (service, hash) => {
      snapCalls.push(hash);
      if (hash === 'slow') return new Promise(() => {});
      return valid('fast');
    },
  };
  let timeoutMessage = null;
  try {
    await fn({ prompt: 'a', hash: 'slow', warmup: false });
  } catch (e) {
    timeoutMessage = e.message;
  }
  const recoveredResult = await fn({
    prompt: 'b',
    hash: 'fast',
    warmup: false,
  });
  fastTimeouts = false;
  scenarios.timeout_recover = {
    timeout_message: timeoutMessage,
    recovered: recoveredResult,
    calls: [...snapCalls],
  };

  resetState();
  let queued = 0;
  window.default_MakerSuite = {
    snap: async () => {
      const id = ++queued;
      order.push('start' + id);
      ioOrder.push({ kind: 'start', id });
      await new Promise((resolve) => realSetTimeout(resolve, 5));
      order.push('end' + id);
      ioOrder.push({ kind: 'end', id });
      return valid('queued' + id);
    },
  };
  await Promise.all([
    fn({ prompt: 'one', hash: 'hash-one', warmup: false }),
    fn({ prompt: 'two', hash: 'hash-two', warmup: false }),
  ]);
  scenarios.queue = { order, io_order: ioOrder, delays: [...delays] };

  console.log(JSON.stringify(scenarios));
})().catch((e) => {
  console.error(e && e.stack ? e.stack : String(e));
  process.exit(1);
});
"""


def _run_js_harness() -> dict[str, Any]:
    runtime = shutil.which("bun") or shutil.which("node")
    if not runtime:
        pytest.skip("Neither bun nor node found in PATH; skipping JS behavior check.")

    harness = _JS_HARNESS.replace("__SCRIPT__", json.dumps(SNAPSHOT_GENERATE_JS))
    with tempfile.TemporaryDirectory(prefix="aistudio-snap-js-") as tmp_dir:
        path = Path(tmp_dir) / "snapshot_harness.mjs"
        path.write_text(harness, encoding="utf-8")
        result = subprocess.run(
            [runtime, str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    assert result.returncode == 0, result.stderr
    payloads = [line for line in result.stdout.splitlines() if line.startswith("{")]
    assert payloads, f"no JSON payload in stdout:\n{result.stdout}\n{result.stderr}"
    data: Any = json.loads(payloads[-1])
    assert isinstance(data, dict)
    return data


@pytest.fixture(scope="module")
def js_results() -> dict[str, Any]:
    return _run_js_harness()


def test_js_never_writes_full_prompt_and_waits_for_input(
    js_results: dict[str, Any],
) -> None:
    data = js_results["truncate"]
    assert isinstance(data, dict)
    assert data["first_write"] == data["expected_tail"]
    assert all(int(length) <= 256 for length in data["write_lengths"])
    assert data["cleared"] is True
    assert data["raf_calls"] == 1
    assert 40 in data["delays"]
    assert data["result"]["snapshot"].startswith("!")
    assert data["result"]["warmup_used"] is False


def test_js_warmup_true_uses_placeholder_then_real_hash(
    js_results: dict[str, Any],
) -> None:
    data = js_results["warmup_true"]
    assert isinstance(data, dict)
    assert data["calls"] == [ZERO_HASH, "realhash"]
    assert data["result"]["warmup_used"] is True
    assert data["result"]["quality_ok"] is True
    assert 40 in data["delays"]


def test_js_low_quality_retries_after_warmup(
    js_results: dict[str, Any],
) -> None:
    data = js_results["low_then_ok"]
    assert isinstance(data, dict)
    assert len(data["calls"]) == 3
    assert data["calls"][0] == "realhash"
    assert data["calls"][1] == ZERO_HASH
    assert data["calls"][2] == "realhash"
    assert data["result"]["warmup_used"] is True
    assert len(data["result"]["snapshot"]) >= 1520


def test_js_invalid_snapshot_after_retry_throws(
    js_results: dict[str, Any],
) -> None:
    data = js_results["invalid_after_retry"]
    assert isinstance(data, dict)
    assert data["threw"] is True
    assert "low_quality_snapshot" in data["message"]
    assert len(data["calls"]) == 3


def test_js_emoji_tail_is_codepoint_safe(js_results: dict[str, Any]) -> None:
    data = js_results["emoji_tail"]
    assert isinstance(data, dict)
    assert data["first_write"] == data["expected_tail"]
    assert data["codepoints"] == 256
    assert data["lone_surrogate"] is False
    assert data["ends_with_emoji"] is True
    assert data["utf16_length"] > 256


def test_js_warmup_failure_is_swallowed_and_real_call_proceeds(
    js_results: dict[str, Any],
) -> None:
    data = js_results["warmup_throw"]
    assert isinstance(data, dict)
    assert data["calls"] == [ZERO_HASH, "realhash"]
    assert data["result"]["snapshot"].startswith("!")


def test_js_low_quality_error_still_clears_input(
    js_results: dict[str, Any],
) -> None:
    data = js_results["low_quality_finally"]
    assert isinstance(data, dict)
    assert data["threw"] is True
    assert "low_quality_snapshot" in data["message"]
    assert data["writes"][-1] == ""


def test_js_snapshot_length_boundary(js_results: dict[str, Any]) -> None:
    assert js_results["boundary_1519"]["threw"] is True
    data = js_results["boundary_1520"]
    assert isinstance(data, dict)
    assert data["length"] == 1520
    assert data["calls"] == ["realhash"]


def test_js_no_raf_falls_back_to_40ms(js_results: dict[str, Any]) -> None:
    data = js_results["no_raf"]
    assert isinstance(data, dict)
    assert data["raf_calls"] == 0
    assert 40 in data["delays"]


def test_js_snapshot_timeout_recovers_queue(js_results: dict[str, Any]) -> None:
    data = js_results["timeout_recover"]
    assert isinstance(data, dict)
    assert data["timeout_message"] == "snapshot_timeout"
    assert data["calls"] == ["slow", "fast"]
    assert data["recovered"]["snapshot"].startswith("!")


def test_js_input_set_and_clear_stay_inside_queue(
    js_results: dict[str, Any],
) -> None:
    data = js_results["queue"]
    assert isinstance(data, dict)
    io_order = data["io_order"]
    assert isinstance(io_order, list)
    kinds = [item["kind"] for item in io_order]
    assert kinds == [
        "write",
        "start",
        "end",
        "write",
        "write",
        "start",
        "end",
        "write",
    ]
    assert io_order[0]["value"] == "one"
    assert io_order[3]["value"] == ""
    assert io_order[4]["value"] == "two"
    assert io_order[7]["value"] == ""


def test_js_snap_queue_serializes_page_calls(js_results: dict[str, Any]) -> None:
    data = js_results["queue"]
    assert isinstance(data, dict)
    order = data["order"]
    assert isinstance(order, list)
    assert len(order) == 4
    assert order[0].startswith("start")
    assert order[1].startswith("end")
    assert order[0][5:] == order[1][3:]
    assert order[2].startswith("start")
    assert order[3].startswith("end")
    assert order[2][5:] == order[3][3:]
