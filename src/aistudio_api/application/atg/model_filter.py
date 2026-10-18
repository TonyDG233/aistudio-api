"""Model classification filter for ATG.

Ported 1:1 from Ark ``src/atg/modelFilter.js`` (itself ported from
Antigravity-gateway ``internal/synthetic/model_filter.go``). A model is treated
as non-text when the non-text regex matches, otherwise as text (permissive
default). Custom patterns supplied via config replace the built-in defaults on a
best-effort basis (invalid regex falls back to the default, mirroring JS).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_NON_TEXT_PATTERN = (
    r"(image|vision-preview|audio|tts|stt|whisper|embed|embedding|video|flux|"
    r"dall-e|sdxl|diffusion|moderation)"
)
DEFAULT_TEXT_PATTERN = (
    r"(gpt|claude|gemini|deepseek|qwen|llama|mistral|gemma|chat|text|instruct|"
    r"reasoner|agent|plus|pro|flash|opus|sonnet|haiku|medium|low|high|lite|base|"
    r"code|coder)"
)

_DEFAULT_NON_TEXT_REGEX = re.compile(DEFAULT_NON_TEXT_PATTERN, re.IGNORECASE)
_DEFAULT_TEXT_REGEX = re.compile(DEFAULT_TEXT_PATTERN, re.IGNORECASE)


@dataclass(frozen=True)
class ModelFilter:
    """Text/non-text classification aligned with Ark's ``createModelFilter``."""

    text_pattern: str = DEFAULT_TEXT_PATTERN
    non_text_pattern: str = DEFAULT_NON_TEXT_PATTERN
    text_regex: re.Pattern[str] = _DEFAULT_TEXT_REGEX
    non_text_regex: re.Pattern[str] = _DEFAULT_NON_TEXT_REGEX

    def is_text_model(self, model_name: object) -> bool:
        trimmed = str(model_name or "").strip()
        if trimmed == "":
            return False
        if self.non_text_regex.search(trimmed):
            return False
        if self.text_regex.search(trimmed):
            return True
        return True


def create_model_filter(
    text_pattern: object = "", non_text_pattern: object = ""
) -> ModelFilter:
    """Build a :class:`ModelFilter`, falling back to defaults on invalid patterns.

    Mirrors Ark: a falsy custom pattern keeps the built-in default, and an
    invalid custom pattern silently falls back to the default too. Custom
    patterns are compiled without flags (matching ``new RegExp(pattern)``).
    """
    text_regex = _DEFAULT_TEXT_REGEX
    non_text_regex = _DEFAULT_NON_TEXT_REGEX
    text_source = DEFAULT_TEXT_PATTERN
    non_text_source = DEFAULT_NON_TEXT_PATTERN

    if text_pattern:
        candidate = str(text_pattern)
        try:
            text_regex = re.compile(candidate)
            text_source = candidate
        except re.error:
            pass

    if non_text_pattern:
        candidate = str(non_text_pattern)
        try:
            non_text_regex = re.compile(candidate)
            non_text_source = candidate
        except re.error:
            pass

    return ModelFilter(
        text_pattern=text_source,
        non_text_pattern=non_text_source,
        text_regex=text_regex,
        non_text_regex=non_text_regex,
    )


__all__ = [
    "DEFAULT_NON_TEXT_PATTERN",
    "DEFAULT_TEXT_PATTERN",
    "ModelFilter",
    "create_model_filter",
]
