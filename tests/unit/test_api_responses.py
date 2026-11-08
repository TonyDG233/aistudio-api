import pytest

from aistudio_api.api.error_mapping import classify_gemini_error_payload
from aistudio_api.api.responses import (
    to_gemini_parts,
    to_gemini_usage_metadata,
)
from aistudio_api.application.finish_reasons import to_gemini_finish_reason
from aistudio_api.domain.errors import (
    AuthError,
    BrowserTransportError,
    RequestError,
    SessionExpiredError,
    SurfaceCapacityError,
    SurfaceSwitchExhaustedError,
    TierRequiredError,
    UsageLimitExceeded,
)


def test_to_gemini_usage_metadata_uses_visible_and_reasoning_tokens():
    assert to_gemini_usage_metadata(
        {
            "prompt_tokens": 9,
            "completion_tokens": 316,
            "total_tokens": 325,
            "completion_tokens_details": {
                "reasoning_tokens": 290,
                "visible_tokens": 26,
            },
        }
    ).model_dump(mode="json") == {
        "promptTokenCount": 9,
        "candidatesTokenCount": 316,
        "thoughtsTokenCount": 290,
        "totalTokenCount": 325,
        "cachedContentTokenCount": None,
    }
    assert to_gemini_usage_metadata(
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cached_tokens": 80,
        }
    ).model_dump(mode="json", exclude_none=True) == {
        "promptTokenCount": 100,
        "candidatesTokenCount": 20,
        "thoughtsTokenCount": 0,
        "totalTokenCount": 120,
        "cachedContentTokenCount": 80,
    }


def test_to_gemini_parts_keeps_function_call_and_response_parts():
    parts = to_gemini_parts(
        "",
        function_calls=[{"name": "getWeather", "args": {"city": "Shanghai"}}],
        function_responses=[{"name": "getWeather", "args": {"temperature": "24C"}}],
    )

    assert [part.model_dump(mode="json", exclude_none=True) for part in parts] == [
        {"functionCall": {"name": "getWeather", "args": {"city": "Shanghai"}}},
        {
            "functionResponse": {
                "name": "getWeather",
                "response": {"temperature": "24C"},
            }
        },
    ]


def test_to_gemini_parts_can_emit_thought_part():
    assert [
        part.model_dump(mode="json", exclude_none=True)
        for part in to_gemini_parts("答案", thinking="思考")
    ] == [
        {"text": "思考", "thought": True},
        {"text": "答案"},
    ]


def test_to_gemini_parts_can_emit_inline_image_data():
    from aistudio_api.domain.models import GeneratedImage

    parts = to_gemini_parts(
        "说明",
        images=[GeneratedImage(mime="image/png", data=b"png-bytes", size=9)],
    )

    assert [part.model_dump(mode="json", exclude_none=True) for part in parts] == [
        {"text": "说明"},
        {"inlineData": {"mimeType": "image/png", "data": "cG5nLWJ5dGVz"}},
    ]


def test_classify_gemini_error_payload():
    """Verify exception mapping to Gemini HTTP status and reason code."""
    assert classify_gemini_error_payload(SessionExpiredError("expired")) == (
        401,
        "All accounts have expired sessions. Please import fresh cookies.",
        "UNAUTHENTICATED",
    )
    assert classify_gemini_error_payload(AuthError("auth forbidden")) == (
        403,
        "auth forbidden",
        "PERMISSION_DENIED",
    )
    assert classify_gemini_error_payload(UsageLimitExceeded("quota exceeded")) == (
        429,
        "quota exceeded",
        "RESOURCE_EXHAUSTED",
    )
    assert classify_gemini_error_payload(ValueError("invalid input")) == (
        400,
        "invalid input",
        "INVALID_ARGUMENT",
    )
    assert classify_gemini_error_payload(RequestError(429, "rate limit")) == (
        429,
        "HTTP 429: rate limit",
        "RESOURCE_EXHAUSTED",
    )
    jspb_400 = 'HTTP 400: [,[3,"Invalid value (), Unexpected list for single non-message field.",[["type.googleapis.com/google.rpc.BadRequest"]]]]'
    assert classify_gemini_error_payload(RequestError(400, jspb_400)) == (
        400,
        "Invalid value (), Unexpected list for single non-message field.",
        "INVALID_ARGUMENT",
    )
    assert classify_gemini_error_payload(
        BrowserTransportError("streaming request failed: Failed to fetch")
    ) == (
        502,
        "streaming request failed: Failed to fetch",
        "UNAVAILABLE",
    )


def test_classify_tier_and_routing_errors():
    tier = TierRequiredError(
        model="gemini-4-argon",
        tier="Pro",
        required_tier="Pro",
        current_tier="Free",
    )
    assert classify_gemini_error_payload(tier)[0] == 403
    assert classify_gemini_error_payload(tier)[2] == "PERMISSION_DENIED"

    capacity = SurfaceCapacityError(surface="chat")
    assert classify_gemini_error_payload(capacity)[0] == 503
    assert classify_gemini_error_payload(capacity)[2] == "UNAVAILABLE"

    exhausted = SurfaceSwitchExhaustedError(surface="chat")
    assert classify_gemini_error_payload(exhausted)[0] == 503


def test_to_gemini_finish_reason_mappings():
    assert to_gemini_finish_reason(None) == "STOP"
    assert to_gemini_finish_reason(1) == "STOP"
    assert to_gemini_finish_reason(5) == "OTHER"
    assert to_gemini_finish_reason(6) == "LANGUAGE"
    assert to_gemini_finish_reason(15) == "IMAGE_OTHER"
    assert to_gemini_finish_reason(16) == "NO_IMAGE"
    assert to_gemini_finish_reason(17) == "IMAGE_RECITATION"
    assert to_gemini_finish_reason(18) == "MISSING_THOUGHT_SIGNATURE"


def test_clean_upstream_error_message():
    from aistudio_api.api.error_mapping import clean_upstream_error_message

    assert clean_upstream_error_message("") == ""
    assert clean_upstream_error_message("Normal plain error") == "Normal plain error"
    assert (
        clean_upstream_error_message("HTTP 429: rate limit") == "HTTP 429: rate limit"
    )
    jspb_nested = 'HTTP 400: [,[3,"Please enable tool_config.include_server_side_tool_invocations to use Built-in tools with Function calling.",[["type.googleapis.com/details"]]]]'
    assert (
        clean_upstream_error_message(jspb_nested)
        == "Please enable tool_config.include_server_side_tool_invocations to use Built-in tools with Function calling."
    )
    jspb_404 = 'HTTP 404: [,[5,"Requested entity was not found."]]'
    assert clean_upstream_error_message(jspb_404) == "Requested entity was not found."


@pytest.mark.asyncio
async def test_build_gemini_streaming_response_passes_requested_model():
    from unittest.mock import MagicMock

    from aistudio_api.api.gemini_service import (
        _build_gemini_streaming_response,
    )
    from aistudio_api.application.contracts.gemini import (
        GeminiContent,
        GeminiGenerateContentRequest,
        GeminiPart,
    )

    passed_model = None

    async def fake_stream_generate_content(*args, **kwargs):
        nonlocal passed_model
        passed_model = kwargs.get("model")
        yield ("body", "data: test")
        yield ("finish_reason", 1)
        yield ("usage", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    mock_client = MagicMock()
    mock_client.stream_generate_content = MagicMock(
        side_effect=fake_stream_generate_content
    )

    from tests.unit.routing_fakes import (
        FakeHandle,
        ScriptedPipeline,
        make_attempt,
        make_context,
    )

    attempt = make_attempt(mock_client)
    pipeline = ScriptedPipeline([attempt], context=make_context())
    req = GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hello")])]
    )
    resp = await _build_gemini_streaming_response(
        pipeline=pipeline,
        model_path="gemini-2.5-pro",
        req=req,
    )

    chunks = [chunk async for chunk in resp.body_iterator]
    assert len(chunks) > 0
    assert passed_model == "models/gemini-2.5-pro"
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True


@pytest.mark.asyncio
async def test_streaming_metadata_event_does_not_block_same_account_retry():
    """Metadata-only attempts still retry on the same instance before any body."""
    from unittest.mock import MagicMock

    from aistudio_api.api.gemini_service import (
        _build_gemini_streaming_response,
    )
    from aistudio_api.application.contracts.gemini import (
        GeminiContent,
        GeminiGenerateContentRequest,
        GeminiPart,
    )
    from aistudio_api.domain.errors import RequestError
    from tests.unit.routing_fakes import (
        FakeHandle,
        ScriptedPipeline,
        make_attempt,
        make_context,
    )

    attempt_count = 0

    async def fake_stream_generate_content(*args, **kwargs):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            yield ("thought_signature", "sig_early")
            raise RequestError(204, "No content")
        yield ("body", "Hello world after retry")
        yield ("finish_reason", 1)
        yield ("usage", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    mock_client = MagicMock()
    mock_client.stream_generate_content = MagicMock(
        side_effect=fake_stream_generate_content
    )
    mock_client.clear_templates = MagicMock()

    attempt = make_attempt(mock_client)
    pipeline = ScriptedPipeline(
        [attempt], actions=["retry_same"], context=make_context()
    )
    req = GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hello")])]
    )
    resp = await _build_gemini_streaming_response(
        pipeline=pipeline, model_path="gemini-3.7-flash", req=req
    )

    chunks = [chunk async for chunk in resp.body_iterator]
    assert attempt_count == 2
    assert any("Hello world after retry" in str(c) for c in chunks)
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True
