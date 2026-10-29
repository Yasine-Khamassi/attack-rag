"""Cross-encoder reranking: the last, most accurate (and slowest) ordering step.

Dense search compares two vectors computed separately; BM25 counts shared words. A cross-encoder reads the
question and one candidate passage *together* and outputs a relevance score, so it can tell a passage that
answers the question apart from one that merely shares its words. Too slow to run over every chunk, so it
only re-orders the top candidates from hybrid search.
"""

from __future__ import annotations

from collections.abc import Sequence

# Candidates, all multilingual (Arabic included):
RERANKERS = {
    "bge-reranker-v2-m3": "BAAI/bge-reranker-v2-m3",                            # 568M params
    "mmarco-minilm": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",               # 118M params
}


class CrossEncoderReranker:
    def __init__(self, name: str = "bge-reranker-v2-m3", max_length: int = 512, batch_size: int = 8):
        from sentence_transformers import CrossEncoder  # heavy import (torch); only when reranking is used

        self.name = name
        self.batch_size = batch_size
        self.model = CrossEncoder(RERANKERS.get(name, name), max_length=max_length, device="cpu")

    def scores(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        return [float(s) for s in self.model.predict([(query, p) for p in passages], batch_size=self.batch_size)]


class ClaudeReranker:
    """Listwise reranking by an LLM: it sees the question and all candidates at once and returns them in
    order of usefulness. Reads any language, so the original question is enough (no translation needed).

    scores() keeps the cross-encoder contract: higher = more relevant. Passages the model leaves out keep
    their incoming order, below every passage it ranked."""

    reads_any_language = True

    def __init__(self, model: str = "claude-haiku-5-5", max_chars: int = 1500):
        from core.llm import Claude

        self.name, self.max_chars = model, max_chars
        self.llm = Claude(model, purpose="rerank")

    def scores(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        listing = "\n\n".join(f"[{i}] {p[:self.max_chars]}" for i, p in enumerate(passages, 1))
        prompt = (f"Question: {query}\n\nSearch results:\n\n{listing}\n\n"
                  "Rank the results by how directly they answer the question. Reply with the result numbers "
                  "only, most relevant first, comma-separated (e.g. 4,1,7). Leave out results that are "
                  "not relevant at all.")
        reply = self.llm.complete(prompt, max_tokens=200)
        order = []
        for part in reply.replace("\n", ",").split(","):
            digits = "".join(ch for ch in part if ch.isdigit())
            if digits and 1 <= int(digits) <= len(passages) and int(digits) - 1 not in order:
                order.append(int(digits) - 1)
        n = len(passages)
        rank = {idx: pos for pos, idx in enumerate(order)}
        return [float(2 * n - rank[i]) if i in rank else float(n - i) / n for i in range(n)]
