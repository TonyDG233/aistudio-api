"""Non-streaming response normalizer for ATG.

Ported from Ark ``src/atg/extractor.js`` (itself ported from
Antigravity-gateway ``internal/synthetic/normalizer.go``). Ark operates on the
Gemini REST ``candidates[].content.parts[]``; this repository's internal model
is :class:`~aistudio_api.domain.models.ModelOutput`. The mapping is:

  ===============================  ==========================================
  ModelOutput / Candidate field     Gemini part equivalent
  ===============================  ==========================================
  ``Candidate.text``                non-thought text part (``part.text``,
                                    ``part.thought !== true``)
  ``Candidate.thinking``            thought text part (``part.thought === true``)
  ``Candidate.function_calls``      ``functionCall`` parts (list of dicts)
  ``Candidate.function_responses``  ``functionResponse`` parts
  ``Candidate.images`` /            inlineData parts (non-thought / thought)
  ``Candidate.reasoning_images``
  ``Candidate.finish_reason``       wire int code (``1`` == STOP)
  ===============================  ==========================================

Intentional deviation from Ark's request handler: ``required`` mode raises an
explicit :class:`SyntheticRequiredError` instead of Ark's catch-and-passthrough,
so a missing synthetic call is never silently accepted (PLAN WP2 acceptance).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from aistudio_api.domain.models import ModelOutput

from .config import AtgConfig
from .repair import repair_synthetic_arguments

#: Ark compares ``candidate.finishReason === "TOOL_CALLS"``; the Gemini REST
#: string form. This repository carries the wire int code, so the rewrite lands
#: on ``1`` (STOP).
TOOL_CALLS_FINISH = "TOOL_CALLS"
STOP_WIRE_CODE = 1


class AtgError(Exception):
    """Base class for ATG processing failures surfaced explicitly to callers."""


class SyntheticArgumentsError(AtgError):
    """Synthetic tool arguments were not decodable into a string ``content``."""


class SyntheticRequiredError(AtgError):
    """``required`` mode: no synthetic call (and no real tool call) present."""


@dataclass
class AtgStats:
    """Unwrap statistics (field set matches Ark).

    Attributes use Python naming but :meth:`to_dict` exposes the Ark camelCase
    keys (``syntheticHit`` / ``syntheticCount`` / ``realToolCount`` / ``repaired``
    / ``conflict``) for logging and downstream (WP6) consumption.
    """

    synthetic_hit: bool = False
    synthetic_count: int = 0
    real_tool_count: int = 0
    repaired: bool = False
    conflict: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "syntheticHit": self.synthetic_hit,
            "syntheticCount": self.synthetic_count,
            "realToolCount": self.real_tool_count,
            "repaired": self.repaired,
            "conflict": self.conflict,
        }


@dataclass
class UnwrapResult:
    output: ModelOutput
    stats: AtgStats


def function_call_args(function_call: dict[str, object]) -> object:
    """Extract a function call's argument payload (``args`` or ``arguments``)."""
    if "args" in function_call:
        return function_call["args"]
    if "arguments" in function_call:
        return function_call["arguments"]
    return function_call.get("raw")


def extract_content_from_args(args: object, cfg: AtgConfig, stats: AtgStats) -> str:
    """Decode the string ``content`` out of a synthetic call's arguments.

    Object args must carry a string ``content`` field. String args are repaired
    deterministically unless ``recovery == "fail"``. Unrecoverable payloads
    raise :class:`SyntheticArgumentsError` (never silently dropped).
    """
    if isinstance(args, dict):
        content = args.get("content")
        if isinstance(content, str):
            return content
        try:
            rendered = json.dumps(args, ensure_ascii=False)[:200]
        except (TypeError, ValueError):
            rendered = str(args)[:200]
        raise SyntheticArgumentsError(
            "synthetic tool arguments must contain a string 'content' field, "
            f"got: {rendered}"
        )

    if isinstance(args, str):
        if cfg.recovery == "fail":
            raise SyntheticArgumentsError(
                "failed to decode synthetic tool arguments: recovery=fail"
            )
        result = repair_synthetic_arguments(args, cfg.max_response_bytes)
        if result.error is not None or not isinstance(result.content, str):
            raise SyntheticArgumentsError(
                f"failed to decode synthetic tool arguments: {result.error}"
            )
        stats.repaired = True
        return result.content

    raise SyntheticArgumentsError("synthetic tool call produced no parseable arguments")


def _is_tool_calls_finish(finish_reason: object) -> bool:
    return finish_reason == TOOL_CALLS_FINISH


class Extractor:
    """Per-response normalizer bound to a frozen :class:`AtgConfig`."""

    def __init__(self, cfg: AtgConfig) -> None:
        self._cfg = cfg

    def normalize_non_streaming(
        self, output: ModelOutput, tool_name: str
    ) -> UnwrapResult:
        cfg = self._cfg
        stats = AtgStats()

        if cfg.mode == "off" or not tool_name:
            return UnwrapResult(output=output, stats=stats)
        if not output.candidates:
            return UnwrapResult(output=output, stats=stats)

        for candidate in output.candidates:
            will_have_synthetic = any(
                fc.get("name") == tool_name for fc in candidate.function_calls
            )
            sees_standard_text = bool(candidate.text)
            if will_have_synthetic and sees_standard_text:
                stats.conflict = True

            remaining_calls: list[dict[str, object]] = []
            synthetic_texts: list[str] = []

            for fc in candidate.function_calls:
                if fc.get("name") == tool_name:
                    stats.synthetic_hit = True
                    stats.synthetic_count += 1
                    synthetic_texts.append(
                        extract_content_from_args(function_call_args(fc), cfg, stats)
                    )
                    continue
                stats.real_tool_count += 1
                remaining_calls.append(fc)

            candidate.function_calls = remaining_calls

            if will_have_synthetic:
                # Suppress the upstream non-thought text; the synthetic content is
                # the real answer.
                candidate.text = "".join(synthetic_texts)

            if (
                will_have_synthetic
                and _is_tool_calls_finish(candidate.finish_reason)
                and stats.real_tool_count == 0
            ):
                candidate.finish_reason = STOP_WIRE_CODE

        if (
            cfg.mode == "required"
            and not stats.synthetic_hit
            and stats.real_tool_count == 0
        ):
            raise SyntheticRequiredError(
                "synthetic tool call required but not present in upstream response"
            )

        return UnwrapResult(output=output, stats=stats)


def create_extractor(cfg: AtgConfig) -> Extractor:
    return Extractor(cfg)


__all__ = [
    "AtgError",
    "AtgStats",
    "Extractor",
    "SyntheticArgumentsError",
    "SyntheticRequiredError",
    "UnwrapResult",
    "create_extractor",
    "extract_content_from_args",
    "function_call_args",
]
