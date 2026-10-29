"""Request-application retry/release behavior with a scripted routing pipeline."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.responses import StreamingResponse

from aistudio_api.api.response_models import GeminiGenerateContentResponse
from aistudio_api.api.schemas import (
    GeminiContent,
    GeminiGenerateContentRequest,
    GeminiPart,
)
from aistudio_api.application.api_service_gemini import (
    handle_gemini_generate_content,
)
from aistudio_api.domain.errors import RequestError, UsageLimitExceeded
from aistudio_api.domain.models import Candidate, ModelOutput
from tests.unit.routing_fakes import (
    FakeHandle,
    ScriptedPipeline,
    make_attempt,
    make_context,
)


def _request() -> GeminiGenerateContentRequest:
    return GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hello")])]
    )


def _output(text: str = "ok") -> ModelOutput:
    return ModelOutput(
        candidates=[Candidate(text=text)],
        usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    )


@pytest.mark.asyncio
async def test_non_streaming_204_retries_same_instance_before_switch() -> None:
    calls = 0

    async def fake_generate(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RequestError(204, "no content")
        return _output("after retry")

    fake_client = MagicMock()
    fake_client.generate_content = AsyncMock(side_effect=fake_generate)
    attempt = make_attempt(fake_client)
    pipeline = ScriptedPipeline(
        [attempt], actions=["retry_same"], context=make_context()
    )

    response = await handle_gemini_generate_content(
        "gemini-3.7-flash", _request(), stream=False, pipeline=pipeline
    )

    assert calls == 2
    assert isinstance(response, GeminiGenerateContentResponse)
    assert response.candidates[0].content.parts[0].text == "after retry"
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True


@pytest.mark.asyncio
async def test_non_streaming_error_still_releases_active_request() -> None:
    fake_client = MagicMock()
    fake_client.generate_content = AsyncMock(
        side_effect=UsageLimitExceeded("quota")
    )
    attempt = make_attempt(fake_client)
    pipeline = ScriptedPipeline([attempt], context=make_context())

    with pytest.raises(UsageLimitExceeded):
        await handle_gemini_generate_content(
            "gemini-3.7-flash", _request(), stream=False, pipeline=pipeline
        )
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True


@pytest.mark.asyncio
async def test_streaming_does_not_switch_after_body_yielded() -> None:
    async def fake_stream(**_kwargs):
        yield ("body", "partial")
        raise UsageLimitExceeded("late 429")

    fake_client = MagicMock()
    fake_client.stream_generate_content = fake_stream
    first = make_attempt(fake_client, instance_id="inst-1")
    second = make_attempt(fake_client, instance_id="inst-2")
    pipeline = ScriptedPipeline([first, second], context=make_context())

    response = await handle_gemini_generate_content(
        "gemini-3.7-flash", _request(), stream=True, pipeline=pipeline
    )
    assert isinstance(response, StreamingResponse)
    chunks = [
        chunk
        async for chunk in cast(AsyncIterator[str], response.body_iterator)
    ]

    assert any("partial" in str(chunk) for chunk in chunks)
    assert any("RESOURCE_EXHAUSTED" in str(chunk) for chunk in chunks)
    assert pipeline.attempts == [second]
    assert isinstance(first.handle, FakeHandle)
    assert first.handle.released is True


@pytest.mark.asyncio
async def test_stream_close_releases_active_request() -> None:
    async def fake_stream(**_kwargs):
        yield ("body", "first")
        await __import__("asyncio").Event().wait()

    fake_client = MagicMock()
    fake_client.stream_generate_content = fake_stream
    attempt = make_attempt(fake_client)
    pipeline = ScriptedPipeline([attempt], context=make_context())

    response = await handle_gemini_generate_content(
        "gemini-3.7-flash", _request(), stream=True, pipeline=pipeline
    )
    assert isinstance(response, StreamingResponse)
    iterator = cast(AsyncGenerator[str, None], response.body_iterator)
    first = await iterator.__anext__()
    assert "first" in str(first)
    await iterator.aclose()
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True


@pytest.mark.asyncio
async def test_forced_429_hook_retries_same_instance(monkeypatch) -> None:
    monkeypatch.setenv("AISTUDIO_TEST_HOOKS", "1")
    monkeypatch.setenv("AISTUDIO_TEST_FORCE_429_COUNT", "1")
    from aistudio_api.application.testing_hooks import reset_test_hooks

    reset_test_hooks()

    fake_client = MagicMock()
    fake_client.generate_content = AsyncMock(return_value=_output("hook ok"))
    attempt = make_attempt(fake_client)
    pipeline = ScriptedPipeline(
        [attempt], actions=["retry_same"], context=make_context()
    )

    response = await handle_gemini_generate_content(
        "gemini-3.7-flash", _request(), stream=False, pipeline=pipeline
    )

    assert fake_client.generate_content.await_count == 1
    assert isinstance(response, GeminiGenerateContentResponse)
    assert response.candidates[0].content.parts[0].text == "hook ok"
