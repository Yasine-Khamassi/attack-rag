"""Text -> vectors. Local models served by Ollama (free, data never leaves the machine).

Many retrieval models are trained with different prefixes for queries and documents; using the wrong one
(or none) silently costs accuracy. So each model's prefixes live next to its name, and callers only ever
say "embed these documents" or "embed this query".
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from core.cache import TTLCache


@dataclass(frozen=True)
class EmbeddingModel:
    name: str            # Ollama model tag
    dim: int
    query_prefix: str = ""
    doc_prefix: str = ""


# Prefixes from each model card.
MODELS = {
    m.name: m
    for m in [
        EmbeddingModel("bge-m3", 1024),
        EmbeddingModel("snowflake-arctic-embed2", 1024, query_prefix="query: "),
        EmbeddingModel("embeddinggemma", 768,
                       query_prefix="task: search result | query: ", doc_prefix="title: none | text: "),
    ]
}
DEFAULT_MODEL = "bge-m3"  # chosen by the step 3b probe (DECISIONS D10); re-check on the phase 2 golden set
# 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 (::1) first, Ollama listens on IPv4 only, and every
# request waited ~2 s for that attempt to fail (DECISIONS D40). Override with OLLAMA_URL.
OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"


class OllamaEmbedder:
    def __init__(self, model: str | EmbeddingModel, url: str | None = None, batch_size: int = 16,
                 timeout: float = 600):
        self.model = MODELS[model] if isinstance(model, str) else model
        self.url = (url or os.environ.get("OLLAMA_URL", OLLAMA_DEFAULT_URL)).rstrip("/")
        self.batch_size = batch_size
        self.query_cache = TTLCache(max_items=4096, ttl_seconds=7 * 86_400)
        self.timeout = timeout

    def _embed(self, texts: Sequence[str]) -> np.ndarray:
        out = []
        for i in range(0, len(texts), self.batch_size):
            body = json.dumps({"model": self.model.name, "input": list(texts[i:i + self.batch_size]),
                               "truncate": True}).encode("utf-8")
            req = urllib.request.Request(f"{self.url}/api/embed", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                out.extend(json.load(resp)["embeddings"])
        vectors = np.asarray(out, dtype=np.float32)
        if vectors.shape[1] != self.model.dim:
            raise ValueError(f"{self.model.name}: expected dim {self.model.dim}, got {vectors.shape[1]}")
        # L2-normalise so a dot product is cosine similarity, whatever the server returns.
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return self._embed([self.model.doc_prefix + t for t in texts])

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        """Query vectors are cached in memory (phase 6): a repeated question skips the embedding request."""
        missing = [t for t in dict.fromkeys(texts) if self.query_cache.get(t) is None]
        if missing:
            for t, v in zip(missing, self._embed([self.model.query_prefix + t for t in missing])):
                self.query_cache.put(t, v)
        return np.stack([self.query_cache.get(t) for t in texts])
