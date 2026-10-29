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
from core.text import identifiers, normalize


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
    def __init__(self, conn: psycopg.Connection, tenant_id: str, embedder: OllamaEmbedder,
                 aliases: dict[str, str] | None = None, links: dict[str, list[str]] | None = None):
        """`aliases`: extra IDs that should resolve to a document (e.g. old IDs -> current doc_id).
        `links`: doc_id -> related doc_ids worth showing the reranker when that doc ranks high (one hop)."""
        self.conn, self.tenant_id, self.embedder = conn, tenant_id, embedder
        rows = conn.execute("SELECT chunk_id, doc_id, text, title, url FROM chunks WHERE tenant_id = %s "
                            "ORDER BY doc_id, ordinal", (tenant_id,)).fetchall()
        if not rows:
            raise LookupError(f"no indexed chunks for tenant {tenant_id!r}")
        self.chunk_doc = {r[0]: r[1] for r in rows}
        self.chunk_text = {r[0]: r[2] for r in rows}
        self.doc_chunks: dict[str, list[str]] = {}
        for r in rows:
            self.doc_chunks.setdefault(r[1], []).append(r[0])
        self.doc_info = {r[1]: (r[3], r[4]) for r in rows}
        self.bm25 = BM25([r[0] for r in rows], [r[2] for r in rows])
        self.id_map = {normalize(alias): doc for alias, doc in (aliases or {}).items() if doc in self.doc_info}
        self.id_map |= {normalize(doc): doc for doc in self.doc_info}
        self.links = {d: [x for x in linked if x in self.doc_info] for d, linked in (links or {}).items()}
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

    def pinned(self, query: str) -> list[str]:
        """Documents whose ID (or alias) appears in the question, in the order typed. Someone who types an ID
        means that object, so it goes first, whatever the rankers think."""
        out: list[str] = []
        for token in identifiers(query):
            doc = self.id_map.get(token)
            if doc and doc not in out:
                out.append(doc)
        return out

    def hybrid(self, query: str, k: int = 10, depth: int = 50, rrf_k: int = 60, translator=None,
               reranker=None, rerank_depth: int = 30, pin_ids: bool = False, expand_top: int = 0,
               max_expanded: int = 20) -> list[Hit]:
        """Dense + keyword, fused with RRF. Options, in pipeline order:
        - translator: a question in another language is also searched in its translation (4 rankings fused);
        - reranker: a cross-encoder re-orders the top `rerank_depth` documents (all their chunks are scored,
          the best chunk counts; with a translation, the better of the two question versions counts);
        - expand_top: before reranking, documents linked to the top `expand_top` results (see `links`) join the
          candidates, up to `max_expanded`. Lets the reranker reach answers one hop away (question -> topic ->
          its countermeasure) that share no words with the question;
        - pin_ids: documents named by ID in the question go first."""
        queries = [query]
        if translator is not None and (translated := translator.translate(query)):
            queries.append(translated)
        rankings: list[list[Hit]] = []
        for q in queries:
            rankings += [self.dense(q, depth, depth), self.keyword(q, depth, depth)]
        best_chunk = {h.doc_id: h.chunk_id for ranking in reversed(rankings) for h in reversed(ranking)}
        ranked = [(d, best_chunk[d], s) for d, s in rrf([[h.doc_id for h in r] for r in rankings], rrf_k)]

        if reranker is not None:
            head, tail = ranked[:rerank_depth], ranked[rerank_depth:]
            if expand_top:
                present = {d for d, _, _ in head}
                extra = [x for d, _, _ in head[:expand_top] for x in self.links.get(d, []) if x not in present]
                extra = list(dict.fromkeys(extra))[:max_expanded]
                head += [(x, self.doc_chunks[x][0], 0.0) for x in extra]
                tail = [r for r in tail if r[0] not in set(extra)]
            chunk_ids = [c for d, _, _ in head for c in self.doc_chunks[d]]
            texts = [self.chunk_text[c] for c in chunk_ids]
            # A reranker that reads any language only needs the original question.
            versions = queries[:1] if getattr(reranker, "reads_any_language", False) else queries
            per_query = [reranker.scores(q, texts) for q in versions]
            chunk_score = {c: max(s[i] for s in per_query) for i, c in enumerate(chunk_ids)}
            reranked = []
            for d, _, _ in head:
                best = max(self.doc_chunks[d], key=chunk_score.__getitem__)
                reranked.append((d, best, chunk_score[best]))
            ranked = sorted(reranked, key=lambda x: -x[2]) + tail

        if pin_ids and (pins := self.pinned(query)):
            by_doc = {d: (d, c, s) for d, c, s in ranked}
            first = [by_doc.get(d, (d, self.doc_chunks[d][0], float("inf"))) for d in pins]
            ranked = first + [r for r in ranked if r[0] not in pins]
        return self._hits(ranked, k)
