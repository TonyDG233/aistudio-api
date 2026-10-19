"""Thought signature cache behavior: reusable, bounded, restart-safe."""

from __future__ import annotations

from aistudio_api.application.format_converters.signature_cache import (
    ThoughtSignatureCache,
)


def test_signature_is_reusable_not_one_shot(tmp_path):
    cache = ThoughtSignatureCache(path=tmp_path / "signatures.json")
    cache.put("call_1", "sig-1")
    assert cache.get("call_1") == "sig-1"
    assert cache.get("call_1") == "sig-1"
    assert cache.get("call_1") == "sig-1"


def test_signature_survives_a_new_cache_instance(tmp_path):
    path = tmp_path / "signatures.json"
    first = ThoughtSignatureCache(path=path)
    first.put("call_restart", "sig-restart")

    second = ThoughtSignatureCache(path=path)
    assert second.get("call_restart") == "sig-restart"


def test_multiple_tool_calls_keep_independent_signatures(tmp_path):
    cache = ThoughtSignatureCache(path=tmp_path / "signatures.json")
    cache.put("call_1", "sig-1")
    cache.put("call_2", "sig-2")
    assert cache.get("call_1") == "sig-1"
    assert cache.get("call_2") == "sig-2"


def test_cache_is_bounded(tmp_path):
    cache = ThoughtSignatureCache(max_entries=2, path=tmp_path / "signatures.json")
    cache.put("call_1", "sig-1")
    cache.put("call_2", "sig-2")
    cache.put("call_3", "sig-3")
    assert cache.get("call_1") is None
    assert cache.get("call_2") == "sig-2"
    assert cache.get("call_3") == "sig-3"
