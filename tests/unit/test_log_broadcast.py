"""Unit tests for the in-memory log ring buffer and SSE broadcast plumbing."""

from __future__ import annotations

import asyncio
import logging

import pytest

from aistudio_api.infrastructure.utils.log_broadcast import (
    BroadcastHandler,
    LogBroadcaster,
    sanitize_log_message,
)


def _publish(broadcaster: LogBroadcaster, count: int, level: str = "INFO") -> list[int]:
    return [
        broadcaster.publish(level, "aistudio.test", f"message-{index}").id
        for index in range(count)
    ]


def test_ring_buffer_capacity_and_monotonic_sequence():
    broadcaster = LogBroadcaster(capacity=3)
    ids = _publish(broadcaster, 5)

    assert ids == [1, 2, 3, 4, 5]
    entries, gap = broadcaster.replay()
    assert [entry.id for entry in entries] == [3, 4, 5]
    assert [entry.message for entry in entries] == [
        "message-2",
        "message-3",
        "message-4",
    ]
    assert gap is None
    assert broadcaster.oldest_id == 3


def test_replay_level_filter_last_id_and_gap():
    broadcaster = LogBroadcaster(capacity=10)
    broadcaster.publish("DEBUG", "aistudio.test", "debug")
    broadcaster.publish("INFO", "aistudio.test", "info")
    broadcaster.publish("WARNING", "aistudio.test", "warning")
    broadcaster.publish("ERROR", "aistudio.test", "error")

    warnings, gap = broadcaster.replay(level="WARNING")
    assert [entry.level for entry in warnings] == ["WARNING", "ERROR"]
    assert gap is None

    after_second, gap = broadcaster.replay(level="DEBUG", last_id=2)
    assert [entry.id for entry in after_second] == [3, 4]
    assert gap is None

    broadcaster = LogBroadcaster(capacity=3)
    _publish(broadcaster, 6)
    entries, gap = broadcaster.replay(last_id=1)
    assert [entry.id for entry in entries] == [4, 5, 6]
    assert gap == {"last_id": 1, "oldest_id": 4}


def test_replay_limit_keeps_most_recent_entries():
    broadcaster = LogBroadcaster(capacity=300)
    _publish(broadcaster, 250)

    entries, _ = broadcaster.replay(limit=100)
    assert len(entries) == 100
    assert entries[0].id == 151
    assert entries[-1].id == 250


async def test_subscriber_queue_overflow_drops_oldest_and_counts():
    broadcaster = LogBroadcaster(capacity=10, queue_size=2)
    subscription = broadcaster.subscribe()

    _publish(broadcaster, 3)
    await asyncio.sleep(0.01)

    queued = [
        subscription.queue.get_nowait() for _ in range(subscription.queue.qsize())
    ]
    assert [entry.id for entry in queued] == [2, 3]
    assert subscription.take_dropped() == 1

    broadcaster.unsubscribe(subscription)
    assert broadcaster.subscriber_count == 0


async def test_subscriber_limit_raises():
    broadcaster = LogBroadcaster(capacity=10)
    subscriptions = [broadcaster.subscribe() for _ in range(8)]
    with pytest.raises(Exception) as exc_info:
        broadcaster.subscribe()
    assert "8" in str(exc_info.value) or "subscription" in str(exc_info.value).lower()

    for subscription in subscriptions:
        broadcaster.unsubscribe(subscription)


def test_sanitize_authorization_bearer_cookie_sapisid_and_api_key():
    sanitized = sanitize_log_message(
        "Authorization: Bearer sk-very-secret-token-123\n"
        "Cookie: SAPISID=abc123; SID=def456; theme=dark\n"
        "x-goog-api-key: AIzaSyVerySecretValue\n"
        'body={"api_key": "json-secret", "note": "keep me"}\n'
        "plain Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature"
    )

    assert "sk-very-secret-token-123" not in sanitized
    assert "abc123" not in sanitized
    assert "def456" not in sanitized
    assert "AIzaSyVerySecretValue" not in sanitized
    assert "json-secret" not in sanitized
    assert "eyJhbGciOiJIUzI1NiJ9.payload.signature" not in sanitized
    assert "theme=dark" in sanitized
    assert "keep me" in sanitized


def test_broadcast_handler_publishes_sanitized_record_without_lines():
    broadcaster = LogBroadcaster(capacity=10)
    handler = BroadcastHandler(broadcaster=broadcaster)
    record = logging.LogRecord(
        name="aistudio.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="Authorization: Bearer secret-value",
        args=(),
        exc_info=None,
    )

    handler.emit(record)

    entries, _ = broadcaster.replay()
    assert len(entries) == 1
    assert entries[0].level == "WARNING"
    assert entries[0].logger == "aistudio.test"
    assert "secret-value" not in entries[0].message
