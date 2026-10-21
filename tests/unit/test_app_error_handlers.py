import pytest

from aistudio_api.api.app import (
    build_surface_exception_handler,
    surface_policy_exception_handler,
)
from aistudio_api.application.surface_orchestrator import SurfacePolicyError
from aistudio_api.infrastructure.gateway.build_surface import BuildSurfaceError


@pytest.mark.asyncio
async def test_surface_policy_error_maps_to_policy_status() -> None:
    exc = SurfacePolicyError(
        "surface_no_capable_account",
        message="no account",
        advice="fix config",
    )
    response = await surface_policy_exception_handler(None, exc)  # type: ignore[arg-type]
    assert response.status_code == 403
    assert b"surface_no_capable_account" not in response.body
    assert b"no account" in response.body


@pytest.mark.asyncio
async def test_build_not_ready_maps_to_503() -> None:
    exc = BuildSurfaceError("ensure_ready", "SURFACE_NOT_READY", "building")
    response = await build_surface_exception_handler(None, exc)  # type: ignore[arg-type]
    assert response.status_code == 503
    assert b"SURFACE_NOT_READY" in response.body
