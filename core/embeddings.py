"""Text -> vectors. Local models served by Ollama (free, data never leaves the machine), or the same model hosted
by Cloudflare Workers AI for servers without a GPU (DECISIONS D41).

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
    cloudflare: str = ""  # the same model on Workers AI, if it is hosted there


# Prefixes from each model card.
MODELS = {
    m.name: m
    for m in [
        EmbeddingModel("bge-m3", 1024, cloudflare="@cf/baai/bge-m3"),
        EmbeddingModel("snowflake-arctic-embed2", 1024, query_prefix="query: "),
        EmbeddingModel("embeddinggemma", 768,
                       query_prefix="task: search result | query: ", doc_prefix="title: none | text: "),
    ]
}
DEFAULT_MODEL = "bge-m3"  # chosen by the step 3b probe (DECISIONS D10); re-check on the phase 2 golden set
# 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 (::1) first, Ollama listens on IPv4 only, and every
# request waited ~2 s for that attempt to fail (DECISIONS D40). Override with OLLAMA_URL.
OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"


class Embedder:
    """Prefixes, normalisation, dimension check and the query cache. Subclasses only implement _request()."""

    def __init__(self, model: str | EmbeddingModel, batch_size: int = 16, timeout: float = 600):
        self.model = MODELS[model] if isinstance(model, str) else model
        self.batch_size = batch_size
        self.query_cache = TTLCache(max_items=4096, ttl_seconds=7 * 86_400)
        self.timeout = timeout

    def _request(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def _embed(self, texts: Sequence[str]) -> np.ndarray:
        out = []
        for i in range(0, len(texts), self.batch_size):
            out.extend(self._request(list(texts[i:i + self.batch_size])))
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


class OllamaEmbedder(Embedder):
    def __init__(self, model: str | EmbeddingModel, url: str | None = None, batch_size: int = 16,
                 timeout: float = 600):
        super().__init__(model, batch_size, timeout)
        self.url = (url or os.environ.get("OLLAMA_URL", OLLAMA_DEFAULT_URL)).rstrip("/")

    def _request(self, texts: list[str]) -> list[list[float]]:
        body = json.dumps({"model": self.model.name, "input": texts, "truncate": True}).encode("utf-8")
        req = urllib.request.Request(f"{self.url}/api/embed", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.load(resp)["embeddings"]


class CloudflareEmbedder(Embedder):
    """The same model run by Cloudflare Workers AI. Needs CLOUDFLARE_ACCOUNT_ID and an API token with the
    Workers AI permission in CLOUDFLARE_API_TOKEN. The token is sent only to api.cloudflare.com."""

    API = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"

    def __init__(self, model: str | EmbeddingModel, account_id: str | None = None, token: str | None = None,
                 batch_size: int = 32, timeout: float = 60):
        super().__init__(model, batch_size, timeout)
        if not self.model.cloudflare:
            raise ValueError(f"{self.model.name} is not hosted on Workers AI")
        self.account_id = account_id or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        self.token = token or os.environ.get("CLOUDFLARE_API_TOKEN", "")
        if not self.account_id or not self.token:
            raise RuntimeError("set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN to use Workers AI embeddings")

    def _request(self, texts: list[str]) -> list[list[float]]:
        req = urllib.request.Request(
            self.API.format(account=self.account_id, model=self.model.cloudflare),
            data=json.dumps({"text": texts}).encode("utf-8"),
            # Cloudflare refuses some default client signatures; name ourselves.
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}",
                     "User-Agent": "rag-core-embeddings/1"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.load(resp)
        if not payload.get("success", True):
            raise RuntimeError(f"Workers AI error: {payload.get('errors')}")
        return payload["result"]["data"]


def make_embedder(model: str | EmbeddingModel = DEFAULT_MODEL) -> Embedder:
    """The embedder this machine should use: EMBEDDINGS_PROVIDER=cloudflare on servers without the local model,
    otherwise Ollama."""
    provider = os.environ.get("EMBEDDINGS_PROVIDER", "ollama").strip().lower()
    if provider == "cloudflare":
        return CloudflareEmbedder(model)
    if provider != "ollama":
        raise ValueError(f"unknown EMBEDDINGS_PROVIDER {provider!r} (ollama or cloudflare)")
    return OllamaEmbedder(model)
