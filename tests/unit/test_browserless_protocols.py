"""Four protocol faces generate/stream through BrowserlessSession (offline)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from aistudio_api.application.contracts.claude import ClaudeMessagesRequest
from aistudio_api.application.contracts.gemini import GeminiGenerateContentRequest
from aistudio_api.application.contracts.openai import OpenAIChatRequest
from aistudio_api.application.contracts.responses import ResponsesRequest
from aistudio_api.application.format_converters.claude_request import (
    convert_claude_request,
)
from aistudio_api.application.format_converters.media import MediaFetcher
from aistudio_api.application.format_converters.openai_request import (
    convert_openai_request,
)
from aistudio_api.application.format_converters.responses_request import (
    convert_responses_request,
)
from aistudio_api.application.generation_service import generate, stream_generation
from aistudio_api.application.routing_eligibility import ModelRoute
from aistudio_api.application.routing_pipeline import (
    RouteAttempt,
    RouteCandidate,
    RouteContext,
)
from aistudio_api.application.routing_safety import RoutingBudget
from aistudio_api.domain.instances import InstanceState
from aistudio_api.infrastructure.gateway.instance_client import InstanceRequestClient
from aistudio_api.infrastructure.gateway.wire_codec import AistudioWireCodec
from aistudio_api.infrastructure.waa.browserless_session import BrowserlessSession
from aistudio_api.infrastructure.waa.signer_client import MintProofResult, RpcResult

_MODEL = "gemini-3.7-flash"
_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "test_output.json"

_STREAM_FRAME = json.dumps(
    [
        [
            [
                [[[[[None, "Part 1 "]], "model"], 22]],
            ],
            [
                [[[[[None, "Part 2"]], "model"], 1]],
            ],
            [
                None,
                None,
                [15, 25, 40, 10, None, None, None, None, None, 5],
                None,
                None,
                None,
                None,
                "resp_final_99",
            ],
        ]
    ]
).encode("utf-8")


class FakeSigner:
    def __init__(self, body: bytes, stream_payload: bytes) -> None:
        self.body = body
        self.stream_payload = stream_payload
        self.mint_count = 0
        self.rpc_count = 0
        self.rpc_bodies: list[str] = []

    async def mint_proof(self, **kwargs: object) -> MintProofResult:
        self.mint_count += 1
        return MintProofResult(
            ok=True,
            proof="proof",
            digest=str(kwargs.get("digest") or ""),
            account_key="acct",
            latency_ms=1.0,
        )

    async def send_rpc(self, **kwargs: object) -> RpcResult:
        self.rpc_count += 1
        self.rpc_bodies.append(str(kwargs.get("body") or ""))
        return RpcResult(status=200, body=self.body)

    async def stream_rpc(self, **kwargs: object):
        self.rpc_count += 1
        yield ("status", 200)
        yield ("chunk", self.stream_payload)

    async def release_worker(
        self, *, account_key: str | None = None
    ) -> dict[str, object]:
        return {"ok": True}


class FakeHandle:
    slot = None

    def release(self) -> None:
        return None


class FakePipeline:
    def __init__(self, client: InstanceRequestClient) -> None:
        self._client = client
        self._attempt: RouteAttempt | None = None
        self.successes = 0

    async def begin(
        self, *, model_path: str, requested_surface: str | None = None
    ) -> RouteContext:
        candidate = RouteCandidate(
            instance_id="inst-1",
            account_id="acct",
            tier="Free",
            surface="chat",
            state=InstanceState.ACTIVE,
            in_flight=0,
            last_used=0.0,
            number=1,
        )
        route = ModelRoute(
            raw_model=model_path,
            model=model_path,
            surface="chat",
            paid=False,
            required_tiers=(),
            required_tier=None,
            tier="Free",
        )
        return RouteContext(
            route=route,
            candidates=[candidate],
            budget=RoutingBudget(),
            max_retries=0,
        )

    async def acquire(self, context: RouteContext) -> RouteAttempt | None:
        self._attempt = RouteAttempt(
            candidate=context.candidates[0],
            handle=cast(Any, FakeHandle()),
            client=cast(Any, self._client),
        )
        return self._attempt

    async def release(self, attempt: RouteAttempt) -> None:
        return None

    async def handle_error(
        self,
        context: RouteContext,
        attempt: RouteAttempt,
        exc: Exception,
        *,
        has_yielded_data: bool = False,
    ) -> str:
        return "raise"

    def record_success(self, *args: object, **kwargs: object) -> None:
        self.successes += 1

    def record_client(self, *args: object, **kwargs: object) -> None:
        return None

    def record_empty_retry(self, *args: object, **kwargs: object) -> None:
        return None


def _auth_file(tmp_path: Path) -> str:
    path = tmp_path / "acc.json"
    path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "SAPISID",
                        "value": "v",
                        "domain": ".google.com",
                        "path": "/",
                    }
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def _session(tmp_path: Path, signer: FakeSigner) -> BrowserlessSession:
    return BrowserlessSession(
        signer=cast(Any, signer),
        auth_file=_auth_file(tmp_path),
        tier="Free",
        account_id="acct",
        realm_id="acct-g1",
    )


async def _gemini_request() -> GeminiGenerateContentRequest:
    return GeminiGenerateContentRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
    )


async def _request_for(api_format: str) -> GeminiGenerateContentRequest:
    media = MediaFetcher()
    if api_format == "gemini":
        return await _gemini_request()
    if api_format == "openai":
        payload = OpenAIChatRequest.model_validate(
            {"model": _MODEL, "messages": [{"role": "user", "content": "hi"}]}
        )
        body = await convert_openai_request(payload, media)
    elif api_format == "claude":
        payload = ClaudeMessagesRequest.model_validate(
            {
                "model": _MODEL,
                "max_tokens": 64,
                "messages": [{"role": "user", "content": "hi"}],
            }
        )
        body = await convert_claude_request(payload, media)
    elif api_format == "responses":
        payload = ResponsesRequest.model_validate({"model": _MODEL, "input": "hi"})
        body = await convert_responses_request(payload, media)
    else:  # pragma: no cover - guard
        raise AssertionError(api_format)
    return GeminiGenerateContentRequest.model_validate(body)


@pytest.mark.asyncio
@pytest.mark.parametrize("api_format", ["gemini", "openai", "claude", "responses"])
async def test_four_protocols_non_stream(tmp_path: Path, api_format: str) -> None:
    signer = FakeSigner(_FIXTURE.read_bytes(), _STREAM_FRAME)
    session = _session(tmp_path, signer)
    client = InstanceRequestClient(
        session, account_id="acct", tier="Free", surface="chat"
    )
    pipeline = FakePipeline(client)

    outcome = await generate(
        model_path=_MODEL,
        req=await _request_for(api_format),
        pipeline=cast(Any, pipeline),
        api_format=api_format,
    )
    assert outcome.output.candidates
    assert outcome.output.candidates[0].text
    assert pipeline.successes == 1
    assert signer.mint_count >= 1
    assert signer.rpc_count >= 1
    wire_body = json.loads(signer.rpc_bodies[0])
    snapshot = wire_body[AistudioWireCodec.SNAPSHOT_INDEX]
    assert isinstance(snapshot, str) and snapshot.startswith("!")


@pytest.mark.asyncio
@pytest.mark.parametrize("api_format", ["gemini", "openai", "claude", "responses"])
async def test_four_protocols_stream(tmp_path: Path, api_format: str) -> None:
    signer = FakeSigner(_FIXTURE.read_bytes(), _STREAM_FRAME)
    session = _session(tmp_path, signer)
    client = InstanceRequestClient(
        session, account_id="acct", tier="Free", surface="chat"
    )
    pipeline = FakePipeline(client)

    events = [
        event
        async for event in stream_generation(
            model_path=_MODEL,
            req=await _request_for(api_format),
            pipeline=cast(Any, pipeline),
            api_format=api_format,
        )
    ]
    body_events = [event.payload for event in events if event.kind == "body"]
    assert "".join(str(item) for item in body_events).strip()
    assert events[-1].kind == "usage"
    assert pipeline.successes == 1
