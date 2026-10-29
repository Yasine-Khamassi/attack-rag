import math

import pytest

from core.metrics import hit_at_k, mean, mrr_at_k, ndcg_at_k, recall_at_k, score

GRADES = {"a": 2, "b": 2, "c": 1}


def test_hit_recall_mrr_use_primary_answers_only():
    ranked = ["c", "x", "b", "y", "a"]
    assert hit_at_k(ranked, GRADES, 2) == 0.0      # "c" is only grade 1
    assert hit_at_k(ranked, GRADES, 3) == 1.0
    assert recall_at_k(ranked, GRADES, 3) == 0.5 and recall_at_k(ranked, GRADES, 5) == 1.0
    assert mrr_at_k(ranked, GRADES) == pytest.approx(1 / 3)
    assert mrr_at_k(["x"], GRADES) == 0.0


def test_ndcg_ideal_is_one_and_order_matters():
    assert ndcg_at_k(["a", "b", "c"], GRADES) == pytest.approx(1.0)
    worse = ndcg_at_k(["c", "b", "a"], GRADES)
    assert 0 < worse < 1
    # hand-computed: gains 1, 3, 3 at ranks 1..3 vs ideal 3, 3, 1
    dcg = 1 + 3 / math.log2(3) + 3 / 2
    idcg = 3 + 3 / math.log2(3) + 1 / 2
    assert worse == pytest.approx(dcg / idcg)
    assert ndcg_at_k(["x", "y"], GRADES) == 0.0


def test_score_and_mean():
    rows = [score(["a"], GRADES), score(["x"], GRADES)]
    assert mean(rows)["hit@5"] == 0.5 and set(rows[0]) == {"hit@5", "recall@10", "mrr@10", "ndcg@10"}
