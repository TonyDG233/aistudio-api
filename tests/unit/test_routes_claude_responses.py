"""C3 Wave 2 integration: Claude/Responses registration, auth, gates, defaults."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import yaml
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request

import aistudio_api.api.app as app_module
from aistudio_api.api import routes_system
from aistudio_api.api.app import app
from aistudio_api.api.dependencies import get_routing_pipeline
from aistudio_api.api.format_errors import format_for_path
from aistudio_api.api.state import RuntimeState
from aistudio_api.api.ui_sandbox import UISandbox
from aistudio_api.config import settings
from aistudio_api.domain.errors import (
    RequestError,
    SurfaceSwitchExhaustedError,
    TierRequiredError,
    UsageLimitExceeded,
)
from aistudio_api.domain.models import Candidate, ModelOutput
from aistudio_api.infrastructure.gateway import model_defaults
from tests.unit.routing_fakes import ScriptedPipeline, make_attempt, make_context

_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_HEADERS = {"x-goog-api-key": "test-key"}
_ALL_HEADERS = (
    {"x-api-key": "test-key"},
    {"Authorization": "Bearer test-key"},
    {"x-goog-api-key": "test-key"},
)
_CLAUDE_BODY = {
    "model": "models/gemini-3.7-flash",
    "max_tokens": 16,
    "messages": [{"role": "user", "content": "hi"}],
}
_RESPONSES_BODY = {"model": "models/gemini-3.7-flash", "input": "hi"}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _http_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("test", 1234),
            "server": ("test", 80),
        }
    )


def _json(response: Any) -> dict[str, Any]:
    if hasattr(response, "body"):
        return json.loads(bytes(response.body))
    return response.json()


def _outcome(text: str = "ok") -> ModelOutput:
    return ModelOutput(
        candidates=[Candidate(text=text)],
        usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    )


def _pipeline(text: str = "ok") -> ScriptedPipeline:
    fake_client = MagicMock()
    fake_client.generate_content = AsyncMock(return_value=_outcome(text))
    return ScriptedPipeline([make_attempt(fake_client)], context=make_context())


@pytest.fixture(autouse=True)
def _clean_overrides(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"test-key"}))
    monkeypatch.setattr(settings, "web_password", None)
    app.dependency_overrides[get_routing_pipeline] = lambda: object()
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Registration / path classification
# ---------------------------------------------------------------------------


def test_claude_and_responses_routers_are_registered() -> None:
    paths = app.openapi()["paths"]
    assert "/v1/messages" in paths
    assert "/v1/messages/count_tokens" in paths
    assert "/v1/responses" in paths
    assert "/v1/responses/input_tokens" in paths
    assert "post" in paths["/v1/messages"]
    assert "post" in paths["/v1/responses"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/v1/messages", "claude"),
        ("/v1/messages/count_tokens", "claude"),
        ("/v1/responses", "responses"),
        ("/v1/responses/input_tokens", "responses"),
        ("/v1/models", "openai"),
        ("/v1/models/gemini-3.7-flash", "openai"),
        ("/v1/chat/completions", "openai"),
        ("/v1beta/models", "gemini"),
        ("/v1beta/models/gemini:generateContent", "gemini"),
    ],
)
def test_format_for_path_four_protocols(path: str, expected: str) -> None:
    assert format_for_path(path) == expected


def test_app_protocol_resolver_uses_shared_classifier() -> None:
    assert app_module._protocol_for_request(_http_request("/v1/messages")) == "claude"
    assert app_module._protocol_for_request(_http_request("/v1/responses")) == "responses"
    assert app_module._protocol_for_request(_http_request("/v1/models")) == "openai"


# ---------------------------------------------------------------------------
# API key headers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("headers", _ALL_HEADERS)
async def test_messages_accepts_all_api_key_headers(headers) -> None:
    app.dependency_overrides[get_routing_pipeline] = lambda: _pipeline("claude-ok")
    async with _client() as client:
        response = await client.post("/v1/messages", headers=headers, json=_CLAUDE_BODY)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["type"] == "message"
    assert body["content"] == [{"type": "text", "text": "claude-ok"}]


@pytest.mark.parametrize("headers", _ALL_HEADERS)
async def test_responses_accepts_all_api_key_headers(headers) -> None:
    app.dependency_overrides[get_routing_pipeline] = lambda: _pipeline("responses-ok")
    async with _client() as client:
        response = await client.post(
            "/v1/responses", headers=headers, json=_RESPONSES_BODY
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["object"] == "response"
    assert body["output_text"] == "responses-ok"


@pytest.mark.parametrize("headers", _ALL_HEADERS)
async def test_v1_models_accepts_all_api_key_headers(headers) -> None:
    async with _client() as client:
        response = await client.get("/v1/models", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["object"] == "list"


# ---------------------------------------------------------------------------
# Error shapes
# ---------------------------------------------------------------------------


async def test_missing_key_401_uses_claude_and_responses_shapes() -> None:
    async with _client() as client:
        claude = await client.post("/v1/messages", json=_CLAUDE_BODY)
        responses = await client.post("/v1/responses", json=_RESPONSES_BODY)

    assert claude.status_code == 401
    assert claude.headers["WWW-Authenticate"] == "Bearer"
    claude_body = claude.json()
    assert claude_body["type"] == "error"
    assert claude_body["error"]["type"] == "authentication_error"

    assert responses.status_code == 401
    assert responses.headers["WWW-Authenticate"] == "Bearer"
    responses_body = responses.json()
    assert "type" not in responses_body
    assert responses_body["error"]["type"] == "authentication_error"
    assert set(responses_body["error"]) >= {"message", "type", "code", "param"}


async def test_missing_key_401_on_v1_models_stays_openai_shape() -> None:
    async with _client() as client:
        response = await client.get("/v1/models")
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["type"] == "authentication_error"
    assert "param" in error


async def test_claude_400_body_error_shape() -> None:
    async with _client() as client:
        response = await client.post(
            "/v1/messages",
            headers=_API_HEADERS,
            json={"model": "gemini-3.7-flash", "messages": []},
        )
    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert "max_tokens" in body["error"]["message"]


async def test_responses_400_body_error_shape() -> None:
    async with _client() as client:
        response = await client.post(
            "/v1/responses",
            headers=_API_HEADERS,
            json={"model": "gemini-3.7-flash"},
        )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert "input" in body["error"]["message"]


async def test_request_validation_400_and_http_422_shapes() -> None:
    validation = RequestValidationError(
        [{"loc": ("body", "input"), "msg": "field required", "type": "missing"}]
    )
    for path, expected_type in (
        ("/v1/messages", "invalid_request_error"),
        ("/v1/responses", "invalid_request_error"),
    ):
        response = await app_module.validation_exception_handler(
            _http_request(path), validation
        )
        assert response.status_code == 400
        body = _json(response)
        error = body["error"]
        assert error["type"] == expected_type

    for path in ("/v1/messages", "/v1/responses"):
        response = await app_module.http_exception_handler(
            _http_request(path), HTTPException(422, "unprocessable")
        )
        assert response.status_code == 422
        assert _json(response)["error"]["type"] == "invalid_request_error"


async def test_429_and_5xx_use_protocol_shapes() -> None:
    for path, is_claude in (("/v1/messages", True), ("/v1/responses", False)):
        limited = await app_module.aistudio_error_handler(
            _http_request(path), UsageLimitExceeded("quota exhausted")
        )
        assert limited.status_code == 429
        error = _json(limited)["error"]
        expected_type = "rate_limit_error" if is_claude else "rate_limit_exceeded"
        assert error["type"] == expected_type

        upstream = await app_module.aistudio_error_handler(
            _http_request(path), RequestError(502, "upstream down")
        )
        assert upstream.status_code == 502
        assert _json(upstream)["error"]["type"] == "api_error"

        if is_claude:
            assert _json(limited)["type"] == "error"


async def test_disabled_feature_returns_protocol_403(monkeypatch) -> None:
    monkeypatch.setattr(
        model_defaults,
        "get_features_config",
        lambda: {
            "formats": {"openai": True, "claude": False, "responses": False}
        },
    )
    async with _client() as client:
        claude = await client.post(
            "/v1/messages", headers=_API_HEADERS, json=_CLAUDE_BODY
        )
        responses = await client.post(
            "/v1/responses", headers=_API_HEADERS, json=_RESPONSES_BODY
        )

    assert claude.status_code == 403
    claude_body = claude.json()
    assert claude_body["type"] == "error"
    assert claude_body["error"]["type"] == "permission_error"
    assert "features.formats.claude" in claude_body["error"]["message"]

    assert responses.status_code == 403
    responses_body = responses.json()
    assert responses_body["error"]["type"] == "permission_error"
    assert "features.formats.responses" in responses_body["error"]["message"]


@pytest.mark.parametrize("path", ["/v1/messages", "/v1/responses"])
async def test_405_method_not_allowed_preserves_allow_header(path: str) -> None:
    async with _client() as client:
        response = await client.get(path)
    assert response.status_code == 405
    assert response.headers["Allow"] == "POST"
    error = response.json()["error"]
    assert error["message"]
    if path == "/v1/messages":
        assert response.json()["type"] == "error"


async def test_openai_405_allow_header_unchanged() -> None:
    async with _client() as client:
        response = await client.get("/v1/chat/completions")
    assert response.status_code == 405
    assert response.headers["Allow"] == "POST"
    assert response.json() == {"detail": "Method Not Allowed"}


async def test_unknown_protocol_paths_404_use_protocol_shapes() -> None:
    async with _client() as client:
        claude = await client.get("/v1/messages/unknown")
        responses = await client.get("/v1/responses/unknown")
    assert claude.status_code == 404
    assert claude.json()["type"] == "error"
    assert responses.status_code == 404
    assert "type" in responses.json()["error"]


# ---------------------------------------------------------------------------
# D-04/D-06 tier and routing
# ---------------------------------------------------------------------------


def _tier_error() -> TierRequiredError:
    return TierRequiredError(
        model="gemini-4-argon",
        tier="Pro",
        required_tier="Pro",
        current_tier="Free",
    )


@pytest.mark.parametrize("path", ["/v1/messages", "/v1/responses"])
async def test_tier_error_is_403_with_four_fields(path: str) -> None:
    response = await app_module.routing_error_handler(
        _http_request(path), _tier_error()
    )
    assert response.status_code == 403
    error = _json(response)["error"]
    assert error["tier"] == "Pro"
    assert error["model"] == "gemini-4-argon"
    assert error["required_tier"] == "Pro"
    assert error["current_tier"] == "Free"
    assert response.status_code not in (429, 503)


@pytest.mark.parametrize("path", ["/v1/messages", "/v1/responses"])
async def test_switch_exhaustion_is_503_and_never_429(path: str) -> None:
    response = await app_module.routing_error_handler(
        _http_request(path), SurfaceSwitchExhaustedError(surface="chat")
    )
    assert response.status_code == 503
    error = _json(response)["error"]
    assert "surface_switch_exhausted" in str(error)
    assert response.status_code != 429


# ---------------------------------------------------------------------------
# Non-stream / stream closed loops with a fake pipeline
# ---------------------------------------------------------------------------


async def test_claude_non_stream_closed_loop() -> None:
    app.dependency_overrides[get_routing_pipeline] = lambda: _pipeline("route-hello")
    async with _client() as client:
        response = await client.post(
            "/v1/messages", headers=_API_HEADERS, json=_CLAUDE_BODY
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model"] == "gemini-3.7-flash"
    assert body["content"] == [{"type": "text", "text": "route-hello"}]


async def test_responses_non_stream_closed_loop() -> None:
    app.dependency_overrides[get_routing_pipeline] = lambda: _pipeline("route-hello")
    async with _client() as client:
        response = await client.post(
            "/v1/responses", headers=_API_HEADERS, json=_RESPONSES_BODY
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["output_text"] == "route-hello"


def _stream_client() -> MagicMock:
    async def fake_stream(*_args, **_kwargs):
        yield ("body", "streamed")
        yield ("finish_reason", 1)
        yield (
            "usage",
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    fake_client = MagicMock()
    fake_client.stream_generate_content = fake_stream
    return fake_client


async def test_claude_stream_closed_loop() -> None:
    pipeline = ScriptedPipeline(
        [make_attempt(_stream_client())], context=make_context()
    )
    app.dependency_overrides[get_routing_pipeline] = lambda: pipeline
    async with _client() as client:
        response = await client.post(
            "/v1/messages",
            headers=_API_HEADERS,
            json={**_CLAUDE_BODY, "stream": True},
        )
    assert response.status_code == 200, response.text
    assert "event: message_start" in response.text
    assert "streamed" in response.text
    assert "event: message_stop" in response.text


async def test_responses_stream_closed_loop() -> None:
    pipeline = ScriptedPipeline(
        [make_attempt(_stream_client())], context=make_context()
    )
    app.dependency_overrides[get_routing_pipeline] = lambda: pipeline
    async with _client() as client:
        response = await client.post(
            "/v1/responses",
            headers=_API_HEADERS,
            json={**_RESPONSES_BODY, "stream": True},
        )
    assert response.status_code == 200, response.text
    assert "response.created" in response.text
    assert "streamed" in response.text
    assert "response.completed" in response.text


# ---------------------------------------------------------------------------
# Defaults and sandbox
# ---------------------------------------------------------------------------


def test_default_config_formats_all_true() -> None:
    defaults = yaml.safe_load(
        (_REPO_ROOT / "defaults" / "config.yaml").read_text(encoding="utf-8")
    )
    formats = defaults["features"]["formats"]
    assert formats["openai"] is True
    assert formats["claude"] is True
    assert formats["responses"] is True

    schema = json.loads(
        (_REPO_ROOT / "defaults" / "config.schema.json").read_text(encoding="utf-8")
    )
    schema_formats = schema["properties"]["features"]["properties"]["formats"]
    assert schema_formats["properties"]["claude"]["default"] is True
    assert schema_formats["properties"]["responses"]["default"] is True


def test_ui_sandbox_formats_default_all_on_and_toggle_is_explicit() -> None:
    sandbox = UISandbox()
    try:
        assert sandbox.formats_payload() == {
            "gemini": True,
            "openai": True,
            "claude": True,
            "responses": True,
        }
        closed = sandbox.apply(action="toggle_formats")
        assert closed["formats"] == {
            "gemini": True,
            "openai": False,
            "claude": False,
            "responses": False,
        }
        reopened = sandbox.apply(action="toggle_formats")
        assert reopened["formats"] == {
            "gemini": True,
            "openai": True,
            "claude": True,
            "responses": True,
        }
    finally:
        sandbox.close()


def test_system_status_formats_reflect_config() -> None:
    state = cast(RuntimeState, SimpleNamespace(ui_debug=False, ui_sandbox=None))
    assert routes_system.formats_status(state) == {
        "gemini": True,
        "openai": True,
        "claude": True,
        "responses": True,
    }


def test_system_status_format_fallbacks_default_true(monkeypatch) -> None:
    monkeypatch.setattr(model_defaults, "get_configured_formats", dict)
    state = cast(RuntimeState, SimpleNamespace(ui_debug=False, ui_sandbox=None))
    assert routes_system.formats_status(state) == {
        "gemini": True,
        "openai": True,
        "claude": True,
        "responses": True,
    }


# ---------------------------------------------------------------------------
# Existing Gemini/OpenAI error shapes are untouched
# ---------------------------------------------------------------------------


async def test_existing_gemini_and_openai_error_shapes_unchanged() -> None:
    async with _client() as client:
        gemini = await client.post(
            "/v1beta/gemini-3.7-flash:generateContent",
            json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        )
        openai = await client.post(
            "/v1/chat/completions",
            json={"model": "gemini-3.7-flash", "messages": []},
        )
    assert gemini.status_code == 401
    assert gemini.json()["error"]["status"] == "UNAUTHENTICATED"
    assert openai.status_code == 401
    assert openai.headers["WWW-Authenticate"] == "Bearer"
    assert openai.json()["error"]["type"] == "authentication_error"
