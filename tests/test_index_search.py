"""Index + hybrid search against a real Postgres, with a fake embedder (no model server needed).
Runs in its own Postgres schema so it never touches the real index."""

import hashlib
import uuid

import numpy as np
import pytest

from core import db
from core.embeddings import EmbeddingModel, OllamaEmbedder
from core.index import index_documents
from core.schema import Document
from core.search import HybridSearcher, rrf
from core.text import tokenize

DIM = 16


class FakeEmbedder(OllamaEmbedder):
    """Bag of hashed tokens: texts sharing words get similar vectors. Deterministic, instant."""

    def __init__(self):
        super().__init__(EmbeddingModel("fake-test", DIM))
        self.calls = 0

    def _embed(self, texts):
        self.calls += len(texts)
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in tokenize(t):
                out[i, int(hashlib.md5(tok.encode()).hexdigest(), 16) % DIM] += 1
        out[:, 0] += 1e-3  # never an all-zero vector
        return out / np.linalg.norm(out, axis=1, keepdims=True)


def doc(doc_id, tenant, text):
    return Document.build(doc_id=doc_id, tenant_id=tenant, source="test", type="note", title=doc_id,
                          text=f"{doc_id}: {doc_id}\nType: note\n\n{text}")


DOCS = [
    doc("A1", "tenant-a", "credential dumping from process memory"),
    doc("A2", "tenant-a", "phishing email with a malicious attachment"),
    doc("A3", "tenant-a", "scheduled task created for persistence"),
    doc("B1", "tenant-b", "credential dumping from process memory, tenant b copy"),
]


@pytest.fixture(scope="module")
def conn():
    try:
        c = db.connect()
    except RuntimeError as e:
        pytest.skip(str(e))
    schema = f"test_{uuid.uuid4().hex[:8]}"
    c.execute(f"CREATE SCHEMA {schema}")
    c.execute(f"SET search_path TO {schema}, public")
    c.commit()
    yield c
    c.rollback()
    c.execute(f"DROP SCHEMA {schema} CASCADE")
    c.commit()
    c.close()


@pytest.fixture(scope="module")
def embedder():
    return FakeEmbedder()


@pytest.fixture(scope="module")
def indexed(conn, embedder):
    return index_documents(conn, DOCS, embedder, progress=lambda *_: None)


def test_index_counts(indexed):
    assert indexed == {"docs": 4, "chunks": 4, "embedded": 4, "tenants": 2}


def test_reindex_uses_cache(conn, embedder, indexed):
    before = embedder.calls
    stats = index_documents(conn, DOCS, embedder, progress=lambda *_: None)
    assert stats["embedded"] == 0 and embedder.calls == before


@pytest.mark.parametrize("mode", ["dense", "keyword", "hybrid"])
def test_finds_the_right_doc(conn, embedder, indexed, mode):
    hits = getattr(HybridSearcher(conn, "tenant-a", embedder), mode)("credential dumping memory", k=3)
    assert hits[0].doc_id == "A1" and hits[0].chunk_id == "A1#0"


def test_keyword_matches_identifier(conn, embedder, indexed):
    assert HybridSearcher(conn, "tenant-a", embedder).keyword("A3")[0].doc_id == "A3"


@pytest.mark.parametrize("mode", ["dense", "keyword", "hybrid"])
def test_tenant_isolation(conn, embedder, indexed, mode):
    # B1 matches the query best of all, but belongs to another tenant: it must never come back.
    for tenant, other in [("tenant-a", "B"), ("tenant-b", "A")]:
        hits = getattr(HybridSearcher(conn, tenant, embedder), mode)("credential dumping memory tenant b copy", k=10)
        assert hits and not any(h.doc_id.startswith(other) for h in hits)


def test_unknown_tenant_is_an_error(conn, embedder, indexed):
    with pytest.raises(LookupError):
        HybridSearcher(conn, "tenant-zzz", embedder)


def test_rrf_rewards_agreement():
    fused = dict(rrf([["x", "y", "z"], ["y", "x", "w"]]))
    assert fused["x"] == fused["y"] > fused["z"] > 0 and fused["w"] == fused["z"]


def test_identifiers_are_pinned_first(conn, embedder, indexed):
    s = HybridSearcher(conn, "tenant-a", embedder, aliases={"OLD9": "A2", "X9": "B1"})
    assert s.hybrid("phishing email, but tell me about A3", pin_ids=True)[0].doc_id == "A3"
    assert s.hybrid("what was OLD9?", pin_ids=True)[0].doc_id == "A2"      # alias -> current doc
    assert s.pinned("X9 B1") == []                                         # other tenant's docs never pinned
    assert s.hybrid("phishing email", pin_ids=True)[0].doc_id == "A2"      # no ID: normal ranking


class WordReranker:
    """Fake cross-encoder: score = 1 if the passage contains the word, else 0."""

    def __init__(self, word):
        self.word, self.seen = word, []

    def scores(self, query, passages):
        self.seen.append(query)
        return [float(self.word in p) for p in passages]


def test_reranker_reorders_candidates(conn, embedder, indexed):
    s = HybridSearcher(conn, "tenant-a", embedder)
    assert s.hybrid("credential dumping memory")[0].doc_id == "A1"
    reranker = WordReranker("persistence")
    assert s.hybrid("credential dumping memory", reranker=reranker)[0].doc_id == "A3"
    assert reranker.seen == ["credential dumping memory"]


def test_reranker_scores_both_question_versions(conn, embedder, indexed):
    class Translator:
        def translate(self, q):
            return "scheduled task persistence"

    reranker = WordReranker("persistence")
    hits = HybridSearcher(conn, "tenant-a", embedder).hybrid("سؤال", translator=Translator(), reranker=reranker)
    assert hits[0].doc_id == "A3" and reranker.seen == ["سؤال", "scheduled task persistence"]
