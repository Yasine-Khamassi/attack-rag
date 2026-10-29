"""BM25 keyword ranking, written out so every part can be explained.

score(q, d) = sum over query terms t of  idf(t) * tf(t,d) * (k1 + 1) / (tf(t,d) + k1 * (1 - b + b * |d| / avg|d|))

- idf: rare terms (an ID like "t1003.001") count far more than common ones ("the", "attack").
- k1 caps how much repeating a term helps (saturation); b normalises for document length.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

from core.text import tokenize


class BM25:
    def __init__(self, ids: Sequence[str], texts: Sequence[str], k1: float = 1.2, b: float = 0.75):
        self.ids = list(ids)
        self.k1, self.b = k1, b
        self.tfs = [Counter(tokenize(t)) for t in texts]
        self.lengths = [sum(tf.values()) for tf in self.tfs]
        self.avg_len = sum(self.lengths) / max(len(self.lengths), 1)
        df = Counter(term for tf in self.tfs for term in tf)
        n = len(self.tfs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.postings: dict[str, list[int]] = {}
        for i, tf in enumerate(self.tfs):
            for term in tf:
                self.postings.setdefault(term, []).append(i)

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        scores: dict[int, float] = {}
        for term in set(tokenize(query)):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i in self.postings[term]:
                tf = self.tfs[i][term]
                norm = self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avg_len)
                scores[i] = scores.get(i, 0.0) + idf * tf * (self.k1 + 1) / (tf + norm)
        top = sorted(scores.items(), key=lambda x: -x[1])[:k]
        return [(self.ids[i], s) for i, s in top]
