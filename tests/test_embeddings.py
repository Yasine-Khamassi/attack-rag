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


def test_cloudflare_embedder_calls_workers_ai(monkeypatch):
    seen = []

    def fake_urlopen(req, timeout):
        seen.append(req)
        texts = json.loads(req.data)["text"]
        return io.BytesIO(json.dumps({"success": True, "result": {"shape": [len(texts), 2],
                                                                  "data": [[3.0, 4.0] for _ in texts]}}).encode())

    monkeypatch.setattr(embeddings.urllib.request, "urlopen", fake_urlopen)
    model = EmbeddingModel("fake", dim=2, query_prefix="q: ", cloudflare="@cf/fake/model")
    vecs = embeddings.CloudflareEmbedder(model, account_id="acc", token="tok", batch_size=2).embed_queries(
        ["a", "b", "c"])
    assert vecs.shape == (3, 2) and np.allclose(vecs[0], [0.6, 0.8])
    assert [json.loads(r.data)["text"] for r in seen] == [["q: a", "q: b"], ["q: c"]]
    assert seen[0].full_url == "https://api.cloudflare.com/client/v4/accounts/acc/ai/run/@cf/fake/model"
    assert seen[0].get_header("Authorization") == "Bearer tok"


def test_cloudflare_needs_a_hosted_model_and_credentials(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    with pytest.raises(ValueError, match="not hosted"):
        embeddings.CloudflareEmbedder(MODEL, account_id="a", token="t")
    with pytest.raises(RuntimeError, match="CLOUDFLARE_ACCOUNT_ID"):
        embeddings.CloudflareEmbedder("bge-m3")


def test_make_embedder_follows_the_environment(monkeypatch):
    monkeypatch.delenv("EMBEDDINGS_PROVIDER", raising=False)
    assert type(embeddings.make_embedder()) is OllamaEmbedder
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "cloudflare")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    assert type(embeddings.make_embedder()) is embeddings.CloudflareEmbedder
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "nope")
    with pytest.raises(ValueError, match="unknown EMBEDDINGS_PROVIDER"):
        embeddings.make_embedder()
