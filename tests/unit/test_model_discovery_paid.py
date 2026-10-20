import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.config import settings
from aistudio_api.infrastructure.gateway.model_discovery import ModelDiscoveryService


def _raw_model(model_id: str, *, access_tier: int | None) -> list[object]:
    item: list[object] = [None] * 84
    item[0] = f"models/{model_id}"
    item[3] = model_id
    item[4] = f"{model_id} description"
    item[5] = 1048576
    item[6] = 8192
    item[7] = ["generateContent", "countTokens"]
    if access_tier is not None:
        item[77] = access_tier
    return item


def test_parse_raw_models_marks_paid_tier() -> None:
    service = ModelDiscoveryService()
    models = service._parse_raw_models(
        [
            [
                _raw_model("paid-model", access_tier=2),
                _raw_model("free-model", access_tier=1),
                _raw_model("unknown-model", access_tier=None),
            ]
        ]
    )

    by_id = {str(item["id"]): item for item in models}
    assert by_id["paid-model"]["paid"] is True
    assert by_id["paid-model"]["access_tier"] == 2
    assert by_id["free-model"]["paid"] is False
    assert by_id["free-model"]["access_tier"] == 1
    assert by_id["unknown-model"]["paid"] is False
    assert by_id["unknown-model"]["access_tier"] is None


@pytest.mark.asyncio
async def test_model_api_does_not_expose_paid_marker(monkeypatch) -> None:
    from aistudio_api.infrastructure.gateway import model_discovery

    async def _models(**_kwargs):
        return [
            {
                "id": "paid-demo",
                "displayName": "Paid Demo",
                "supportedGenerationMethods": ["generateContent"],
                "paid": True,
                "access_tier": 2,
            }
        ]

    monkeypatch.setattr(settings, "api_keys", frozenset())
    monkeypatch.setattr(model_discovery.model_discovery, "get_models", _models)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1beta/models")

    assert response.status_code == 200
    first = response.json()["models"][0]
    assert "paid" not in first
    assert "access_tier" not in first
