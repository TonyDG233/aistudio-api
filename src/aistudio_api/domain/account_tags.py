"""Account tag normalization and the derived tier tag."""

from __future__ import annotations

from collections.abc import Iterable


def normalize_tag(value: object) -> str:
    return str(value or "").strip()


def normalize_tags(value: object) -> list[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, dict)):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in value:
        tag = normalize_tag(item)
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
    return tags


def tier_tag(tier: object) -> str | None:
    value = normalize_tag(tier)
    return value.casefold() if value else None


def account_tags(tags: object, tier: object) -> list[str]:
    """Custom tags plus the automatic tier tag, deduplicated case-insensitively."""
    derived: list[str] = []
    tier_value = tier_tag(tier)
    if tier_value:
        derived.append(tier_value)
    derived.extend(normalize_tags(tags))
    return normalize_tags(derived)


def tag_matches(allowed: object, tags: object) -> bool:
    allowed_tags = normalize_tags(allowed)
    if not allowed_tags:
        return True
    account_tag_keys = {tag.casefold() for tag in normalize_tags(tags)}
    return any(tag.casefold() in account_tag_keys for tag in allowed_tags)
