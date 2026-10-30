"""Model routing (phase 6): spend LLM calls only where they change the result.

A question that is essentially an identifier ("T1047", "ما هي T1486؟") is answered by exact-ID pinning: the
named document goes first whatever the reranker says, so translating and reranking it buys nothing. Everything
else takes the full pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from core.text import identifiers, tokenize

MAX_ID_QUESTION_TOKENS = 4  # "ما هي التقنية T1003.001" = 4 tokens after normalisation


@dataclass(frozen=True)
class Route:
    translate: bool
    rerank: bool
    reason: str


def route(question: str, pinned: list[str]) -> Route:
    ids = identifiers(question)
    if len(pinned) == 1 and len(ids) == 1:
        # Words other than the ID (an old ID pins its replacement, so compare with the typed ID, not the pin).
        # tokenize() also emits the base of a dotted ID ("t1003" for "t1003.001"); that is not a word either.
        words = [t for t in tokenize(question) if t not in {ids[0], ids[0].split(".")[0]}]
        if len(words) + 1 <= MAX_ID_QUESTION_TOKENS:
            return Route(translate=False, rerank=False, reason="id_lookup")
    return Route(translate=True, rerank=True, reason="full")
