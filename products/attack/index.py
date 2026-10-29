"""
Step 4: build the search index for ATT&CK: chunk every document, embed (bge-m3 via Ollama), write to pgvector.

Vectors are cached by text hash in the database, so re-running only embeds changed text. Vectors from the
step 3b probe (data/probe/) are loaded into the cache first: single-chunk documents need no new embedding.

Run:  python -m products.attack.index          (needs `ollama serve`; DB from DATABASE_URL or local pgserver)
"""

from __future__ import annotations

import time

import numpy as np

from core import db
from core.embeddings import DEFAULT_MODEL, OllamaEmbedder
from core.index import ensure_schema, index_documents, seed_cache
from core.schema import Document
from products.attack.ingest import DOCS_PATH, ROOT

PROBE_CACHE = ROOT / "data" / "probe"


def load_docs() -> list[Document]:
    with DOCS_PATH.open(encoding="utf-8") as f:
        return [Document.model_validate_json(line) for line in f]


def seed_from_probe(conn, model: str, docs: list[Document]) -> int:
    """Probe vectors line up with attack_docs.jsonl line by line; reuse them if the file is for this model."""
    added = 0
    for path in PROBE_CACHE.glob(f"{model}-*.npy"):
        vectors = np.load(path)
        if len(vectors) == len(docs):
            added += seed_cache(conn, model, [d.content_hash for d in docs], vectors)
    return added


def main() -> None:
    docs = load_docs()
    embedder = OllamaEmbedder(DEFAULT_MODEL)
    start = time.perf_counter()
    with db.connect() as conn:
        ensure_schema(conn, embedder.model.name, embedder.model.dim)
        print(f"Seeded {seed_from_probe(conn, embedder.model.name, docs)} vectors from the probe cache")
        stats = index_documents(conn, docs, embedder)
        sizes = conn.execute("SELECT min(length(text)), percentile_disc(0.5) WITHIN GROUP (ORDER BY length(text)),"
                             " max(length(text)) FROM chunks").fetchone()
        per_doc = conn.execute("SELECT max(n) FROM (SELECT count(*) n FROM chunks GROUP BY doc_id) x").fetchone()[0]
    print(f"Indexed {stats['docs']} docs -> {stats['chunks']} chunks ({stats['embedded']} newly embedded) "
          f"in {time.perf_counter() - start:.0f}s")
    print(f"Chunk length chars: min={sizes[0]} median={sizes[1]} max={sizes[2]}; max chunks per doc={per_doc}")


if __name__ == "__main__":
    main()
