"""
Step 6: evaluate retrieval on the golden set (eval/golden.jsonl, graded relevance).

Configs:
  dense        bge-m3 + pgvector only
  keyword      BM25 only
  hybrid       dense + keyword (RRF) + exact-ID pinning, no LLM
  full         hybrid + Claude Haiku 5.5 translation + Haiku rerank with the production settings in
               products/attack/settings.py (depth, excerpt length, link expansion); run RUNS times
               because the LLM reranker is not deterministic (DECISIONS D19); mean and min/max are reported.

Writes eval/results/golden_eval.json (per question, per run, top 10). With --update-baseline the summary also
becomes eval/baseline.json, the numbers CI holds the pipeline to (tests/test_eval_gate.py).

Run:  python -m products.attack.eval_golden [--runs 3] [--update-baseline]      (~$0.24 per full run)
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict

from core import db
from core.cache import TTLCache
from core.embeddings import DEFAULT_MODEL, OllamaEmbedder
from core.llm import spent_usd
from core.metrics import mean, score
from core.query import ClaudeTranslator
from core.rerank import ClaudeReranker
from core.schema import Document
from core.search import HybridSearcher
from products.attack.ingest import DOCS_PATH, REL_PATH, ROOT, TENANT_ID
from products.attack import pipeline
from products.attack.pipeline import load_links
from products.attack.settings import EXPAND_TOP, LLM_MODEL, RERANK_CHARS, RERANK_DEPTH

GOLDEN_PATH = ROOT / "eval" / "golden.jsonl"
RESULTS_PATH = ROOT / "eval" / "results" / "golden_eval.json"
BASELINE_PATH = ROOT / "eval" / "baseline.json"


def summarize(rows: list[dict]) -> dict:
    """Mean metrics overall, per language and per template."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        for key in ("all", f"lang:{r['lang']}", f"template:{r['template']}"):
            groups[key].append(r["metrics"])
    return {k: {"n": len(v), **mean(v)} for k, v in sorted(groups.items())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--update-baseline", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--rescore", action=argparse.BooleanOptionalAction, default=False,
                        help="recompute metrics from the saved top-10 rankings with the current labels (no API calls)")
    args = parser.parse_args()

    with GOLDEN_PATH.open(encoding="utf-8") as f:
        questions = [json.loads(line) for line in f]
    with DOCS_PATH.open(encoding="utf-8") as f:
        aliases = {old: d.doc_id for d in map(Document.model_validate_json, f)
                   for old in d.metadata.get("previous_ids", [])}
    spent_before = spent_usd()

    configs: dict[str, list[list[dict]]] = {}
    timing: dict[str, float] = {}
    if args.rescore:
        saved = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        by_id = {q["id"]: q for q in questions}
        for name, runs in saved["per_question"].items():
            configs[name] = [[{**row, "metrics": score(row["top10"], by_id[row["id"]]["relevance"])} for row in rows]
                             for rows in runs]
            timing[name] = saved["summary"][name]["ms_per_question"]
        args.runs = saved["runs_full"]
    else:
        configs, timing = run_configs(questions, aliases, args.runs)
    finish(questions, configs, timing, args, spent_before)


def run_configs(questions: list[dict], aliases: dict[str, str], runs_full: int):
    configs: dict[str, list[list[dict]]] = {}
    timing: dict[str, float] = {}
    with db.connect() as conn:
        searcher = HybridSearcher(conn, TENANT_ID, OllamaEmbedder(DEFAULT_MODEL), aliases=aliases,
                                  links=load_links())
        translator = ClaudeTranslator(LLM_MODEL, target="English", domain="cybersecurity")
        reranker = ClaudeReranker(LLM_MODEL, max_chars=RERANK_CHARS)
        searcher.dense("warm up")
        plans = {
            "dense": (1, lambda q: searcher.dense(q, k=10)),
            "keyword": (1, lambda q: searcher.keyword(q, k=10)),
            "hybrid": (1, lambda q: searcher.hybrid(q, k=10, pin_ids=True)),
            "full": (runs_full, lambda q: pipeline.search(searcher, q, translator, reranker)[0]),
        }
        for name, (runs, search) in plans.items():
            configs[name] = []
            start = time.perf_counter()
            for run in range(runs):
                # Each run starts cold: the rerank/translation caches would otherwise replay run 1 and hide
                # the LLM reranker's run-to-run variance (found when 3 runs came back identical, D38).
                reranker.cache = TTLCache(max_items=2048, ttl_seconds=86_400)
                translator._cache.clear()
                rows = []
                for q in questions:
                    ranked = [h.doc_id for h in search(q["query"])]
                    rows.append({"id": q["id"], "lang": q["lang"], "template": q["template"], "top10": ranked,
                                 "metrics": score(ranked, q["relevance"])})
                configs[name].append(rows)
                print(f"{name} run {run + 1}/{runs}: {mean([r['metrics'] for r in rows])}", flush=True)
            timing[name] = round(1000 * (time.perf_counter() - start) / (runs * len(questions)))
    return configs, timing


def finish(questions, configs, timing, args, spent_before) -> None:
    summary = {}
    for name, runs in configs.items():
        per_run = [summarize(rows) for rows in runs]
        summary[name] = {"mean": per_run[0] if len(per_run) == 1 else {
            group: {"n": per_run[0][group]["n"],
                    **{m: round(sum(r[group][m] for r in per_run) / len(per_run), 3) for m in per_run[0]["all"] if m != "n"}}
            for group in per_run[0]}}
        if len(per_run) > 1:
            summary[name]["spread_all"] = {m: [min(r["all"][m] for r in per_run), max(r["all"][m] for r in per_run)]
                                           for m in per_run[0]["all"] if m != "n"}
        summary[name]["ms_per_question"] = timing[name]

    cost = round(spent_usd() - spent_before, 4)
    result = {"questions": len(questions), "embedding": DEFAULT_MODEL, "llm": LLM_MODEL, "runs_full": args.runs,
              "rerank": {"depth": RERANK_DEPTH, "chars": RERANK_CHARS, "expand_top": EXPAND_TOP},
              "cost_usd": cost, "summary": summary,
              "per_question": {name: runs for name, runs in configs.items()}}
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    metrics = ["hit@5", "recall@10", "mrr@10", "ndcg@10"]
    print(f"\n{len(questions)} questions, cost ${cost}\n")
    print("| Config | " + " | ".join(metrics) + " | ms/question |")
    print("|---|" + "---|" * (len(metrics) + 1))
    for name, s in summary.items():
        cells = [str(s["mean"]["all"][m]) for m in metrics]
        if "spread_all" in s:
            cells = [f"{c} ({s['spread_all'][m][0]}-{s['spread_all'][m][1]})" for c, m in zip(cells, metrics)]
        print(f"| {name} | " + " | ".join(cells) + f" | {s['ms_per_question']} |")
    print("\nFull pipeline by group (nDCG@10 / hit@5):")
    for group, v in summary["full"]["mean"].items():
        if group != "all":
            print(f"  {group:26s} n={v['n']:3d}  {v['ndcg@10']} / {v['hit@5']}")

    if args.update_baseline:
        baseline = {name: {g: {m: v[m] for m in metrics} for g, v in s["mean"].items()
                           if g == "all" or g.startswith("lang:")} for name, s in summary.items()}
        BASELINE_PATH.write_text(json.dumps(baseline, indent=1), encoding="utf-8")
        print(f"\nBaseline updated: {BASELINE_PATH.relative_to(ROOT)}")
    print(f"Wrote {RESULTS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
