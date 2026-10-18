"""Tests for deterministic synthetic-argument repair."""

from __future__ import annotations

from aistudio_api.application.atg.repair import repair_synthetic_arguments

MAX_BYTES = 16 * 1024 * 1024


def test_fast_json_path_is_not_flagged_repaired():
    res = repair_synthetic_arguments('{"content": "hello"}', MAX_BYTES)
    assert res.content == "hello"
    assert res.error is None
    assert res.repaired is False


def test_markdown_code_fence_is_repaired():
    raw = '```json\n{"content": "fenced answer"}\n```'
    res = repair_synthetic_arguments(raw, MAX_BYTES)
    assert res.content == "fenced answer"
    assert res.repaired is True


def test_structural_repair_unclosed_string_and_brace():
    res = repair_synthetic_arguments('{"content": "truncated', MAX_BYTES)
    assert res.content == "truncated"
    assert res.repaired is True


def test_structural_repair_trailing_comma():
    res = repair_synthetic_arguments('{"content": "a",}', MAX_BYTES)
    assert res.content == "a"
    assert res.repaired is True


def test_structural_repair_missing_open_brace_prefix():
    res = repair_synthetic_arguments('noise {"content": "x"}', MAX_BYTES)
    assert res.content == "x"
    assert res.repaired is True


def test_scanner_fallback_extracts_content_key():
    res = repair_synthetic_arguments('garbage "content": "scanned"', MAX_BYTES)
    assert res.content == "scanned"
    assert res.repaired is True


def test_scanner_fallback_alternate_keys():
    res = repair_synthetic_arguments('x "answer": "via-answer"', MAX_BYTES)
    assert res.content == "via-answer"
    assert res.repaired is True


def test_unicode_escape_in_scanner():
    res = repair_synthetic_arguments('x "content": "a\\u0041b"', MAX_BYTES)
    assert res.content == "aAb"


def test_oversize_rejected():
    res = repair_synthetic_arguments("x" * 100, 10)
    assert res.content is None
    assert res.error == "arguments exceed maximum allowed size"
    assert res.repaired is False


def test_empty_rejected():
    res = repair_synthetic_arguments("   ", MAX_BYTES)
    assert res.content is None
    assert res.error == "arguments decode failed"


def test_non_string_rejected():
    res = repair_synthetic_arguments({"content": "x"}, MAX_BYTES)
    assert res.content is None
    assert res.error == "arguments exceed maximum allowed size"


def test_undecodable_rejected():
    res = repair_synthetic_arguments("~~~ totally broken ~~~", MAX_BYTES)
    assert res.content is None
    assert res.error == "synthetic tool arguments decode failed"


def test_non_content_json_is_not_a_fast_hit():
    # valid JSON but no string content -> falls through to repair paths
    res = repair_synthetic_arguments('{"answer": "ok"}', MAX_BYTES)
    assert res.content == "ok"
    assert res.repaired is True
