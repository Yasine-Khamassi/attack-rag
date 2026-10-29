"""
Step 4: does retrieval find the right documents? Each pipeline stage measured on its own (an ablation).

Question sets (each line: query + doc IDs that count as correct; optional "relevant_via" adds every doc ATT&CK
links to a subject, e.g. {"source": "G0032", "relation": "uses", "type": "software"} = all software Lazarus uses,
for questions whose correct answers are exactly those linked documents):
  eval/spot_checks.jsonl      20 new questions; the phase 1 gate is: the full pipeline (GATE_MODE) has a
                              correct doc in the top 5 for every one of them
  eval/embedding_probe.jsonl  the 32 step 3b probe questions, to compare with the dense-only numbers there

Modes, each adding one stage to the previous:
  dense, keyword          one retriever alone
  hybrid                  dense + keyword fused with RRF
  +translate              Arabic questions also searched in an English translation (qwen2.5:3b)
  +pin                    documents named by ID (or old revoked ID) in the question go first
  +rerank                 the top 30 are re-ordered by a reranker (the full pipeline; gate mode)

Two stacks for the translate/rerank stages:
  --stack haiku   Claude Haiku 5.5 translates and reranks (listwise). Default. Costs ~$0.001 per question,
                  budget-limited by core.llm.
  --stack local   qwen2.5:3b (Ollama) translates; cross-encoders bge-reranker-v2-m3 (+rerank) and
                  mmarco-mMiniLMv2 (+rerank_small) rerank on the CPU. Free but slow (~1 h for all questions).

Run:  python -m products.attack.retrieval_check [--stack haiku|local]
"""

from __future__ import annotations

import argparse
import json
import time

from core import db
from core.embeddings import DEFAULT_MODEL, OllamaEmbedder
from core.query import ClaudeTranslator, OllamaTranslator
from core.rerank import ClaudeReranker, CrossEncoderReranker
from core.schema import Document
from core.search import HybridSearcher
from products.attack.embedding_probe import first_relevant_rank, load_jsonl
from products.attack.ingest import DOCS_PATH, REL_PATH, ROOT, TENANT_ID

SETS = {"spot_checks": ROOT / "eval" / "spot_checks.jsonl", "probe": ROOT / "eval" / "embedding_probe.jsonl"}
RESULTS_DIR = ROOT / "eval" / "results"
GATE_MODE = "+rerank"
GATE_K = 5
STACKS = {
    "haiku": {"translator": "claude-haiku-5-5", "rerankers": {"+rerank": "claude-haiku-5-5"}},
    "local": {"translator": "qwen2.5:3b",
              "rerankers": {"+rerank": "bge-reranker-v2-m3", "+rerank_small": "mmarco-minilm"}},
}


def expand_relevant(q: dict, rels: list[dict], doc_type: dict[str, str]) -> set[str]:
    relevant = set(q["relevant"])
    for spec in q.get("relevant_via", []):
        relevant |= {r["target"] for r in rels if r["source"] == spec["source"] and r["relation"] == spec["relation"]
                     and doc_type.get(r["target"]) == spec["type"]}
    return relevant


def summarize(ranks: list[int | None]) -> dict:
    n = len(ranks)
    return {"n": n,
            "hit@1": round(sum(r == 1 for r in ranks) / n, 3),
            "hit@5": round(sum(r is not None and r <= 5 for r in ranks) / n, 3),
            "mrr@10": round(sum(1 / r for r in ranks if r) / n, 3)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack", choices=list(STACKS), default="haiku")
    stack_name = parser.parse_args().stack
    stack = STACKS[stack_name]
    results_path = RESULTS_DIR / f"retrieval_check_{stack_name}.json"
    results: dict = {"stack": stack_name, "model": DEFAULT_MODEL, "translator": stack["translator"],
                     "rerankers": stack["rerankers"], "sets": {}}
    with DOCS_PATH.open(encoding="utf-8") as f:
        all_docs = [Document.model_validate_json(line) for line in f]
    aliases = {old: d.doc_id for d in all_docs for old in d.metadata.get("previous_ids", [])}
    doc_type = {d.doc_id: d.type for d in all_docs}
    with REL_PATH.open(encoding="utf-8") as f:
        rels = [json.loads(line) for line in f]
    with db.connect() as conn:
        searcher = HybridSearcher(conn, TENANT_ID, OllamaEmbedder(DEFAULT_MODEL), aliases=aliases)
        if stack_name == "haiku":
            translator = ClaudeTranslator(stack["translator"], target="English", domain="cybersecurity")
            rerankers = {mode: ClaudeReranker(name) for mode, name in stack["rerankers"].items()}
        else:
            translator = OllamaTranslator(stack["translator"], target="English", domain="cybersecurity")
            rerankers = {mode: CrossEncoderReranker(name) for mode, name in stack["rerankers"].items()}
            for r in rerankers.values():
                r.scores("warm up", ["warm up"])  # first call loads the model; keep it out of the timings
        searcher.dense("warm up")
        translator.translate("تجربة")
        search = {
            "dense": searcher.dense,
            "keyword": searcher.keyword,
            "hybrid": searcher.hybrid,
            "+translate": lambda q, k: searcher.hybrid(q, k=k, translator=translator),
            "+pin": lambda q, k: searcher.hybrid(q, k=k, translator=translator, pin_ids=True),
        }
        for mode, reranker in rerankers.items():
            search[mode] = (lambda r: lambda q, k: searcher.hybrid(q, k=k, translator=translator, pin_ids=True,
                                                                   reranker=r))(reranker)
        modes = list(search)
        timings: dict[str, list[float]] = {m: [] for m in modes}
        for set_name, path in SETS.items():
            queries = load_jsonl(path)
            rows = []
            for q in queries:
                row = {"id": q["id"], "lang": q["lang"]}
                translator.translate(q["query"])  # translations are cached: timings below exclude them
                for mode in modes:
                    start = time.perf_counter()
                    hits = search[mode](q["query"], k=10)
                    timings[mode].append(time.perf_counter() - start)
                    ranked = [h.doc_id for h in hits]
                    row[mode] = first_relevant_rank(ranked, expand_relevant(q, rels, doc_type))
                    row[f"{mode}_strict"] = first_relevant_rank(ranked, set(q["relevant"]))  # listed IDs only
                    row[f"{mode}_top10"] = ranked  # kept so labels can be re-scored without re-running
                row["translation"] = translator.translate(q["query"])
                rows.append(row)
            langs = sorted({r["lang"] for r in rows}) + ["all"]
            results["sets"][set_name] = {
                "summary": {mode: {lang: summarize([r[mode] for r in rows if lang in (r["lang"], "all")])
                                   for lang in langs} for mode in modes},
                "per_query": rows,
            }
        results["ms_per_query_excl_translation"] = {m: round(1000 * sum(t) / len(t)) for m, t in timings.items()}
        tx = [r for s_ in results["sets"].values() for r in s_["per_query"] if r["translation"]]
        results["translated_questions"] = len(tx)

    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    for set_name, data in results["sets"].items():
        print(f"\n## {set_name}  (hit@1 / hit@5 / MRR@10)")
        langs = list(data["summary"]["dense"])
        print("| Mode | " + " | ".join(langs) + " |")
        print("|---|" + "---|" * len(langs))
        for mode in modes:
            cells = [" / ".join(str(data["summary"][mode][l][k]) for k in ("hit@1", "hit@5", "mrr@10")) for l in langs]
            print(f"| {mode} | " + " | ".join(cells) + " |")
        misses = [r["id"] for r in data["per_query"] if not (r[GATE_MODE] and r[GATE_MODE] <= GATE_K)]
        print(f"{GATE_MODE} misses (not in top {GATE_K}): {misses or 'none'}")

    spot = results["sets"]["spot_checks"]["per_query"]
    passed = sum(1 for r in spot if r[GATE_MODE] and r[GATE_MODE] <= GATE_K)
    strict = sum(1 for r in spot if r[f"{GATE_MODE}_strict"] and r[f"{GATE_MODE}_strict"] <= GATE_K)
    print(f"\nPhase 1 gate: {passed}/{len(spot)} spot checks have a correct doc in {GATE_MODE} top {GATE_K} "
          f"-> {'PASS' if passed == len(spot) else 'FAIL'}  (listed IDs only, without relevant_via: {strict}/{len(spot)})")
    print("\nms per query (CPU; translation excluded, cached): " +
          ", ".join(f"{m} {ms}" for m, ms in results["ms_per_query_excl_translation"].items()))
    print(f"Wrote {results_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
