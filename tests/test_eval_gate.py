"""CI gate for retrieval quality (phase 2).

CI cannot run the embedding model or call Claude, so the eval runs locally
(`python -m products.attack.eval_golden`) and its results are committed. These tests then hold every commit to
the approved baseline (eval/baseline.json): a change that makes retrieval worse fails CI as soon as its
eval results are committed. The golden set itself is also checked for consistency and answer leaks.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "eval" / "golden.jsonl"
RESULTS = ROOT / "eval" / "results" / "golden_eval.json"
BASELINE = ROOT / "eval" / "baseline.json"
# Allowed drop below baseline. Sized from measured run-to-run spread of the LLM reranker (DECISIONS D19/D20).
TOLERANCE = {"hit@5": 0.03, "recall@10": 0.03, "mrr@10": 0.05, "ndcg@10": 0.05}
ID = re.compile(r"\b(?:T\d{4}(?:\.\d{3})?|[GSMC]\d{4}|DET\d{4})\b")


def golden():
    return [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_golden_set_is_consistent():
    qs = golden()
    assert len(qs) >= 100
    assert len({q["id"] for q in qs}) == len(qs)
    for q in qs:
        assert q["lang"] in {"en", "ar", "mixed"} and q["query"].strip()
        assert q["relevance"] and set(q["relevance"].values()) <= {1, 2} and 2 in q["relevance"].values()


def test_questions_do_not_contain_their_answers():
    for q in golden():
        added = set(ID.findall(q["query"])) - set(ID.findall(q["template_question"]))
        assert not added & set(q["relevance"]), f"{q['id']} leaks answer IDs {added & set(q['relevance'])}"


@pytest.fixture(scope="module")
def results():
    if not RESULTS.exists() or not BASELINE.exists():
        pytest.skip("no committed eval results yet")
    return json.loads(RESULTS.read_text(encoding="utf-8")), json.loads(BASELINE.read_text(encoding="utf-8"))


def test_results_match_current_golden_set(results):
    latest, _ = results
    assert latest["questions"] == len(golden()), "golden set changed: re-run eval_golden and commit the results"
    ids = {r["id"] for r in latest["per_question"]["full"][0]}
    assert ids == {q["id"] for q in golden()}


@pytest.mark.parametrize("config", ["hybrid", "full"])
def test_no_regression_against_baseline(results, config):
    latest, baseline = results
    for group, approved in baseline[config].items():
        now = latest["summary"][config]["mean"][group]
        for metric, value in approved.items():
            assert now[metric] >= value - TOLERANCE[metric], (
                f"{config} {group} {metric}: {now[metric]} < baseline {value} - {TOLERANCE[metric]}")
