"""Tests for the non-streaming ATG extractor (ModelOutput adaptation)."""

from __future__ import annotations

import pytest

from aistudio_api.application.atg.config import load_atg_config
from aistudio_api.application.atg.extractor import (
    SyntheticArgumentsError,
    SyntheticRequiredError,
    create_extractor,
)
from aistudio_api.domain.models import Candidate, ModelOutput

TOOL = "atg_emit_deadbeef"
STOP = 1


def _extractor(mode="prefer", **overrides):
    return create_extractor(load_atg_config({"mode": mode, **overrides}))


def _output(*candidates: Candidate) -> ModelOutput:
    return ModelOutput(candidates=list(candidates))


def test_off_mode_or_no_tool_passthrough():
    out = _output(
        Candidate(text="hi", function_calls=[{"name": TOOL, "args": {"content": "x"}}])
    )
    result = _extractor(mode="off").normalize_non_streaming(out, TOOL)
    assert result.stats.synthetic_hit is False
    assert result.output.text == "hi"
    assert len(result.output.function_calls) == 1

    result2 = _extractor().normalize_non_streaming(out, "")
    assert result2.stats.synthetic_hit is False


def test_synthetic_hit_becomes_text():
    out = _output(
        Candidate(
            text="", function_calls=[{"name": TOOL, "args": {"content": "FINAL"}}]
        )
    )
    result = _extractor().normalize_non_streaming(out, TOOL)
    assert result.output.text == "FINAL"
    assert result.output.function_calls == []
    assert result.stats.synthetic_hit is True
    assert result.stats.synthetic_count == 1
    assert result.stats.real_tool_count == 0
    assert result.stats.to_dict()["syntheticHit"] is True


def test_conflict_when_text_present_alongside_synthetic():
    out = _output(
        Candidate(
            text="preamble",
            function_calls=[{"name": TOOL, "args": {"content": "answer"}}],
        )
    )
    result = _extractor().normalize_non_streaming(out, TOOL)
    assert result.stats.conflict is True
    assert result.output.text == "answer"


def test_string_args_repaired():
    out = _output(
        Candidate(
            function_calls=[
                {"name": TOOL, "args": '```json\n{"content": "fenced"}\n```'}
            ]
        )
    )
    result = _extractor().normalize_non_streaming(out, TOOL)
    assert result.output.text == "fenced"
    assert result.stats.repaired is True


def test_real_tool_call_is_retained():
    out = _output(
        Candidate(
            function_calls=[
                {"name": "real_tool", "args": {"x": 1}},
                {"name": TOOL, "args": {"content": "answer"}},
            ]
        )
    )
    result = _extractor().normalize_non_streaming(out, TOOL)
    assert result.output.text == "answer"
    assert [fc["name"] for fc in result.output.function_calls] == ["real_tool"]
    assert result.stats.real_tool_count == 1
    assert result.stats.synthetic_hit is True


def test_object_args_without_content_raises():
    out = _output(Candidate(function_calls=[{"name": TOOL, "args": {"wrong": "x"}}]))
    with pytest.raises(SyntheticArgumentsError):
        _extractor().normalize_non_streaming(out, TOOL)


def test_string_args_fail_recovery_raises_without_repair():
    out = _output(
        Candidate(function_calls=[{"name": TOOL, "args": '{"content": "x"}'}])
    )
    with pytest.raises(SyntheticArgumentsError):
        _extractor(recovery="fail").normalize_non_streaming(out, TOOL)


def test_required_missing_raises_explicitly():
    out = _output(Candidate(text="just text"))
    with pytest.raises(SyntheticRequiredError) as exc:
        _extractor(mode="required").normalize_non_streaming(out, TOOL)
    assert "synthetic tool call required" in str(exc.value)


def test_required_satisfied_by_synthetic():
    out = _output(Candidate(function_calls=[{"name": TOOL, "args": {"content": "ok"}}]))
    result = _extractor(mode="required").normalize_non_streaming(out, TOOL)
    assert result.output.text == "ok"


def test_required_satisfied_by_real_tool():
    out = _output(Candidate(function_calls=[{"name": "real_tool", "args": {}}]))
    result = _extractor(mode="required").normalize_non_streaming(out, TOOL)
    assert result.stats.real_tool_count == 1


def test_finish_reason_tool_calls_rewritten_to_stop():
    candidate = Candidate(
        function_calls=[{"name": TOOL, "args": {"content": "answer"}}]
    )
    candidate.finish_reason = "TOOL_CALLS"  # type: ignore[assignment]
    out = _output(candidate)
    result = _extractor(mode="required").normalize_non_streaming(out, TOOL)
    assert result.output.candidates[0].finish_reason == STOP


def test_finish_reason_not_rewritten_when_real_call_present():
    candidate = Candidate(
        function_calls=[
            {"name": "real_tool", "args": {}},
            {"name": TOOL, "args": {"content": "answer"}},
        ]
    )
    candidate.finish_reason = "TOOL_CALLS"  # type: ignore[assignment]
    result = _extractor().normalize_non_streaming(_output(candidate), TOOL)
    assert result.output.candidates[0].finish_reason == "TOOL_CALLS"


def test_no_candidates_returns_early():
    result = _extractor(mode="required").normalize_non_streaming(_output(), TOOL)
    assert result.stats.synthetic_hit is False


def test_arguments_key_is_honored():
    out = _output(
        Candidate(
            function_calls=[{"name": TOOL, "arguments": {"content": "via-arguments"}}]
        )
    )
    result = _extractor().normalize_non_streaming(out, TOOL)
    assert result.output.text == "via-arguments"
