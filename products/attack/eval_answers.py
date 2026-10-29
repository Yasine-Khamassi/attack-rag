"""
Step 7: evaluate generated answers (phase 3).

Answerable: a stratified sample of the golden set (every template, both languages). Unanswerable: off-topic,
fake IDs/names, future predictions, facts outside the corpus (eval/unanswerable.jsonl): the right answer is a refusal.

Per answer (deterministic): refused?, citation coverage (share of claims with a citation), invalid citations,
language matches the question, cites a primary answer page (grade 2 in the golden labels).
Faithfulness (LLM judge, a stronger model than the writer): is each claim supported by the sources it cites?

Run:  python -m products.attack.eval_answers [--per-template 4] [--judge claude-sonnet-5-5]   (~$0.6)
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict

from core.judge import FaithfulnessJudge
from core.llm import Claude, spent_usd
from products.attack.assistant import Assistant, to_json
from products.attack.ingest import ROOT

GOLDEN_PATH = ROOT / "eval" / "golden.jsonl"
UNANSWERABLE_PATH = ROOT / "eval" / "unanswerable.jsonl"
RESULTS_PATH = ROOT / "eval" / "results" / "answers_eval.json"
SEED = 11  # the sample prompts were tuned on; --seed 12 gives a fresh validation sample


def sample(per_template: int, seed: int = SEED) -> list[dict]:
    """per_template questions of each template, split across languages where the template has both."""
    with GOLDEN_PATH.open(encoding="utf-8") as f:
        golden = [json.loads(line) for line in f]
    by: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for q in golden:
        by[(q["template"], q["lang"])].append(q)
    rng, out = random.Random(seed), []
    for template in sorted({t for t, _ in by}):
        langs = sorted(lang for t, lang in by if t == template)
        for i in range(per_template):
            pool = by[(template, langs[i % len(langs)])]
            pool = [q for q in pool if q not in out]
            if pool:
                out.append(rng.choice(pool))
    return out


def rate(rows: list[dict], key) -> float:
    return round(sum(1 for r in rows if key(r)) / len(rows), 3) if rows else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-template", type=int, default=4)
    parser.add_argument("--judge", default="claude-sonnet-5-5")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", default="answers_eval.json")
    args = parser.parse_args()

    answerable = sample(args.per_template, args.seed)
    with UNANSWERABLE_PATH.open(encoding="utf-8") as f:
        unanswerable = [json.loads(line) for line in f]
    spent_before = spent_usd()
    assistant = Assistant()
    judge = FaithfulnessJudge(Claude(args.judge, purpose="faithfulness judge"))

    rows = []
    for q in answerable + unanswerable:
        answer, timing = assistant.ask(q["query"])
        row = {"id": q["id"], "lang": q["lang"], "kind": q.get("template", q.get("kind")),
               "answerable": "relevance" in q, **to_json(answer, timing)}
        if row["answerable"]:
            primary = {d for d, g in q["relevance"].items() if g == 2}
            row["cites_primary"] = any(answer.sources[n - 1].doc_id in primary for n in answer.cited)
            verdict = judge.judge(answer)
            row["faithfulness"] = None if verdict is None else round(verdict.faithfulness, 3)
            row["verdicts"] = [] if verdict is None else verdict.verdicts
        rows.append(row)
        print(f"{q['id']:8s} refused={answer.refused!s:5s} cov={answer.citation_coverage:.2f} "
              f"faith={row.get('faithfulness')} {answer.text[:70]!r}", flush=True)

    ans = [r for r in rows if r["answerable"]]
    una = [r for r in rows if not r["answerable"]]
    answered = [r for r in ans if not r["refused"]]
    judged = [r["faithfulness"] for r in answered if r["faithfulness"] is not None]
    summary = {
        "answerable": len(ans), "unanswerable": len(una),
        "answer_rate": rate(ans, lambda r: not r["refused"]),
        "refusal_rate_unanswerable": rate(una, lambda r: r["refused"]),
        "citation_coverage": round(sum(r["checks"]["citation_coverage"] for r in answered) / len(answered), 3),
        "answers_fully_cited": rate(answered, lambda r: r["checks"]["citation_coverage"] == 1.0),
        "answers_with_invalid_citation": rate(answered, lambda r: r["checks"]["invalid_citations"]),
        "language_match": rate(answered, lambda r: r["checks"]["language_ok"]),
        "cites_primary_source": rate(answered, lambda r: r["cites_primary"]),
        "faithfulness": round(sum(judged) / len(judged), 3) if judged else None,
        "answers_fully_faithful": rate(answered, lambda r: r["faithfulness"] == 1.0),
        # How often the guards had to step in (the model's raw compliance): D29.
        "guard_removed_claims": rate(ans, lambda r: r["checks"]["removed_uncited"]),
        "guard_language_repaired": rate(ans, lambda r: r["checks"]["language_repaired"]),
        "seed": args.seed,
        "by_lang": {lang: {"faithfulness": round(sum(r["faithfulness"] for r in g) / len(g), 3),
                           "cites_primary_source": rate(g, lambda r: r["cites_primary"])}
                    for lang in ("en", "ar", "mixed")
                    if (g := [r for r in answered if r["lang"] == lang and r["faithfulness"] is not None])},
        "seconds_per_question": round(sum(r["timing"]["retrieval_s"] + r["timing"]["answer_s"] for r in rows)
                                      / len(rows), 2),
        "cost_usd": round(spent_usd() - spent_before, 4),
        "writer": "claude-haiku-5-5", "judge": args.judge,
    }
    out = RESULTS_PATH.with_name(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n" + json.dumps(summary, indent=1))
    print(f"Wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
