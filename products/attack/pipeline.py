"""The production search call, shared by the assistant and the evals so they measure exactly the same thing:
settings from products/attack/settings.py + model routing (core/routing.py)."""

from __future__ import annotations

import json
from collections import defaultdict

from core.routing import route
from core.search import Hit, HybridSearcher
from products.attack.ingest import REL_PATH
from products.attack.settings import EXPAND_TOP, RERANK_DEPTH


def load_links() -> dict[str, list[str]]:
    """technique -> its mitigations and detection strategies (the one-hop answers to "how do I stop/spot X")."""
    links: dict[str, list[str]] = defaultdict(list)
    with REL_PATH.open(encoding="utf-8") as f:
        for r in map(json.loads, f):
            if r["relation"] in ("mitigates", "detects"):
                links[r["target"]].append(r["source"])
    return dict(links)


def search(searcher: HybridSearcher, question: str, translator, reranker, k: int = 10) -> tuple[list[Hit], str]:
    """Returns (hits, route reason). ID-only questions skip translation and reranking (pinning decides)."""
    r = route(question, searcher.pinned(question))
    hits = searcher.hybrid(question, k=k, translator=translator if r.translate else None, pin_ids=True,
                           reranker=reranker if r.rerank else None, rerank_depth=RERANK_DEPTH, expand_top=EXPAND_TOP)
    return hits, r.reason
