"""
Step 4: does retrieval find the right documents? Dense vs keyword (BM25) vs hybrid (RRF).

Question sets (each line: query + doc IDs that count as correct):
  eval/spot_checks.jsonl      20 new questions; the phase 1 gate is: the full pipeline (GATE_MODE) has a
                              correct doc in the top 5 for every one of them
  eval/embedding_probe.jsonl  the 32 step 3b probe questions, to compare with the dense-only numbers there

Modes: dense, keyword, hybrid (dense + keyword), hybrid_tx (hybrid + Arabic questions also searched in an
English translation from a local LLM, qwen2.5:3b). The gate is checked on hybrid_tx, the full pipeline.

Run:  python -m products.attack.retrieval_check
"""

from __future__ import annotations

import json

from core import db
from core.embeddings import DEFAULT_MODEL, OllamaEmbedder
from core.query import OllamaTranslator
from core.search import HybridSearcher
from products.attack.embedding_probe import first_relevant_rank, load_jsonl
from products.attack.ingest import ROOT, TENANT_ID

SETS = {"spot_checks": ROOT / "eval" / "spot_checks.jsonl", "probe": ROOT / "eval" / "embedding_probe.jsonl"}
RESULTS_PATH = ROOT / "eval" / "results" / "retrieval_check.json"
MODES = ["dense", "keyword", "hybrid", "hybrid_tx"]
GATE_MODE = "hybrid_tx"
TRANSLATOR_MODEL = "qwen2.5:3b"
GATE_K = 5


def summarize(ranks: list[int | None]) -> dict:
    n = len(ranks)
    return {"n": n,
            "hit@1": round(sum(r == 1 for r in ranks) / n, 3),
            "hit@5": round(sum(r is not None and r <= 5 for r in ranks) / n, 3),
            "mrr@10": round(sum(1 / r for r in ranks if r) / n, 3)}


def main() -> None:
    results: dict = {"model": DEFAULT_MODEL, "translator": TRANSLATOR_MODEL, "sets": {}}
    with db.connect() as conn:
        searcher = HybridSearcher(conn, TENANT_ID, OllamaEmbedder(DEFAULT_MODEL))
        translator = OllamaTranslator(TRANSLATOR_MODEL, target="English", domain="cybersecurity")
        searcher.dense("warm up the model")  # first call loads the model; keep it out of the numbers
        search = {"dense": searcher.dense, "keyword": searcher.keyword, "hybrid": searcher.hybrid,
                  "hybrid_tx": lambda q, k: searcher.hybrid(q, k=k, translator=translator)}
        for set_name, path in SETS.items():
            queries = load_jsonl(path)
            rows = []
            for q in queries:
                row = {"id": q["id"], "lang": q["lang"]}
                for mode in MODES:
                    hits = search[mode](q["query"], k=10)
                    ranked = [h.doc_id for h in hits]
                    row[mode] = first_relevant_rank(ranked, set(q["relevant"]))
                    row[f"{mode}_top3"] = ranked[:3]
                row["translation"] = translator.translate(q["query"])
                rows.append(row)
            langs = sorted({r["lang"] for r in rows}) + ["all"]
            results["sets"][set_name] = {
                "summary": {mode: {lang: summarize([r[mode] for r in rows if lang in (r["lang"], "all")])
                                   for lang in langs} for mode in MODES},
                "per_query": rows,
            }

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    for set_name, data in results["sets"].items():
        print(f"\n## {set_name}  (hit@1 / hit@5 / MRR@10)")
        langs = list(data["summary"]["dense"])
        print("| Mode | " + " | ".join(langs) + " |")
        print("|---|" + "---|" * len(langs))
        for mode in MODES:
            cells = [" / ".join(str(data["summary"][mode][l][k]) for k in ("hit@1", "hit@5", "mrr@10")) for l in langs]
            print(f"| {mode} | " + " | ".join(cells) + " |")
        misses = [r["id"] for r in data["per_query"] if not (r[GATE_MODE] and r[GATE_MODE] <= GATE_K)]
        print(f"{GATE_MODE} misses (not in top {GATE_K}): {misses or 'none'}")

    spot = results["sets"]["spot_checks"]["per_query"]
    passed = sum(1 for r in spot if r[GATE_MODE] and r[GATE_MODE] <= GATE_K)
    print(f"\nPhase 1 gate: {passed}/{len(spot)} spot checks have a correct doc in {GATE_MODE} top {GATE_K} "
          f"-> {'PASS' if passed == len(spot) else 'FAIL'}")
    print(f"Wrote {RESULTS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
