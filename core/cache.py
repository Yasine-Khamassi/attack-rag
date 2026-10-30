"""Small in-process caches (phase 6). Repeated work is the cheapest to remove: the same question embedded,
reranked or answered twice costs CPU time and API money for nothing.

TTLCache: least-recently-used eviction plus an age limit. Thread-safe. In-process only: a multi-process
deployment would put the answer cache in Postgres or Redis (same keys)."""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any


class TTLCache:
    def __init__(self, max_items: int = 1024, ttl_seconds: float = 86_400):
        self.max_items, self.ttl = max_items, ttl_seconds
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    def get(self, key: str):
        with self._lock:
            item = self._data.get(key)
            if item is None or time.monotonic() - item[0] > self.ttl:
                self._data.pop(key, None)
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return item[1]

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)


def key(*parts: Any) -> str:
    """Stable cache key from any mix of strings / numbers / iterables."""
    h = hashlib.sha256()
    for p in parts:
        items = p if isinstance(p, (list, tuple)) else [p]
        for item in items:
            h.update(str(item).encode("utf-8"))
            h.update(b"\x1f")
        h.update(b"\x1e")
    return h.hexdigest()[:32]


def normalize_question(text: str) -> str:
    """Questions that differ only in case, spacing or a trailing ? share a cache entry."""
    return " ".join(text.lower().split()).rstrip("?؟!. ")


def fingerprint(texts: Iterable[str]) -> str:
    """Changes whenever any indexed text changes: put it in answer-cache keys so new data invalidates them."""
    h = hashlib.sha256()
    for t in texts:
        h.update(hashlib.sha256(t.encode("utf-8")).digest())
    return h.hexdigest()[:16]
