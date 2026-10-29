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


class OllamaEmbedder:
    def __init__(self, model: str | EmbeddingModel, url: str | None = None, batch_size: int = 16,
                 timeout: float = 600):
        self.model = MODELS[model] if isinstance(model, str) else model
        self.url = (url or os.environ.get("OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.batch_size = batch_size
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
        return self._embed([self.model.query_prefix + t for t in texts])
