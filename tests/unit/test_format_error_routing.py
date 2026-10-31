"""D-04/D-06/D11-013: four-protocol 403 tier body and 503 routing separation."""

from __future__ import annotations

import pytest

from aistudio_api.api.error_mapping import error_payload
from aistudio_api.application.api_service_gemini import format_sse_error
from aistudio_api.domain.errors import (
    SurfaceCapacityError,
    SurfaceSwitchExhaustedError,
    TierRequiredError,
    UsageLimitExceeded,
)


def _tier_error() -> TierRequiredError:
    return TierRequiredError(
        model="gemini-4-argon",
        tier="Pro",
        required_tier="Pro",
        current_tier="Free",
    )


@pytest.mark.parametrize("protocol", ["gemini", "openai", "claude", "responses"])
def test_tier_403_has_four_fields_and_is_403(protocol) -> None:
    status, body = error_payload(_tier_error(), protocol)
    assert status == 403
    error = body.get("error")
    assert isinstance(error, dict)
    for field in ("tier", "model", "required_tier", "current_tier"):
        assert error[field] == {
            "tier": "Pro",
            "model": "gemini-4-argon",
            "required_tier": "Pro",
            "current_tier": "Free",
        }[field]
    assert status != 429 and status != 503


@pytest.mark.parametrize("protocol", ["gemini", "openai", "claude", "responses"])
def test_capacity_and_switch_exhaustion_are_503(protocol) -> None:
    for exc in (
        SurfaceCapacityError(surface="chat"),
        SurfaceCapacityError(surface="build"),
        SurfaceSwitchExhaustedError(surface="chat"),
    ):
        status, body = error_payload(exc, protocol)
        assert status == 503
        error = body.get("error")
        assert isinstance(error, dict)
        assert exc.code in (str(value) for value in error.values())


def test_429_is_never_tier_403() -> None:
    status, body = error_payload(UsageLimitExceeded("429"), "gemini")
    assert status == 429
    error = body.get("error")
    assert isinstance(error, dict)
    assert error["status"] == "RESOURCE_EXHAUSTED"
    assert "required_tier" not in error


def test_sse_tier_error_carries_fields() -> None:
    chunk = format_sse_error(_tier_error())
    assert "gemini-4-argon" in chunk
    assert "required_tier" in chunk
    assert "current_tier" in chunk
    assert "PERMISSION_DENIED" in chunk


class _TierRaisingPipeline:
    async def begin(self, *, model_path: str, requested_surface: str | None = None):
        del model_path, requested_surface
        raise _tier_error()


@pytest.mark.asyncio
async def test_openai_route_tier_403_keeps_four_fields() -> None:
    import httpx
    from fastapi import FastAPI

    from aistudio_api.api import routes_openai
    from aistudio_api.api.dependencies import get_routing_pipeline

    app = FastAPI()
    app.include_router(routes_openai.router)
    app.dependency_overrides[routes_openai.require_openai] = lambda: None
    app.dependency_overrides[get_routing_pipeline] = lambda: _TierRaisingPipeline()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gemini-4-argon",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 403
    error = response.json()["error"]
    assert error["type"] == "tier_required"
    assert error["code"] == "tier_required"
    assert error["tier"] == "Pro"
    assert error["model"] == "gemini-4-argon"
    assert error["required_tier"] == "Pro"
    assert error["current_tier"] == "Free"
    assert response.status_code not in (429, 503)
