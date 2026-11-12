"""Protocol-neutral generation routing contracts (P5 B5b)."""

from __future__ import annotations

import logging
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.application import generation_service
from aistudio_api.application.contracts.gemini import GeminiGenerateContentRequest
from aistudio_api.domain.errors import UsageLimitExceeded
from aistudio_api.domain.models import Candidate, GeneratedImage, ModelOutput
from tests.unit.routing_fakes import (
    FakeHandle,
    ScriptedPipeline,
    make_attempt,
    make_context,
)


def _request() -> GeminiGenerateContentRequest:
    return GeminiGenerateContentRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "hello"}]}]}
    )


def _released(attempt: Any) -> bool:
    return bool(cast("FakeHandle", attempt.handle).released)


def _output(text: str = "ok") -> ModelOutput:
    return ModelOutput(
        candidates=[Candidate(text=text)],
        usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        response_id="resp-1",
    )


@pytest.mark.asyncio
async def test_generate_uses_pipeline_surface_and_records_success():
    client = MagicMock()
    client.generate_content = AsyncMock(return_value=_output())
    attempt = make_attempt(client)
    pipeline = ScriptedPipeline([attempt], context=make_context())

    outcome = await generation_service.generate(
        model_path="gemini-3.7-flash",
        req=_request(),
        pipeline=pipeline,
        api_format="openai",
    )

    assert outcome.output.text == "ok"
    assert outcome.model == "models/gemini-3.7-flash"
    assert outcome.surface == "chat"
    assert outcome.response_id == "resp-1"
    assert pipeline.successes == [
        ("inst-1", {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3})
    ]
    assert isinstance(attempt.handle, FakeHandle)
    assert _released(attempt) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["images", "reasoning_images"])
async def test_generate_image_only_output_is_not_empty(field):
    image = GeneratedImage(mime="image/png", data=b"png", size=3)
    candidate = (
        Candidate(images=[image])
        if field == "images"
        else Candidate(reasoning_images=[image])
    )
    client = MagicMock()
    client.generate_content = AsyncMock(
        return_value=ModelOutput(candidates=[candidate], usage={})
    )
    attempt = make_attempt(client)
    pipeline = ScriptedPipeline([attempt], context=make_context())

    outcome = await generation_service.generate(
        model_path="gemini-3.7-flash",
        req=_request(),
        pipeline=pipeline,
        api_format="openai",
    )

    assert outcome.output.candidates[0] == candidate
    assert client.generate_content.await_count == 1
    assert _released(attempt) is True


@pytest.mark.asyncio
async def test_generate_retries_same_attempt_without_reacquire():
    calls = 0

    async def fake_generate(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise UsageLimitExceeded("429")
        return _output("retried")

    client = MagicMock()
    client.generate_content = AsyncMock(side_effect=fake_generate)
    attempt = make_attempt(client)
    pipeline = ScriptedPipeline(
        [attempt], actions=["retry_same"], context=make_context()
    )

    outcome = await generation_service.generate(
        model_path="gemini-3.7-flash",
        req=_request(),
        pipeline=pipeline,
        api_format="gemini",
    )

    assert outcome.output.text == "retried"
    assert calls == 2
    assert pipeline.attempts == []
    assert _released(attempt) is True


@pytest.mark.asyncio
async def test_generate_switch_releases_first_attempt():
    first_client = MagicMock()
    first_client.generate_content = AsyncMock(side_effect=UsageLimitExceeded("429"))
    second_client = MagicMock()
    second_client.generate_content = AsyncMock(return_value=_output("second"))
    first = make_attempt(first_client, instance_id="inst-1")
    second = make_attempt(second_client, instance_id="inst-2")
    pipeline = ScriptedPipeline(
        [first, second], actions=["switch"], context=make_context()
    )

    outcome = await generation_service.generate(
        model_path="gemini-3.7-flash",
        req=_request(),
        pipeline=pipeline,
        api_format="gemini",
    )

    assert outcome.output.text == "second"
    assert _released(first) is True
    assert _released(second) is True
    assert pipeline.successes[0][0] == "inst-2"


@pytest.mark.asyncio
async def test_generate_failure_releases_attempt_in_finally():
    client = MagicMock()
    client.generate_content = AsyncMock(side_effect=RuntimeError("boom"))
    attempt = make_attempt(client)
    pipeline = ScriptedPipeline([attempt], context=make_context())

    with pytest.raises(RuntimeError):
        await generation_service.generate(
            model_path="gemini-3.7-flash",
            req=_request(),
            pipeline=pipeline,
            api_format="gemini",
        )

    assert _released(attempt) is True


@pytest.mark.asyncio
async def test_stream_generation_emits_routing_wait_and_terminal_usage():
    async def fake_stream(**_kwargs):
        yield ("body", "answer")
        yield ("finish_reason", 1)
        yield (
            "usage",
            {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        )
        yield ("response_id", "resp-stream")

    client = MagicMock()
    client.stream_generate_content = fake_stream
    attempt = make_attempt(client)
    pipeline = ScriptedPipeline([attempt], context=make_context())

    events = [
        event
        async for event in generation_service.stream_generation(
            model_path="gemini-3.7-flash",
            req=_request(),
            pipeline=pipeline,
            api_format="openai",
        )
    ]

    assert events[0].kind == "routing_wait"
    assert any(event.kind == "body" and event.payload == "answer" for event in events)
    terminal = events[-1]
    assert terminal.kind == "usage"
    assert terminal.finish_reason == "STOP"
    assert terminal.response_id == "resp-stream"
    assert isinstance(terminal.payload, dict)
    assert cast("dict[str, Any]", terminal.payload)["total_tokens"] == 5
    assert _released(attempt) is True


@pytest.mark.asyncio
async def test_stream_generation_cancel_releases_attempt():
    async def open_stream(**_kwargs):
        yield ("body", "first")
        await __import__("asyncio").Event().wait()

    client = MagicMock()
    client.stream_generate_content = open_stream
    attempt = make_attempt(client)
    pipeline = ScriptedPipeline([attempt], context=make_context())

    iterator = generation_service.stream_generation(
        model_path="gemini-3.7-flash",
        req=_request(),
        pipeline=pipeline,
        api_format="gemini",
    )
    first = await iterator.__anext__()
    assert first.kind == "routing_wait"
    second = await iterator.__anext__()
    assert second.kind == "body"
    await iterator.aclose()
    assert _released(attempt) is True


@pytest.mark.asyncio
async def test_stream_generation_retries_same_attempt_before_switch():
    calls = 0

    async def fake_stream(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise UsageLimitExceeded("429 before data")
        yield ("body", "recovered")
        yield ("finish_reason", 1)
        yield ("usage", {"prompt_tokens": 1, "completion_tokens": 1})

    client = MagicMock()
    client.stream_generate_content = fake_stream
    attempt = make_attempt(client)
    pipeline = ScriptedPipeline(
        [attempt], actions=["retry_same"], context=make_context()
    )

    events = [
        event
        async for event in generation_service.stream_generation(
            model_path="gemini-3.7-flash",
            req=_request(),
            pipeline=pipeline,
            api_format="gemini",
        )
    ]

    assert calls == 2
    assert any(
        event.kind == "body" and event.payload == "recovered" for event in events
    )
    assert _released(attempt) is True


@pytest.mark.asyncio
async def test_generate_thinking_only_skips_retry_and_warns(caplog):
    client = MagicMock()
    client.generate_content = AsyncMock(
        return_value=ModelOutput(
            candidates=[Candidate(thinking="only reasoning")],
            usage={"prompt_tokens": 1, "completion_tokens": 2},
        )
    )
    attempt = make_attempt(client)
    pipeline = ScriptedPipeline([attempt], context=make_context())

    with caplog.at_level(logging.WARNING, logger="aistudio.generation"):
        outcome = await generation_service.generate(
            model_path="gemini-3.7-flash",
            req=_request(),
            pipeline=pipeline,
            api_format="openai",
        )

    assert client.generate_content.await_count == 1
    assert pipeline.empty_retries == 0
    assert outcome.output.candidates[0].thinking == "only reasoning"
    warnings = [
        record for record in caplog.records if "思维链截断告警" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    assert "thinking_len=14" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_stream_generation_thinking_only_skips_retry_and_warns(caplog):
    calls = 0

    async def fake_stream(**_kwargs):
        nonlocal calls
        calls += 1
        yield ("thinking", "deep reasoning")
        yield ("finish_reason", 1)
        yield ("usage", {"prompt_tokens": 1, "completion_tokens": 2})
        yield ("response_id", "resp-think")

    client = MagicMock()
    client.stream_generate_content = fake_stream
    attempt = make_attempt(client)
    pipeline = ScriptedPipeline([attempt], context=make_context())

    with caplog.at_level(logging.WARNING, logger="aistudio.generation"):
        events = [
            event
            async for event in generation_service.stream_generation(
                model_path="gemini-3.7-flash",
                req=_request(),
                pipeline=pipeline,
                api_format="openai",
            )
        ]

    assert calls == 1
    assert pipeline.empty_retries == 0
    assert any(event.kind == "thinking" for event in events)
    assert not any(event.kind in ("body", "tool_calls", "images") for event in events)
    warnings = [
        record for record in caplog.records if "思维链截断告警" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    assert "thinking_len=14" in warnings[0].getMessage()
    assert events[-1].kind == "usage"
