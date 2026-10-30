"""Tests for the streaming ATG unwrapper and the incremental JSON decoder."""

from __future__ import annotations

from typing import Any, cast

import pytest

from aistudio_api.application.atg.config import load_atg_config
from aistudio_api.application.atg.extractor import SyntheticRequiredError
from aistudio_api.application.atg.stream_decoder import IncrementalJSONStringDecoder
from aistudio_api.application.atg.stream_extractor import create_stream_extractor

TOOL = "atg_emit_deadbeef"


def _unwrapper(mode="prefer", **overrides):
    cfg = load_atg_config({"mode": mode, **overrides})
    return create_stream_extractor(cfg, TOOL)


# ========== StreamUnwrapper state machine ==========


def test_synthetic_tool_call_is_converted_to_body():
    u = _unwrapper()
    events = u.process_event(
        "tool_calls", [{"name": TOOL, "args": {"content": "hello"}}]
    )
    assert events == [("body", "hello")]
    assert u.stats.synthetic_hit is True
    assert u.stats.synthetic_count == 1
    assert u.should_remap_finish_reason() is True
    assert u.finalize() == []


def test_real_tool_call_passes_through():
    u = _unwrapper()
    fc = {"name": "real_tool", "args": {"x": 1}}
    events = u.process_event("tool_calls", [fc])
    assert events == [("tool_calls", [fc])]
    assert u.stats.real_tool_count == 1
    assert u.should_remap_finish_reason() is False


def test_real_and_synthetic_coexist():
    u = _unwrapper()
    events = u.process_event(
        "tool_calls",
        [{"name": "real_tool", "args": {}}, {"name": TOOL, "args": {"content": "ans"}}],
    )
    assert events[0] == ("body", "ans")
    assert events[1][0] == "tool_calls"
    remaining = cast("list[dict[str, Any]]", events[1][1])
    assert [fc["name"] for fc in remaining] == ["real_tool"]
    assert u.stats.real_tool_count == 1
    assert u.should_remap_finish_reason() is False


def test_body_and_thinking_pass_through_with_conflict_on_late_synthetic():
    u = _unwrapper()
    assert u.process_event("body", "hi") == [("body", "hi")]
    assert u.process_event("thinking", "thought") == [("thinking", "thought")]
    u.process_event("tool_calls", [{"name": TOOL, "args": {"content": "ans"}}])
    assert u.stats.conflict is True


def test_unknown_event_passes_through():
    u = _unwrapper()
    assert u.process_event("images", ["img"]) == [("images", ["img"])]
    assert u.process_event("weird", "payload") == [("weird", "payload")]


def test_side_buffer_suppresses_preamble_when_synthetic_arrives():
    u = _unwrapper(side_buffer_bytes=4096)
    assert u.process_event("body", "preamble") == []
    assert u.process_event(
        "tool_calls", [{"name": TOOL, "args": {"content": "ans"}}]
    ) == [("body", "ans")]
    assert u.stats.conflict is True
    assert u.finalize() == []


def test_side_buffer_flushes_when_no_synthetic():
    u = _unwrapper(side_buffer_bytes=4096)
    assert u.process_event("body", "pre") == []
    assert u.process_event("body", "amble") == []
    assert u.finalize() == [("body", "preamble")]


def test_required_mode_finalize_raises_when_unsatisfied():
    u = _unwrapper(mode="required")
    u.process_event("body", "hi")
    with pytest.raises(SyntheticRequiredError):
        u.finalize()


def test_required_mode_finalize_ok_with_real_tool():
    u = _unwrapper(mode="required")
    u.process_event("tool_calls", [{"name": "real_tool", "args": {}}])
    assert u.finalize() == []


def test_cross_chunk_string_args_accumulate():
    u = _unwrapper()
    assert u.process_event(
        "tool_calls", [{"name": TOOL, "arguments": '{"content":"ab'}]
    ) == [("body", "ab")]
    assert u.process_event("tool_calls", [{"arguments": 'c"}'}]) == [("body", "c")]
    assert u.finalize() == []
    assert u.stats.synthetic_hit is True


def test_invalid_options_do_not_raise_in_stream():
    # object args without content is non-fatal in the stream path (Ark semantics)
    u = _unwrapper()
    events = u.process_event("tool_calls", [{"name": TOOL, "args": {"wrong": 1}}])
    assert events == []
    assert u.stats.synthetic_hit is True


# ========== IncrementalJSONStringDecoder ==========


def test_decoder_complete_json_string():
    d = IncrementalJSONStringDecoder()
    res = d.feed('{"content": "héllo"}')
    assert res.output == "héllo"
    assert res.done is True


def test_decoder_escape_split_across_chunks():
    d = IncrementalJSONStringDecoder()
    assert d.feed('{"content":"a\\').output == "a"
    second = d.feed('n"}')
    assert second.output == "\n"
    assert second.done is True


def test_decoder_string_split_across_chunks():
    d = IncrementalJSONStringDecoder()
    assert d.feed('{"content":"ab').output == "ab"
    assert d.feed('c"}').output == "c"


def test_decoder_unicode_escape():
    d = IncrementalJSONStringDecoder()
    res = d.feed('{"content":"\\u0041\\u0042"}')
    assert res.output == "AB"


def test_decoder_empty_chunk_reports_done_state():
    d = IncrementalJSONStringDecoder()
    assert d.feed("").done is False
    d.feed('{"content":"x"}')
    assert d.feed("").done is True
