"""Retrieval metrics with graded relevance.

`grades` maps doc_id -> relevance (2 = primary answer, 1 = also relevant; missing = 0).

- hit@k     : 1 if a primary answer (grade = max grade) is in the top k.
- recall@k  : share of the primary answers that are in the top k.
- MRR       : 1 / rank of the first primary answer within the top k (0 if absent).
- nDCG@k    : rewards putting higher grades higher. DCG = sum (2^grade - 1) / log2(rank + 1), divided by the best
              possible DCG for these grades, so 1.0 = ideal order and the score is comparable across questions.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def _primary(grades: dict[str, int]) -> set[str]:
    top = max(grades.values())
    return {d for d, g in grades.items() if g == top}


def hit_at_k(ranked: Sequence[str], grades: dict[str, int], k: int) -> float:
    return float(bool(_primary(grades) & set(ranked[:k])))


def recall_at_k(ranked: Sequence[str], grades: dict[str, int], k: int) -> float:
    primary = _primary(grades)
    return len(primary & set(ranked[:k])) / len(primary)


def mrr_at_k(ranked: Sequence[str], grades: dict[str, int], k: int = 10) -> float:
    primary = _primary(grades)
    return next((1.0 / i for i, d in enumerate(ranked[:k], 1) if d in primary), 0.0)


def ndcg_at_k(ranked: Sequence[str], grades: dict[str, int], k: int = 10) -> float:
    dcg = sum((2 ** grades.get(d, 0) - 1) / math.log2(i + 1) for i, d in enumerate(ranked[:k], 1))
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 1) for i, g in enumerate(ideal, 1))
    return dcg / idcg if idcg else 0.0


def score(ranked: Sequence[str], grades: dict[str, int]) -> dict[str, float]:
    return {"hit@5": hit_at_k(ranked, grades, 5), "recall@10": recall_at_k(ranked, grades, 10),
            "mrr@10": mrr_at_k(ranked, grades, 10), "ndcg@10": ndcg_at_k(ranked, grades, 10)}


def mean(rows: Sequence[dict[str, float]]) -> dict[str, float]:
    return {m: round(sum(r[m] for r in rows) / len(rows), 3) for m in rows[0]} if rows else {}
