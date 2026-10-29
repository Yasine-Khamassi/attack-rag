"""Probe vectors may only seed the index when they were computed for exactly the current corpus."""

import numpy as np

from core.schema import Document
from products.attack import index
from products.attack.embedding_probe import corpus_key


def docs(texts):
    return [Document.build(doc_id=f"D{i}", tenant_id="t", source="s", type="k", title="T", text=t)
            for i, t in enumerate(texts)]


def test_seeds_only_matching_corpus(monkeypatch, tmp_path):
    seeded = []
    monkeypatch.setattr(index, "PROBE_CACHE", tmp_path)
    monkeypatch.setattr(index, "seed_cache", lambda conn, model, hashes, vecs: seeded.append(hashes) or len(hashes))

    old, new = docs(["a", "b"]), docs(["a", "b changed"])  # same count, different text
    np.save(tmp_path / f"m-{corpus_key(old)}.npy", np.zeros((2, 3), dtype=np.float32))

    assert index.seed_from_probe(None, "m", new) == 0 and seeded == []          # stale file ignored
    assert index.seed_from_probe(None, "m", old) == 2 and seeded == [[d.content_hash for d in old]]
