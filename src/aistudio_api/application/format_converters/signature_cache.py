"""Bounded, persistent cache for Gemini tool-call thought signatures.

OpenAI / Claude clients echo the tool call id back on the next turn but have no
field for Gemini's ``thoughtSignature``. The proxy caches the signature emitted
with each call and reattaches it when converting later history turns.

The cache is not one-shot: entries are read without being consumed and may be
reused by every later turn that replays the same tool call id. Entries are
persisted so a proxy restart does not break an ongoing conversation.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from pathlib import Path

from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("format.signature_cache")

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
