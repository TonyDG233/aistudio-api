"""Per-event streaming transformer for ATG (adapted to this repo's event stream).

Ported from Ark ``src/atg/streamExtractor.js`` semantics, but the interface is
this repository's parsed event stream rather than raw Gemini SSE chunks:

``process_event(event_type, text) -> list[tuple[str, object]]``

where the relevant ``event_type`` values are ``body`` (non-thought text chunk),
``thinking`` (thought text), ``tool_calls`` (list of function-call dicts),
``images`` / ``reasoning_images``. Other event types pass through untouched.

Modes (matching Ark):
  - ``side_buffer_bytes == 0``: immediate pass-through of plain text (true
    streaming); if a synthetic call arrives later, the already-flushed text is
    flagged as conflict.
  - ``side_buffer_bytes > 0``: plain text is buffered and only flushed at
    :meth:`finalize` if the stream ends without a synthetic hit.

Cross-chunk synthetic arguments are accumulated with
:class:`~aistudio_api.application.atg.stream_decoder.IncrementalJSONStringDecoder`
when a synthetic call (or a nameless continuation of one) carries raw string
arguments.
"""

from __future__ import annotations

from .config import AtgConfig
from .extractor import (
    AtgStats,
    SyntheticRequiredError,
    function_call_args,
)
from .stream_decoder import IncrementalJSONStringDecoder

_PASSTHROUGH_TEXT_EVENTS = ("thinking", "images", "reasoning_images")


class StreamUnwrapper:
    """Stateful per-stream unwrapper created once per wrapped request."""

    def __init__(self, cfg: AtgConfig, tool_name: str) -> None:
        self._cfg = cfg
        self._tool_name = tool_name
        self.stats = AtgStats()
        self._buffered_text: list[str] = []
        self._flushed_plain_text = False
        self._decoder: IncrementalJSONStringDecoder | None = None
        self._decoder_active = False

    @property
    def synthetic_hit(self) -> bool:
        return self.stats.synthetic_hit

    def should_remap_finish_reason(self) -> bool:
        """True when a consumed synthetic call leaves no real tool call behind."""
        return self.stats.synthetic_hit and self.stats.real_tool_count == 0

    def _content_from_synthetic_args(self, function_call: dict[str, object]) -> str:
        args = function_call_args(function_call)
        if isinstance(args, dict):
            content = args.get("content")
            return content if isinstance(content, str) else ""
        if isinstance(args, str):
            if self._decoder is None:
                self._decoder = IncrementalJSONStringDecoder()
            self._decoder_active = True
            return self._decoder.feed(args).output
        return ""

    def _process_tool_calls(self, text: object) -> list[tuple[str, object]]:
        if not isinstance(text, list):
            return [("tool_calls", text)]

        remaining: list[object] = []
        content_parts: list[str] = []

        for function_call in text:
            if not isinstance(function_call, dict):
                remaining.append(function_call)
                continue

            name = function_call.get("name")
            if name == self._tool_name:
                self.stats.synthetic_hit = True
                self.stats.synthetic_count += 1
                if self._buffered_text:
                    self.stats.conflict = True
                    self._buffered_text = []
                if self._flushed_plain_text:
                    self.stats.conflict = True
                content = self._content_from_synthetic_args(function_call)
                if content:
                    content_parts.append(content)
                continue

            if isinstance(name, str) and name:
                self.stats.real_tool_count += 1
                remaining.append(function_call)
                continue

            # Nameless continuation of an incrementally-streamed synthetic call.
            if self.stats.synthetic_hit:
                content = self._content_from_synthetic_args(function_call)
                if content:
                    content_parts.append(content)
                continue

            remaining.append(function_call)

        output: list[tuple[str, object]] = []
        if content_parts:
            output.append(("body", "".join(content_parts)))
        if remaining:
            output.append(("tool_calls", remaining))
        return output

    def process_event(self, event_type: str, text: object) -> list[tuple[str, object]]:
        if event_type == "tool_calls":
            return self._process_tool_calls(text)

        if event_type == "body":
            if self._cfg.side_buffer_bytes > 0:
                self._buffered_text.append(str(text))
                return []
            self._flushed_plain_text = True
            return [("body", text)]

        if event_type in _PASSTHROUGH_TEXT_EVENTS:
            return [(event_type, text)]

        return [(event_type, text)]

    def finalize(self) -> list[tuple[str, object]]:
        """Flush buffered output and enforce ``required`` mode.

        Returns the flush events (0..n) and raises
        :class:`SyntheticRequiredError` when ``required`` mode is unsatisfied.

        NOTE: the frozen interface sketch annotates this as ``-> None``; it
        returns the flush events because Ark's ``finish()`` does the same and the
        side-buffer semantics cannot be preserved otherwise (a buffered answer
        must still reach the client when no synthetic call arrives).
        """
        output: list[tuple[str, object]] = []

        if self._decoder_active and self._decoder is not None:
            tail = self._decoder.finish(self._cfg.max_response_bytes)
            if tail:
                output.append(("body", tail))
            self._decoder_active = False

        if self._buffered_text:
            buffered = "".join(self._buffered_text)
            self._buffered_text = []
            if not self.stats.synthetic_hit:
                output.append(("body", buffered))

        if (
            self._cfg.mode == "required"
            and not self.stats.synthetic_hit
            and self.stats.real_tool_count == 0
        ):
            raise SyntheticRequiredError(
                "synthetic tool call required but not present in upstream response"
            )

        return output


def create_stream_extractor(cfg: AtgConfig, tool_name: str) -> StreamUnwrapper:
    return StreamUnwrapper(cfg, tool_name)


__all__ = ["StreamUnwrapper", "create_stream_extractor"]
