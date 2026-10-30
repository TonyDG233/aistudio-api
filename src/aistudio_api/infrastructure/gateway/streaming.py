"""Streaming replay workflow for chat completions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path

from aistudio_api.config import settings
from aistudio_api.domain.errors import (
    BrowserTransportError,
    RequestError,
    classify_error,
)
from aistudio_api.infrastructure.gateway.capture import CapturedRequest
from aistudio_api.infrastructure.gateway.session_protocol import RequestSession
from aistudio_api.infrastructure.gateway.stream_parser import (
    IncrementalJSONStreamParser,
)
from aistudio_api.infrastructure.gateway.wire_parser import (
    WIRE_RESPONSE_ID_INDEX,
    parse_chunk_usage,
    parse_response_chunk,
)
from aistudio_api.infrastructure.gateway.wire_spec import (
    WIRE_FINISH_REASON_CONTINUING,
    WIRE_FINISH_REASON_STOP,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("streaming")

# Bounded idle window applied only after at least one complete ``tool_calls``
# event has been emitted and no finishReason has been seen yet.  This is NOT a
# global request/stream deadline: plain text streams stay infinite.
TOOL_STREAM_IDLE_FINISH_S = 2.0


def _dump_stream_exchange(
    *,
    model: str,
    url: str,
    modified_body: str,
    status_code: int,
    raw_response: str,
) -> None:
    if not settings.dump_raw_response:
        return

    out_dir = Path(settings.dump_raw_response_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_model = model.replace("/", "_")
    timestamp = __import__("time").strftime("%Y%m%d_%H%M%S")
    payload = {
        "kind": "stream_generate_content",
        "model": model,
        "url": url,
        "status_code": status_code,
        "modified_body": json.loads(modified_body),
        "raw_response": raw_response,
    }
    path = out_dir / f"aistudio_stream_generate_content_{safe_model}_{timestamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    logger.info("已落盘流式原始请求/响应: %s", path)


def _summarize_error_body(raw_response: str, limit: int = 500) -> str:
    text = raw_response.strip()
    if not text:
        return ""

    try:
        payload = json.loads(text)
        compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except json.JSONDecodeError:
        compact = " ".join(text.split())

    if len(compact) > limit:
        return compact[:limit] + "..."
    return compact


class StreamingGateway:
    def __init__(self, session: RequestSession | None = None):
        self._session = session

    async def stream_chat(
        self,
        *,
        captured: CapturedRequest,
        model: str,
    ) -> AsyncGenerator[tuple[str, object | None], None]:
        if not captured:
            raise ValueError("captured request is required")
        if self._session is None:
            raise RuntimeError("browser session is required for streaming xhr replay")
        modified_body = captured.body

        parser = IncrementalJSONStreamParser()
        latest_usage: dict[str, object] | None = None
        latest_finish_reason: int | None = None
        latest_response_id: str | None = None
        raw_parts: list[str] = []
        has_received_chunks = False
        has_tool_calls = False
        status_code = 0
        stream = self._session.send_streaming_request(
            body=modified_body,
            url=captured.url if captured else None,
            headers=captured.headers if captured else None,
        )
        try:
            while True:
                # Only tool-call streams that already emitted a complete
                # tool_calls event and have not seen a finish get a bounded
                # idle window.  Everything else keeps waiting indefinitely.
                idle_bounded = has_tool_calls and latest_finish_reason is None
                try:
                    if idle_bounded:
                        event_type, payload = await asyncio.wait_for(
                            stream.__anext__(), TOOL_STREAM_IDLE_FINISH_S
                        )
                    else:
                        event_type, payload = await stream.__anext__()
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    if not idle_bounded:
                        raise
                    logger.info(
                        "tool stream idle for %.1fs after tool_calls; "
                        "synthesizing STOP",
                        TOOL_STREAM_IDLE_FINISH_S,
                    )
                    break

                if event_type == "status" and payload and not status_code:
                    status_code = int(str(payload))
                elif event_type == "chunk" and payload:
                    has_received_chunks = True
                    if isinstance(payload, bytes):
                        text_payload = payload.decode("utf-8", errors="replace")
                    else:
                        text_payload = str(payload)
                    if settings.dump_raw_response or status_code != 200:
                        raw_parts.append(text_payload)
                    for parsed_chunk in parser.feed(text_payload):
                        usage = parse_chunk_usage(parsed_chunk)
                        if usage:
                            latest_usage = usage
                        if (
                            isinstance(parsed_chunk, list)
                            and len(parsed_chunk) > WIRE_RESPONSE_ID_INDEX
                            and isinstance(parsed_chunk[WIRE_RESPONSE_ID_INDEX], str)
                            and parsed_chunk[WIRE_RESPONSE_ID_INDEX]
                        ):
                            latest_response_id = str(
                                parsed_chunk[WIRE_RESPONSE_ID_INDEX]
                            )
                            yield ("response_id", latest_response_id)
                        candidate = parse_response_chunk(parsed_chunk)
                        if (
                            candidate.finish_reason is not None
                            and candidate.finish_reason != WIRE_FINISH_REASON_CONTINUING
                        ):
                            latest_finish_reason = candidate.finish_reason
                        if candidate.thinking:
                            yield ("thinking", candidate.thinking)
                        if candidate.reasoning_images:
                            yield ("reasoning_images", candidate.reasoning_images)
                        if candidate.function_calls:
                            has_tool_calls = True
                            yield ("tool_calls", candidate.function_calls)
                        if candidate.images:
                            yield ("images", candidate.images)
                        if candidate.text:
                            yield ("body", candidate.text)
                        if candidate.thought_signature:
                            yield ("thought_signature", candidate.thought_signature)
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    logger.debug("failed to close upstream stream", exc_info=True)
        raw_response = "".join(raw_parts)
        _dump_stream_exchange(
            model=model,
            url=captured.url,
            modified_body=modified_body,
            status_code=status_code,
            raw_response=raw_response,
        )
        if status_code != 200:
            detail = _summarize_error_body(raw_response)
            if (
                status_code in (401, 403, 429)
                or "[,[" in raw_response
                or "[null,[" in raw_response
                or raw_response.lstrip().startswith("[,")
            ):
                raise classify_error(status_code, raw_response)
            if detail:
                raise RequestError(status_code, detail)
            raise RequestError(status_code, "")
        if not has_received_chunks:
            raise BrowserTransportError(
                "stream ended before any data arrived (connection truncated)"
            )
        # A completed tool-call stream may end with ``done`` and no candidate
        # finishReason.  Synthesize STOP so clients do not wait for a terminal
        # event; never add a wall-clock timeout here.
        if latest_finish_reason is None and has_tool_calls:
            latest_finish_reason = WIRE_FINISH_REASON_STOP
        if latest_finish_reason is not None:
            yield ("finish_reason", latest_finish_reason)
        yield ("usage", latest_usage)
        yield ("done", None)
