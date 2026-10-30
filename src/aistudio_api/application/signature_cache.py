"""Bounded, persistent cache for Gemini tool-call thought signatures.

Gemini requires a ``thoughtSignature`` on historical model ``functionCall``
parts.  Clients that replay history without the signature (OpenAI / Claude
adapters and SillyTavern presets that rebuild the tool channel) can recover the
real signature from this cache, keyed by tool-call id.

The cache is not one-shot: entries are read without being consumed and may be
reused by every later turn that replays the same tool call id.  Entries are
persisted so a proxy restart does not break an ongoing conversation.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from pathlib import Path

from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("application.signature_cache")

# Placeholder accepted by upstream for Gemini 3 functionCall validation when
# the real signature is not available.  Real cached signatures are preferred.
DUMMY_THOUGHT_SIGNATURE = "context_engineering_is_the_way_to_go"

_DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60


class ThoughtSignatureCache:
    def __init__(
        self,
        *,
        max_entries: int = 4096,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        path: str | Path | None = None,
    ) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._lock = threading.Lock()
        self._path = Path(path) if path is not None else None
        self._path_resolved = path is not None
        self._loaded = False

    def configure_path(self, path: str | Path | None) -> None:
        """Point the cache at a new file (used by tests and explicit wiring)."""
        with self._lock:
            self._path = Path(path) if path is not None else None
            self._path_resolved = True
            self._loaded = False
            self._entries.clear()

    def put(self, call_id: str, signature: str) -> None:
        if not call_id or not signature:
            return
        now = time.time()
        with self._lock:
            self._ensure_loaded_locked()
            self._entries[call_id] = (now, signature)
            self._entries.move_to_end(call_id)
            self._prune(now)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            self._save_locked()

    def get(self, call_id: str) -> str | None:
        if not call_id:
            return None
        now = time.time()
        with self._lock:
            self._ensure_loaded_locked()
            entry = self._entries.get(call_id)
            if entry is None:
                return None
            created, signature = entry
            if now - created > self._ttl_seconds:
                self._entries.pop(call_id, None)
                return None
            self._entries.move_to_end(call_id)
            return signature

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._loaded = True

    def _ensure_loaded_locked(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._path_resolved:
            self._path_resolved = True
            try:
                from aistudio_api.config import resolve_data_dir

                self._path = resolve_data_dir() / "signature_cache.json"
            except Exception:
                self._path = None
        if self._path is None or not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("failed to load thought signature cache: %s", exc)
            return
        if not isinstance(data, dict):
            return
        now = time.time()
        for key, value in data.items():
            if (
                not isinstance(key, str)
                or not isinstance(value, list)
                or len(value) != 2
            ):
                continue
            created, signature = value
            if not isinstance(created, (int, float)) or not isinstance(signature, str):
                continue
            if now - created > self._ttl_seconds:
                continue
            self._entries[key] = (float(created), signature)
        self._prune(now)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def _save_locked(self) -> None:
        if self._path is None:
            return
        payload = {
            key: [created, signature]
            for key, (created, signature) in self._entries.items()
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except OSError as exc:
            logger.warning("failed to persist thought signature cache: %s", exc)

    def _prune(self, now: float) -> None:
        expired = [
            key
            for key, (created, _signature) in self._entries.items()
            if now - created > self._ttl_seconds
        ]
        for key in expired:
            self._entries.pop(key, None)


signature_cache = ThoughtSignatureCache()


def record_function_call_signatures(function_calls: object) -> None:
    """Persist real thought signatures keyed by tool-call id.

    Never logs the signature or any tool content.  Empty ids/signatures are
    ignored so a synthetic/nameless call cannot pollute the cache.
    """
    if not isinstance(function_calls, list):
        return
    for call in function_calls:
        if not isinstance(call, dict):
            continue
        call_id = call.get("id") or call.get("call_id")
        signature = call.get("thought_signature") or call.get("thoughtSignature")
        if (
            isinstance(call_id, str)
            and call_id
            and isinstance(signature, str)
            and signature
        ):
            signature_cache.put(call_id, signature)


__all__ = [
    "DUMMY_THOUGHT_SIGNATURE",
    "ThoughtSignatureCache",
    "record_function_call_signatures",
    "signature_cache",
]
