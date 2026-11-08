"""Single source for ``GetAiStudioBenefitTier`` response parsing.

Both transports (page XHR in :mod:`...gateway.session_auth` and the direct HTTP
probe in :mod:`...account.account_info_refresher`) receive the same response
body shape, so body decoding and tier normalization live here exactly once.
Transport selection stays at the call sites.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from aistudio_api.domain.model_access import benefit_tier_from_code


@dataclass(frozen=True, slots=True)
class BenefitTierEnvelope:
    """Decoded BenefitTier body.

    ``code`` is the raw integer when the envelope carried one. ``tier`` is the
    canonical label from the domain table, or ``None`` for an unknown code.
    ``message`` mirrors the optional second envelope element used in error
    bodies (truncated to 300 characters).

    ``valid`` is true only for an unambiguous upstream tier answer: an empty
    body/bare ``null`` hole (explicit Free) or a known numeric code. Unknown
    codes, malformed JSON, non-list bodies, invalid element shapes and a
    ``null`` hole paired with a non-empty message leave it false so probes can
    return ``None`` instead of fabricating Free.
    """

    code: int | None
    tier: str | None
    message: str = ""
    valid: bool = False

    def strict_tier(self) -> str | None:
        """Return the explicit tier, or ``None`` when no valid answer exists.

        Unlike :meth:`display_tier`, this never maps an unknown code or an
        unparseable body to Free. Only an explicit upstream answer (including
        the documented empty/null Free encoding) produces a tier.
        """
        if not self.valid:
            return None
        return self.tier or "Free"

    def display_tier(self, *, include_unknown_code: bool = False) -> str:
        """Return the label policy for one call site.

        The cookie/session probes treat unknown codes as Free; the refresher
        surfaces ``unknown-<code>`` so operators can see a new upstream tier.
        """
        if self.tier is not None:
            return self.tier
        if include_unknown_code and self.code is not None:
            return f"unknown-{self.code}"
        return "Free"


def parse_benefit_tier_envelope(raw_data: object) -> BenefitTierEnvelope:
    """Parse a BenefitTier response body without any transport concerns.

    Accepts the shapes seen in practice: already-decoded lists, JSON text,
    ``)]}'``-prefixed jspb text, the ``[,`` jspb null-hole form, empty bodies,
    malformed text, scalar/envelope values and string/int/float codes. All of
    them degrade to an empty envelope instead of raising.
    """
    payload = raw_data
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith(")]}'"):
            text = text[4:].strip()
        if text.startswith("[,"):
            text = "[null," + text[2:]
        try:
            payload = json.loads(text)
        except Exception:
            return BenefitTierEnvelope(None, None)

    if not isinstance(payload, list):
        return BenefitTierEnvelope(None, None)

    message = ""
    if len(payload) >= 2 and isinstance(payload[1], str):
        message = payload[1][:300]

    if not payload:
        # Documented upstream shape for a Free account (no tier code carried).
        return BenefitTierEnvelope(None, None, message, valid=True)

    first = payload[0]
    if first is None:
        # A bare jspb null-hole/omitted first element encodes Free explicitly.
        # ``[null, "error message"]`` carries an error alongside the hole, so it
        # is not an authoritative Free answer.
        if message:
            return BenefitTierEnvelope(None, None, message)
        return BenefitTierEnvelope(None, None, message, valid=True)
    if isinstance(first, bool):
        return BenefitTierEnvelope(None, None, message)
    if isinstance(first, (int, float)):
        try:
            code = int(first)
        except (OverflowError, ValueError):
            return BenefitTierEnvelope(None, None, message)
        tier = benefit_tier_from_code(code, unknown="") or None
        return BenefitTierEnvelope(code, tier, message, valid=tier is not None)
    return BenefitTierEnvelope(None, None, message)


__all__ = [
    "BenefitTierEnvelope",
    "parse_benefit_tier_envelope",
]
