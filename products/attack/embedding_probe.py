"""
Step 3b: which embedding model should dense retrieval use?

Embeds all ATT&CK docs with each candidate model, runs the probe questions in eval/embedding_probe.jsonl
(English, Arabic, and Arabic mixed with English IDs/tool names), and reports per language:
  hit@1, hit@5 : share of questions with a relevant doc at rank 1 / in the top 5
  MRR@10       : mean of 1/rank of the first relevant doc (0 if not in the top 10)
plus EN<->AR agreement (same question in both languages) and embedding speed.

This is a small model-selection probe (32 questions), not the phase 2 golden set.
Doc vectors are cached in data/probe/ so re-runs only re-embed when the corpus changes.

Run:  python -m products.attack.embedding_probe [model ...]
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

from core.embeddings import MODELS, OllamaEmbedder
from core.schema import Document
from products.attack.ingest import DOCS_PATH, ROOT

QUERIES_PATH = ROOT / "eval" / "embedding_probe.jsonl"
RESULTS_PATH = ROOT / "eval" / "results" / "embedding_probe.json"
CACHE_DIR = ROOT / "data" / "probe"
LANGS = ["en", "ar", "mixed"]


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def corpus_key(docs: list[Document]) -> str:
    """Short hash of every document's content_hash: changes whenever any document's text changes."""
    return hashlib.sha256("".join(d.content_hash for d in docs).encode()).hexdigest()[:12]


def doc_vectors(embedder: OllamaEmbedder, docs: list[Document]) -> tuple[np.ndarray, float | None]:
    """Embed every doc once per model and corpus version. Returns (vectors, docs/sec or None if cached)."""
    cache = CACHE_DIR / f"{embedder.model.name}-{corpus_key(docs)}.npy"
    if cache.exists():
        return np.load(cache), None
    start = time.perf_counter()
    vectors = embedder.embed_documents([d.text for d in docs])
    rate = len(docs) / (time.perf_counter() - start)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.save(cache, vectors)
    return vectors, rate


def first_relevant_rank(ranked_ids: list[str], relevant: set[str]) -> int | None:
    for rank, doc_id in enumerate(ranked_ids, 1):
        if doc_id in relevant:
            return rank
    return None


def evaluate(model_name: str, docs: list[Document], queries: list[dict]) -> dict:
    embedder = OllamaEmbedder(model_name)
    doc_vecs, docs_per_sec = doc_vectors(embedder, docs)
    ids = [d.doc_id for d in docs]

    start = time.perf_counter()
    q_vecs = embedder.embed_queries([q["query"] for q in queries])
    ms_per_query = 1000 * (time.perf_counter() - start) / len(queries)

    scores = q_vecs @ doc_vecs.T  # cosine similarity (all vectors are unit length)
    per_query = []
    for q, row in zip(queries, scores):
        top = np.argsort(-row)[:10]
        ranked = [ids[i] for i in top]
        rank = first_relevant_rank(ranked, set(q["relevant"]))
        per_query.append({"id": q["id"], "lang": q["lang"], "rank": rank, "top3": ranked[:3]})

    by_lang = {}
    for lang in LANGS + ["all"]:
        rows = [r for r in per_query if lang in (r["lang"], "all")]
        ranks = [r["rank"] for r in rows]
        by_lang[lang] = {
            "n": len(rows),
            "hit@1": round(sum(r == 1 for r in ranks) / len(rows), 3),
            "hit@5": round(sum(r is not None and r <= 5 for r in ranks) / len(rows), 3),
            "mrr@10": round(sum(1 / r for r in ranks if r) / len(rows), 3),
        }

    # Same question asked in English and Arabic (en01 <-> ar01 ...): how close are the two query vectors,
    # and how many of their top-10 docs are shared? Low values = the model treats the languages as different worlds.
    qi = {q["id"]: i for i, q in enumerate(queries)}
    pairs = [(qi[q["id"]], qi["ar" + q["id"][2:]]) for q in queries if q["lang"] == "en" and "ar" + q["id"][2:] in qi]
    cos = [float(q_vecs[a] @ q_vecs[b]) for a, b in pairs]
    overlap = [len(set(np.argsort(-scores[a])[:10]) & set(np.argsort(-scores[b])[:10])) / 10 for a, b in pairs]

    return {
        "model": model_name,
        "dim": embedder.model.dim,
        "by_lang": by_lang,
        "en_ar_query_cosine": round(sum(cos) / len(cos), 3),
        "en_ar_top10_overlap": round(sum(overlap) / len(overlap), 3),
        "docs_per_sec": round(docs_per_sec, 1) if docs_per_sec else None,
        "ms_per_query": round(ms_per_query, 1),
        "per_query": per_query,
    }


def main(model_names: list[str]) -> None:
    with DOCS_PATH.open(encoding="utf-8") as f:
        docs = [Document.model_validate_json(line) for line in f]
    queries = load_jsonl(QUERIES_PATH)
    known = {d.doc_id for d in docs}
    missing = {r for q in queries for r in q["relevant"]} - known
    if missing:
        raise SystemExit(f"probe targets not in corpus: {sorted(missing)}")

    results = []
    for name in model_names:
        print(f"--- {name}", flush=True)
        results.append(evaluate(name, docs, queries))

    previous = json.loads(RESULTS_PATH.read_text(encoding="utf-8")) if RESULTS_PATH.exists() else {}
    merged = {r["model"]: r for r in previous.get("results", [])}
    for r in results:
        if r["docs_per_sec"] is None and r["model"] in merged:  # vectors came from cache: keep the measured speed
            r["docs_per_sec"] = merged[r["model"]]["docs_per_sec"]
        merged[r["model"]] = r
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps({"corpus_docs": len(docs), "queries": len(queries),
                                        "results": list(merged.values())}, ensure_ascii=False, indent=1),
                            encoding="utf-8")

    print(f"\n{len(docs)} docs, {len(queries)} queries\n")
    print("| Model | dim | EN hit@1 / hit@5 / MRR | AR hit@1 / hit@5 / MRR | Mixed hit@1 / hit@5 / MRR | All hit@1 / hit@5 / MRR "
          "| EN-AR cos | EN-AR top10 overlap | docs/s | ms/query |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in merged.values():
        cells = [" / ".join(str(r["by_lang"][l][k]) for k in ("hit@1", "hit@5", "mrr@10")) for l in LANGS + ["all"]]
        print(f"| {r['model']} | {r['dim']} | " + " | ".join(cells) +
              f" | {r['en_ar_query_cosine']} | {r['en_ar_top10_overlap']} | {r['docs_per_sec']} | {r['ms_per_query']} |")
    print(f"\nWrote {RESULTS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1:] or list(MODELS))
