"""Wire-code to Gemini finish-reason mapping (framework-free)."""

from __future__ import annotations

WIRE_TO_GEMINI_FINISH_REASON: dict[int, str] = {
    0: "FINISH_REASON_UNSPECIFIED",
    1: "STOP",
    2: "MAX_TOKENS",
    3: "SAFETY",
    4: "RECITATION",
    5: "OTHER",
    6: "LANGUAGE",
    7: "BLOCKLIST",
    8: "PROHIBITED_CONTENT",
    9: "SPII",
    10: "MALFORMED_FUNCTION_CALL",
    11: "IMAGE_SAFETY",
    12: "UNEXPECTED_TOOL_CALL",
    13: "TOO_MANY_TOOL_CALLS",
    14: "IMAGE_PROHIBITED_CONTENT",
    15: "IMAGE_OTHER",
    16: "NO_IMAGE",
    17: "IMAGE_RECITATION",
    18: "MISSING_THOUGHT_SIGNATURE",
}


def to_gemini_finish_reason(wire_code: int | None) -> str:
    if wire_code is None:
        return "STOP"
    return WIRE_TO_GEMINI_FINISH_REASON.get(wire_code, "FINISH_REASON_UNSPECIFIED")


__all__ = ["WIRE_TO_GEMINI_FINISH_REASON", "to_gemini_finish_reason"]
