"""Search keeps answering from keywords when the embedding service fails, if the caller allows it (D42)."""

import io
import json
import urllib.error

import pytest

from core import embeddings
from core.embeddings import EmbeddingModel
from core.search import HybridSearcher


def test_dense_failure_falls_back_to_keywords_only_when_allowed(capsys):
    s = object.__new__(HybridSearcher)

    def boom(*args, **kwargs):
        raise RuntimeError("rate limited")

    s.dense = boom
    s.dense_fallback = False
    with pytest.raises(RuntimeError):
        s._dense_or_nothing("q", 10)
    s.dense_fallback = True
    assert s._dense_or_nothing("q", 10) == []
    assert "keyword search only" in capsys.readouterr().err


def test_gateway_embedder_retries_a_rate_limit(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(req)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {"Retry-After": "1"}, None)
        return io.BytesIO(json.dumps({"data": [{"index": 0, "embedding": [1.0, 0.0]}]}).encode())

    monkeypatch.setattr(embeddings.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: None)
    monkeypatch.setenv("VERCEL_OIDC_TOKEN", "t")
    e = embeddings.GatewayEmbedder(EmbeddingModel("fake", dim=2, gateway="vendor/fake"))
    assert e.embed_queries(["a"]).shape == (1, 2) and len(calls) == 2
