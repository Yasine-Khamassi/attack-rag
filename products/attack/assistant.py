"""The ATT&CK analyst assistant: retrieval (hybrid + Haiku rerank) + a cited answer from Claude Haiku 5.5.

Serving path security (phase 4): the database connection logs in as the restricted app role pinned to this
tenant, so row-level security applies to every query (DECISIONS D31); the model can call only this tenant's
allowlisted tools (D33); every turn is written to the tenant's conversation log with its API cost.

Used by the web API (products/attack/api.py) and the answer eval (products/attack/eval_answers.py).

Try it:  python -m products.attack.assistant "How do I detect Kerberoasting?"
"""

from __future__ import annotations

import json
import sys
import threading
import time

from dataclasses import asdict

from core import convlog, db, security_log
from core.answer import NO_ANSWER, Answer, Answerer, check
from core.cache import TTLCache, fingerprint, key, normalize_question
from core.embeddings import DEFAULT_MODEL, OllamaEmbedder
from core.guardrails import Finding, check_input
from core.llm import Claude
from core.query import ClaudeTranslator
from core.rerank import ClaudeReranker
from core.schema import Document
from core.search import HybridSearcher
from products.attack.pipeline import load_links, search
from products.attack.ingest import DOCS_PATH, TENANT_ID
from products.attack.settings import ANSWER_CACHE_SECONDS, LLM_MODEL, RERANK_CHARS, USE_TOOLS
from products.attack.tools import attack_tools

PERSONA = ("You are an assistant for SOC analysts. You answer questions about adversary behaviour, detection and "
           "mitigation using MITRE ATT&CK. Be precise and practical; mention ATT&CK IDs when the sources give them. "
           "In a source header, 'Previously: T1032' means T1032 is an older, revoked ID of that same entry, so a "
           "question about T1032 is answered by that source.")


class Assistant:
    def __init__(self, use_tools: bool = USE_TOOLS, tenant_id: str = TENANT_ID, security: bool = True):
        """`security=False` turns the prompt-injection guards off: only for measuring them (red-team baseline)."""
        self.tenant_id, self.security = tenant_id, security
        with DOCS_PATH.open(encoding="utf-8") as f:
            aliases = {old: d.doc_id for d in map(Document.model_validate_json, f)
                       for old in d.metadata.get("previous_ids", [])}
        # Restricted role pinned to this tenant (RLS). Autocommit: a serving connection must never sit "idle in
        # transaction" holding locks (it blocked schema changes, D37).
        self.conn = db.connect_app(tenant_id, autocommit=True)
        self.searcher = HybridSearcher(self.conn, tenant_id, OllamaEmbedder(DEFAULT_MODEL), aliases=aliases,
                                       links=load_links())
        self.translator = ClaudeTranslator(LLM_MODEL, target="English", domain="cybersecurity")
        self.reranker = ClaudeReranker(LLM_MODEL, max_chars=RERANK_CHARS)
        self.answerer = Answerer(Claude(LLM_MODEL, purpose="answer"), persona=PERSONA, security=security)
        self.tools = attack_tools(self.searcher) if use_tools else None
        self.lock = threading.Lock()  # one database connection: serialise requests
        # Answer cache (phase 6): same tenant + same index content + same normalised question -> same answer.
        self.cache = TTLCache(max_items=2048, ttl_seconds=ANSWER_CACHE_SECONDS)
        self.index_version = fingerprint(self.searcher.chunk_text[c] for c in sorted(self.searcher.chunk_text))

    def ask(self, question: str, conversation_id: str | None = None) -> tuple[Answer, dict]:
        with self.lock:
            cost0, t0 = Claude.total_cost, time.perf_counter()
            # Input guard: explicit attempts to override the rules are refused before any API call.
            if self.security and (found := check_input(question)):
                answer = check(question, NO_ANSWER, [])
                answer = Answer(**{**answer.__dict__, "security": [asdict(f) for f in found]})
                return self._finish(answer, {"retrieval_s": 0.0, "answer_s": 0.0, "cost_usd": 0.0}, conversation_id)
            cache_key = key(self.tenant_id, self.index_version, self.security, normalize_question(question))
            if cached := self.cache.get(cache_key):
                answer = Answer(**{**cached.__dict__, "question": question})
                return self._finish(answer, {"retrieval_s": 0.0, "answer_s": 0.0, "cost_usd": 0.0,
                                             "cache": True, "route": "cache"}, conversation_id)
            hits, route_reason = search(self.searcher, question, self.translator, self.reranker)
            t1 = time.perf_counter()
            answer = self.answerer.answer(question, hits, self.searcher.chunk_text, tools=self.tools,
                                          tenant_id=self.tenant_id)
            t2 = time.perf_counter()
            timing = {"retrieval_s": round(t1 - t0, 2), "answer_s": round(t2 - t1, 2),
                      "cost_usd": round(Claude.total_cost - cost0, 6), "cache": False, "route": route_reason}
            self.cache.put(cache_key, answer)
            return self._finish(answer, timing, conversation_id)

    def _finish(self, answer: Answer, timing: dict, conversation_id: str | None) -> tuple[Answer, dict]:
        for f in answer.security:  # every guardrail hit becomes a SIEM event
            security_log.event(Finding(**f), self.tenant_id, conversation_id)
        if conversation_id:
            convlog.log_turn(self.conn, self.tenant_id, conversation_id,
                             {**to_json(answer, timing), "cost_usd": timing["cost_usd"]})
        return answer, timing


def to_json(answer: Answer, timing: dict) -> dict:
    return {
        "question": answer.question, "answer": answer.text, "refused": answer.refused, "timing": timing,
        "sources": [{"n": s.n, "doc_id": s.doc_id, "title": s.title, "url": s.url, "cited": s.n in answer.cited}
                    for s in answer.sources],
        "checks": {"citation_coverage": round(answer.citation_coverage, 3), "uncited": answer.uncited,
                   "invalid_citations": answer.invalid_citations, "language_ok": answer.language_ok,
                   "removed_uncited": answer.removed, "language_repaired": answer.language_repaired,
                   "quarantined": answer.quarantined, "security": [f["rule"][:40] for f in answer.security]},
    }


if __name__ == "__main__":
    assistant = Assistant()
    for q in sys.argv[1:] or ["How do I detect Kerberoasting?"]:
        print(json.dumps(to_json(*assistant.ask(q)), ensure_ascii=False, indent=1))
