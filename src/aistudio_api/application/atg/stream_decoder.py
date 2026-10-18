"""Incremental JSON string decoder for synthetic tool arguments.

Ported 1:1 from Ark ``src/atg/streamDecoder.js`` (itself ported from
Antigravity-gateway ``internal/synthetic/stream_decoder.go``). Operates on
byte-stream semantics: incoming chunks are turned into bytes, and decoded output
is emitted as UTF-8 text. The state machine walks a ``{"content": "..."}``-style
JSON envelope, streaming the string value out incrementally while tolerating
truncation in the middle of escapes, UTF-8 sequences and quotes.

Environment deviation from Ark's ``chunkToBytes``: Ark decodes the stream into a
latin1 JS byte string (code units <= 0xFF become single bytes); this repo feeds
already-decoded Python ``str``, so the whole chunk is UTF-8 encoded (see
``_chunk_to_bytes``). That keeps U+0080..U+00FF characters intact while the
observable state machine matches Ark exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

from .repair import repair_synthetic_arguments

_LEADING_WS = re.compile(r"^[ \t\r\n]+")
_HEX_PREFIX = re.compile(r"[0-9a-fA-F]+")

CONTENT_KEYS = (
    '"content"',
    '"text"',
    '"answer"',
    '"response"',
    '"result"',
    '"output"',
    '"message"',
)


class _State(IntEnum):
    Init = 0
    LookingForKey = 1
    LookingForColon = 2
    LookingForQuote = 3
    InString = 4
    Completed = 5
    Error = 6


@dataclass
class FeedResult:
    done: bool
    output: str


def _latin1(data: list[int]) -> str:
    return bytes(data).decode("latin-1")


def _utf8_encode(text: str) -> list[int]:
    # errors="replace" mirrors JS Buffer.from(str, "utf8") replacement behaviour
    # for lone surrogate code points.
    return list(text.encode("utf-8", errors="replace"))


def _utf8_decode(data: list[int]) -> str:
    return bytes(data).decode("utf-8", errors="replace")


def _strip_leading_ws(text: str) -> str:
    return _LEADING_WS.sub("", text, count=1)


def _utf16_is_high(code: int) -> bool:
    return 0xD800 <= code <= 0xDBFF


def _utf16_is_low(code: int) -> bool:
    return 0xDC00 <= code <= 0xDFFF


def _parse_js_hex(text: str) -> int | None:
    """Mimic JS ``parseInt(text, 16)`` (leading hex digits, NaN when none)."""
    match = _HEX_PREFIX.match(text)
    if match is None:
        return None
    return int(match.group(0), 16)


def _is_full_rune(data: list[int]) -> bool:
    if not data:
        return False
    b0 = data[0]
    if b0 < 0x80:
        return True
    if 0xC2 <= b0 <= 0xDF:
        return len(data) >= 2
    if 0xE0 <= b0 <= 0xEF:
        return len(data) >= 3
    if 0xF0 <= b0 <= 0xF4:
        return len(data) >= 4
    return True


def _decode_rune(data: list[int]) -> tuple[str, bool, int]:
    b0 = data[0]
    if b0 < 0x80:
        return chr(b0), False, 1
    if 0xC2 <= b0 <= 0xDF and len(data) >= 2 and (data[1] & 0xC0) == 0x80:
        cp = ((b0 & 0x1F) << 6) | (data[1] & 0x3F)
        return chr(cp), False, 2
    if (
        0xE0 <= b0 <= 0xEF
        and len(data) >= 3
        and (data[1] & 0xC0) == 0x80
        and (data[2] & 0xC0) == 0x80
    ):
        cp = ((b0 & 0x0F) << 12) | ((data[1] & 0x3F) << 6) | (data[2] & 0x3F)
        return chr(cp), False, 3
    if (
        0xF0 <= b0 <= 0xF4
        and len(data) >= 4
        and (data[1] & 0xC0) == 0x80
        and (data[2] & 0xC0) == 0x80
        and (data[3] & 0xC0) == 0x80
    ):
        cp = (
            ((b0 & 0x07) << 18)
            | ((data[1] & 0x3F) << 12)
            | ((data[2] & 0x3F) << 6)
            | (data[3] & 0x3F)
        )
        return chr(cp), False, 4
    return "", True, 1


class IncrementalJSONStringDecoder:
    """Stateful decoder streaming the ``content`` string out of a JSON envelope."""

    def __init__(self) -> None:
        self.state = _State.Init
        self._pending_escape: list[int] = []
        self._pending_utf8: list[int] = []
        self._pending_key = ""
        self._pending_quote = False
        self._surrogate_high = 0
        self._accum_raw = ""
        self._content_emitted = False

    def _chunk_to_bytes(self, chunk: str) -> list[int]:
        # Environment adaptation from Ark's ``chunkToBytes``: Ark receives a
        # latin1-decoded JS byte string (code units <= 0xFF are single bytes),
        # whereas this repo feeds already-decoded Python ``str``. Encoding the
        # whole chunk as UTF-8 reproduces the same underlying byte stream (and
        # keeps U+0080..U+00FF characters such as 'é' intact).
        return list(chunk.encode("utf-8", errors="replace"))

    def feed(self, chunk: str) -> FeedResult:
        if chunk == "":
            return FeedResult(done=self.state == _State.Completed, output="")

        self._accum_raw += chunk
        src = self._chunk_to_bytes(chunk)
        out: list[int] = []
        i = 0
        n = len(src)

        while i < n:
            state = self.state

            if state == _State.Init:
                b = src[i]
                if b == 0x7B:  # {
                    self.state = _State.LookingForKey
                    i += 1
                elif b == 0x22:  # "
                    self.state = _State.LookingForKey
                    self._pending_key += '"'
                    i += 1
                elif b in (0x20, 0x09, 0x0D, 0x0A, 0x60):  # ws or `
                    i += 1
                else:
                    self.state = _State.InString
                continue

            if state == _State.LookingForKey:
                self._pending_key += chr(src[i])
                i += 1
                key_str = self._pending_key
                if any(key in key_str for key in CONTENT_KEYS):
                    self.state = _State.LookingForColon
                    self._pending_key = ""
                elif '":' in key_str or '": ' in key_str:
                    self.state = _State.LookingForQuote
                    self._pending_key = ""
                elif len(key_str) > 500:
                    self.state = _State.InString
                continue

            if state == _State.LookingForColon:
                b = src[i]
                if b == 0x3A:  # :
                    self.state = _State.LookingForQuote
                    i += 1
                elif b in (0x20, 0x09, 0x0D, 0x0A):
                    i += 1
                elif b == 0x22:
                    self.state = _State.InString
                    i += 1
                else:
                    self.state = _State.InString
                continue

            if state == _State.LookingForQuote:
                b = src[i]
                if b == 0x22:
                    self.state = _State.InString
                    i += 1
                elif b in (0x20, 0x09, 0x0D, 0x0A):
                    i += 1
                else:
                    self.state = _State.InString
                continue

            if state == _State.Completed:
                trimmed = _latin1(src[i:]).strip()
                if trimmed not in ("", "}", "},", "}\n"):
                    self.state = _State.InString
                    continue
                i = n
                continue

            if state == _State.Error:
                i = n
                continue

            # state == InString
            if self._pending_quote:
                self._pending_quote = False
                trimmed_remaining = _strip_leading_ws(_latin1(src[i:]))
                if trimmed_remaining.startswith("}") or trimmed_remaining == "":
                    self.state = _State.Completed
                    i = n
                    continue
                out.append(0x22)

            if self._pending_escape:
                working = self._pending_escape + src[i:]
                self._pending_escape = []
            elif self._pending_utf8:
                working = self._pending_utf8 + src[i:]
                self._pending_utf8 = []
            else:
                working = src[i:]

            w = 0
            wn = len(working)
            while w < wn:
                b = working[w]

                if b == 0x5C:  # backslash
                    if w + 1 >= wn:
                        self._pending_escape = working[w:]
                        w = wn
                        break
                    nxt = working[w + 1]
                    if nxt == 0x22:
                        out.append(0x22)
                        w += 2
                    elif nxt == 0x5C:
                        out.append(0x5C)
                        w += 2
                    elif nxt == 0x2F:
                        out.append(0x2F)
                        w += 2
                    elif nxt == 0x62:
                        out.append(0x08)
                        w += 2
                    elif nxt == 0x66:
                        out.append(0x0C)
                        w += 2
                    elif nxt == 0x6E:
                        out.append(0x0A)
                        w += 2
                    elif nxt == 0x72:
                        out.append(0x0D)
                        w += 2
                    elif nxt == 0x74:
                        out.append(0x09)
                        w += 2
                    elif nxt == 0x75:  # \uXXXX
                        if w + 6 > wn:
                            self._pending_escape = working[w:]
                            w = wn
                            break
                        hex_str = _latin1(working[w + 2 : w + 6])
                        code = _parse_js_hex(hex_str)
                        if code is None:
                            out.extend(_utf8_encode("\\u" + hex_str))
                        elif _utf16_is_high(code):
                            self._surrogate_high = code
                        elif _utf16_is_low(code) and self._surrogate_high != 0:
                            combined = (
                                0x10000
                                + ((self._surrogate_high - 0xD800) << 10)
                                + (code - 0xDC00)
                            )
                            out.extend(_utf8_encode(chr(combined)))
                            self._surrogate_high = 0
                        else:
                            out.extend(_utf8_encode(chr(code)))
                        w += 6
                    else:
                        out.append(b)
                        out.append(nxt)
                        w += 2
                elif b == 0x22:  # closing quote
                    remaining_after_quote = working[w + 1 :]
                    trimmed_rest = _strip_leading_ws(_latin1(remaining_after_quote))
                    if len(remaining_after_quote) == 0:
                        self._pending_quote = True
                        w += 1
                        break
                    if trimmed_rest.startswith("}"):
                        self.state = _State.Completed
                        w = wn
                        break
                    out.append(0x22)
                    w += 1
                elif b < 0x80:
                    out.append(b)
                    w += 1
                else:
                    if not _is_full_rune(working[w:]):
                        self._pending_utf8 = working[w:]
                        w = wn
                        break
                    rune_char, rune_error, rune_size = _decode_rune(working[w:])
                    if rune_error:
                        out.append(b)
                        w += 1
                    else:
                        out.extend(_utf8_encode(rune_char))
                        w += rune_size

            i = n
            continue

        if out:
            self._content_emitted = True

        return FeedResult(
            done=self.state == _State.Completed,
            output=_utf8_decode(out) if out else "",
        )

    def finish(self, max_bytes: int) -> str:
        out: list[int] = []

        if self._pending_quote and not self._content_emitted:
            out.append(0x22)
            self._pending_quote = False

        if self._pending_escape:
            out.extend(self._pending_escape)
            self._pending_escape = []
        if self._pending_utf8:
            out.extend(self._pending_utf8)
            self._pending_utf8 = []

        if not self._content_emitted and self._accum_raw != "":
            res = repair_synthetic_arguments(self._accum_raw, max_bytes)
            if res.error is None and res.content not in (None, ""):
                return res.content

        return _utf8_decode(out) if out else ""


__all__ = [
    "CONTENT_KEYS",
    "FeedResult",
    "IncrementalJSONStringDecoder",
]
