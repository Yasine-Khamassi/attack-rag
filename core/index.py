"""Write chunks + their vectors into Postgres/pgvector.

Tables (created on demand):
- embedding_cache(model, content_hash, embedding): every vector ever computed, keyed by the text's hash.
  Re-indexing only embeds text that changed; on a CPU that is the difference between seconds and an hour.
- chunks: one row per chunk per tenant, with the vector for the active model and an HNSW index for search.
- index_meta: which embedding model the `chunks` vectors came from (a mismatch means: re-index).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

import numpy as np
import psycopg

from core.chunking import Chunk, chunk_document
from core.embeddings import OllamaEmbedder
from core.schema import Document


def to_pgvector(v: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


def ensure_schema(conn: psycopg.Connection, model: str, dim: int) -> None:
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute("CREATE TABLE IF NOT EXISTS index_meta (key text PRIMARY KEY, value text NOT NULL)")
    conn.execute("""CREATE TABLE IF NOT EXISTS embedding_cache (
        model text NOT NULL, content_hash text NOT NULL, embedding vector NOT NULL,
        PRIMARY KEY (model, content_hash))""")
    row = conn.execute("SELECT value FROM index_meta WHERE key = 'model'").fetchone()
    if row and row[0] != model:
        # Vectors from different models are not comparable; rebuild the table for the new one.
        conn.execute("DROP TABLE IF EXISTS chunks")
    conn.execute(f"""CREATE TABLE IF NOT EXISTS chunks (
        tenant_id text NOT NULL,
        chunk_id text NOT NULL,
        doc_id text NOT NULL,
        ordinal int NOT NULL,
        type text NOT NULL,
        title text NOT NULL,
        url text,
        text text NOT NULL,
        metadata jsonb NOT NULL DEFAULT '{{}}',
        content_hash text NOT NULL,
        embedding vector({int(dim)}) NOT NULL,
        PRIMARY KEY (tenant_id, chunk_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS chunks_tenant ON chunks (tenant_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS chunks_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)")
    conn.execute("INSERT INTO index_meta VALUES ('model', %s) ON CONFLICT (key) DO UPDATE SET value = %s",
                 (model, model))
    conn.commit()


def seed_cache(conn: psycopg.Connection, model: str, hashes: Sequence[str], vectors: np.ndarray) -> int:
    """Load vectors computed elsewhere (e.g. an earlier experiment) into the cache. Returns rows added."""
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO embedding_cache VALUES (%s, %s, %s::vector) ON CONFLICT DO NOTHING",
            [(model, h, to_pgvector(v)) for h, v in zip(hashes, vectors)])
        added = cur.rowcount
    conn.commit()
    return max(added, 0)


def _cached(conn: psycopg.Connection, model: str, hashes: Iterable[str]) -> set[str]:
    rows = conn.execute("SELECT content_hash FROM embedding_cache WHERE model = %s AND content_hash = ANY(%s)",
                        (model, list(hashes))).fetchall()
    return {r[0] for r in rows}


def index_documents(conn: psycopg.Connection, docs: Sequence[Document], embedder: OllamaEmbedder,
                    batch: int = 64, progress=print) -> dict[str, int]:
    """Chunk, embed what isn't cached, and replace each tenant's rows in `chunks` with the new set."""
    model = embedder.model
    ensure_schema(conn, model.name, model.dim)
    chunks: list[Chunk] = [c for d in docs for c in chunk_document(d)]

    unique = {c.content_hash: c.text for c in chunks}
    todo = [h for h in unique if h not in _cached(conn, model.name, unique)]
    progress(f"{len(chunks)} chunks, {len(unique)} unique texts, {len(todo)} to embed")
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        seed_cache(conn, model.name, part, embedder.embed_documents([unique[h] for h in part]))
        progress(f"  embedded {min(i + batch, len(todo))}/{len(todo)}")  # committed per batch: safe to resume

    tenants = sorted({c.tenant_id for c in chunks})
    with conn.transaction():
        conn.execute("DELETE FROM chunks WHERE tenant_id = ANY(%s)", (tenants,))
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO chunks (tenant_id, chunk_id, doc_id, ordinal, type, title, url, text, metadata,
                                       content_hash, embedding)
                   SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, embedding
                   FROM embedding_cache WHERE model = %s AND content_hash = %s""",
                [(c.tenant_id, c.chunk_id, c.doc_id, c.ordinal, c.type, c.title, c.url, c.text,
                  json.dumps(c.metadata, ensure_ascii=False), c.content_hash, model.name, c.content_hash)
                 for c in chunks])
    count = conn.execute("SELECT count(*) FROM chunks WHERE tenant_id = ANY(%s)", (tenants,)).fetchone()[0]
    if count != len(chunks):
        raise RuntimeError(f"indexed {count} rows, expected {len(chunks)}")
    return {"docs": len(docs), "chunks": len(chunks), "embedded": len(todo), "tenants": len(tenants)}
