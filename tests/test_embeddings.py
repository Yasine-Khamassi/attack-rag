"""Embedder logic without a model server: prefixes, batching, normalisation, dimension check."""

import io
import json

import numpy as np
import pytest

from core import embeddings
from core.embeddings import EmbeddingModel, OllamaEmbedder

MODEL = EmbeddingModel("fake", dim=2, query_prefix="q: ", doc_prefix="d: ")


@pytest.fixture
def calls(monkeypatch):
    """Fake /api/embed: returns [len(text), 1] per input and records every request body."""
    seen = []

    def fake_urlopen(req, timeout):
        body = json.loads(req.data)
        seen.append(body)
        dim = 3 if body["input"][0].endswith("BADDIM") else 2
        return io.BytesIO(json.dumps({"embeddings": [[len(t), 1.0, 0.0][:dim] for t in body["input"]]}).encode())

    monkeypatch.setattr(embeddings.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_prefixes_are_applied(calls):
    e = OllamaEmbedder(MODEL, url="http://x")
    e.embed_queries(["hi"])
    e.embed_documents(["doc"])
    assert calls[0]["input"] == ["q: hi"] and calls[1]["input"] == ["d: doc"]
    assert calls[0]["model"] == "fake" and calls[0]["truncate"] is True


def test_batches_and_normalises(calls):
    vecs = OllamaEmbedder(MODEL, url="http://x", batch_size=2).embed_documents(["a", "bb", "ccc"])
    assert [len(c["input"]) for c in calls] == [2, 1]
    assert vecs.shape == (3, 2)
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0)


def test_wrong_dimension_is_an_error(calls):
    with pytest.raises(ValueError, match="expected dim 2"):
        OllamaEmbedder(MODEL, url="http://x").embed_queries(["BADDIM"])


def test_known_models_have_dimensions():
    assert {m.dim for m in embeddings.MODELS.values()} <= {768, 1024}
