"""ATG (Anti-Truncation Gateway) facade.

Assembles config, injector, extractors and history purge into the single entry
point used by the Gemini application service. Ported from Ark
``src/atg/index.js``. The complete post-processing pipeline for the synthetic
transport tool is:

    wrap request  ->  upstream (AI Studio)  ->  unwrap stream/non-stream  ->  client

The synthetic tool name is a per-request value passed explicitly by the caller
(this repository has no ``res.__atgToolName`` side-channel); the facade holds no
global mutable state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aistudio_api.domain.models import ModelOutput

from .client_patterns import Detection
from .config import AtgConfig, load_atg_config
from .extractor import Extractor, UnwrapResult, create_extractor
from .history_purge import purge_synthetic_history
from .injector import InjectResult, create_injector
from .stream_extractor import StreamUnwrapper, create_stream_extractor


@dataclass
class WrapResult:
    body: dict[str, object]
    synthetic_hit: bool
    tool_name: str
    client_synthetic: Detection | None = None
    client_tools: list[str] = field(default_factory=list)


class AtgProcessor:
    """Per-request facade over config, injector and extractors."""

    def __init__(self, cfg: AtgConfig) -> None:
        self.cfg = cfg
        self.enabled = cfg.enabled
        self._injector = create_injector(cfg) if self.enabled else None
        self._extractor: Extractor | None = (
            create_extractor(cfg) if self.enabled else None
        )

    def is_synthetic_tool_name(self, name: object) -> bool:
        return isinstance(name, str) and name.startswith(self.cfg.tool_prefix)

    def wrap(self, body: object, model_name: str = "") -> WrapResult:
        """Wrap an outgoing Gemini request body; purge stale synthetic history first."""
        if not self.enabled or not isinstance(body, dict):
            return WrapResult(
                body=body if isinstance(body, dict) else {},
                synthetic_hit=False,
                tool_name="",
            )

        contents = body.get("contents")
        if isinstance(contents, list):
            body["contents"] = purge_synthetic_history(contents, self.cfg.tool_prefix)

        assert self._injector is not None
        result: InjectResult = self._injector.inject_gemini(body, model_name)
        return WrapResult(
            body=result.body,
            synthetic_hit=result.synthetic_hit,
            tool_name=result.tool_name,
            client_synthetic=result.client_synthetic,
            client_tools=result.client_tools,
        )

    def unwrap_response(self, output: ModelOutput, tool_name: str) -> UnwrapResult:
        """Unwrap a non-streaming :class:`ModelOutput`."""
        if not self.enabled or not tool_name or self._extractor is None:
            from .extractor import AtgStats

            return UnwrapResult(output=output, stats=AtgStats())
        return self._extractor.normalize_non_streaming(output, tool_name)

    def create_stream_unwrapper(self, tool_name: str) -> StreamUnwrapper:
        """Create a stateful per-stream unwrapper for one wrapped request."""
        return create_stream_extractor(self.cfg, tool_name)


def create_atg_processor(overrides: dict[str, object] | None = None) -> AtgProcessor:
    """Build an :class:`AtgProcessor` from the current (hot-reloadable) features."""
    return AtgProcessor(load_atg_config(overrides))


__all__ = [
    "AtgProcessor",
    "WrapResult",
    "create_atg_processor",
]
