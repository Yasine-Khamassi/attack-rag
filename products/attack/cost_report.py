"""
Step 10 (phase 6): what does a question / a conversation cost in production?

Reads the per-question API cost the assistant records (timing.cost_usd: translation + rerank + answer + tools +
guards; the evaluation judge is excluded) from an answer-eval results file, and projects it.

Run:  python -m products.attack.cost_report [eval/results/answers_eval_phase6.json]
"""

from __future__ import annotations

import json
import statistics
import sys

from products.attack.ingest import ROOT

TURNS_PER_CONVERSATION = 3   # assumption for projections: a conversation = question + ~2 follow-ups


def percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def main(path: str) -> None:
    rows = json.loads((ROOT / path).read_text(encoding="utf-8"))["rows"]
    costs = [r["timing"]["cost_usd"] for r in rows if "cost_usd" in r["timing"]]
    by_route: dict[str, list[float]] = {}
    for r in rows:
        by_route.setdefault(r["timing"].get("route", "?"), []).append(r["timing"].get("cost_usd", 0.0))
    mean, p50, p95 = statistics.mean(costs), percentile(costs, 50), percentile(costs, 95)
    per_conv = mean * TURNS_PER_CONVERSATION
    report = {
        "questions": len(costs), "mean_usd": round(mean, 6), "p50_usd": round(p50, 6), "p95_usd": round(p95, 6),
        "by_route": {k: {"n": len(v), "mean_usd": round(statistics.mean(v), 6)} for k, v in by_route.items()},
        "per_conversation_usd": round(per_conv, 5), "per_1000_conversations_usd": round(per_conv * 1000, 2),
        "per_100k_conversations_usd": round(per_conv * 100_000, 2),
        "assumption": f"{TURNS_PER_CONVERSATION} questions per conversation; no answer-cache hits",
    }
    out = ROOT / "eval" / "results" / "cost_report.json"
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "eval/results/answers_eval_phase6.json")
