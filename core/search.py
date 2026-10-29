"""Hybrid retrieval: dense (pgvector) + keyword (BM25), fused with Reciprocal Rank Fusion.

Every search is scoped to one tenant; there is no code path that searches without a tenant_id.

RRF: score(doc) = sum over rankings of 1 / (k + rank). It only uses ranks, not raw scores, so it can combine
a cosine similarity (0..1) with a BM25 score (0..30+) without any calibration. k = 60 is the common default:
it stops the very top ranks from dominating.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from core.bm25 import BM25
from core.embeddings import OllamaEmbedder
from core.index import to_pgvector


@dataclass(frozen=True)
class Hit:
    doc_id: str
    chunk_id: str      # best-matching chunk of that document (what gets cited)
    score: float
    title: str
    url: str | None


def _collapse(ranked_chunks: list[tuple[str, float]], chunk_doc: dict[str, str]) -> list[tuple[str, str, float]]:
    """Chunk ranking -> document ranking, keeping each document's best chunk."""
    seen, out = set(), []
    for chunk_id, score in ranked_chunks:
        doc_id = chunk_doc[chunk_id]
        if doc_id not in seen:
            seen.add(doc_id)
            out.append((doc_id, chunk_id, score))
    return out


def rrf(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])


class HybridSearcher:
    def __init__(self, conn: psycopg.Connection, tenant_id: str, embedder: OllamaEmbedder):
        self.conn, self.tenant_id, self.embedder = conn, tenant_id, embedder
        rows = conn.execute("SELECT chunk_id, doc_id, text, title, url FROM chunks WHERE tenant_id = %s",
                            (tenant_id,)).fetchall()
        if not rows:
            raise LookupError(f"no indexed chunks for tenant {tenant_id!r}")
        self.chunk_doc = {r[0]: r[1] for r in rows}
        self.doc_info = {r[1]: (r[3], r[4]) for r in rows}
        self.bm25 = BM25([r[0] for r in rows], [r[2] for r in rows])
        # HNSW returns at most ef_search candidates; raise it above the depth we ask for.
        conn.execute("SET hnsw.ef_search = 200")

    def _hits(self, ranked: list[tuple[str, str, float]], k: int) -> list[Hit]:
        return [Hit(d, c, s, *self.doc_info[d]) for d, c, s in ranked[:k]]

    def dense(self, query: str, k: int = 10, depth: int = 50) -> list[Hit]:
        qv = to_pgvector(self.embedder.embed_queries([query])[0])
        rows = self.conn.execute(
            """SELECT chunk_id, 1 - (embedding <=> %s::vector) FROM chunks
               WHERE tenant_id = %s ORDER BY embedding <=> %s::vector LIMIT %s""",
            (qv, self.tenant_id, qv, depth)).fetchall()
        return self._hits(_collapse(rows, self.chunk_doc), k)

    def keyword(self, query: str, k: int = 10, depth: int = 50) -> list[Hit]:
        return self._hits(_collapse(self.bm25.search(query, depth), self.chunk_doc), k)

    def hybrid(self, query: str, k: int = 10, depth: int = 50, rrf_k: int = 60, translator=None) -> list[Hit]:
        """Dense + keyword, fused. With a translator, a question in another language is also searched in
        its translation (both dense and keyword), so up to four rankings are fused."""
        queries = [query]
        if translator is not None and (translated := translator.translate(query)):
            queries.append(translated)
        rankings: list[list[Hit]] = []
        for q in queries:
            rankings += [self.dense(q, depth, depth), self.keyword(q, depth, depth)]
        best_chunk = {h.doc_id: h.chunk_id for ranking in reversed(rankings) for h in reversed(ranking)}
        fused = rrf([[h.doc_id for h in ranking] for ranking in rankings], rrf_k)
        return self._hits([(d, best_chunk[d], s) for d, s in fused], k)
