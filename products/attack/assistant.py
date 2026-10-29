"""The ATT&CK analyst assistant: retrieval (hybrid + Haiku rerank) + a cited answer from Claude Haiku 5.5.

Used by the web API (products/attack/api.py) and the answer eval (products/attack/eval_answers.py).

Try it:  python -m products.attack.assistant "How do I detect Kerberoasting?"
"""

from __future__ import annotations

import json
import sys
import threading
import time

from core import db
from core.answer import Answer, Answerer
from core.embeddings import DEFAULT_MODEL, OllamaEmbedder
from core.llm import Claude
from core.query import ClaudeTranslator
from core.rerank import ClaudeReranker
from core.schema import Document
from core.search import HybridSearcher
from products.attack.eval_golden import EXPAND_TOP, load_links
from products.attack.ingest import DOCS_PATH, TENANT_ID

LLM_MODEL = "claude-haiku-5-5"
PERSONA = ("You are an assistant for SOC analysts. You answer questions about adversary behaviour, detection and "
           "mitigation using MITRE ATT&CK. Be precise and practical; mention ATT&CK IDs when the sources give them. "
           "In a source header, 'Previously: T1032' means T1032 is an older, revoked ID of that same entry, so a "
           "question about T1032 is answered by that source.")


class Assistant:
    def __init__(self):
        with DOCS_PATH.open(encoding="utf-8") as f:
            aliases = {old: d.doc_id for d in map(Document.model_validate_json, f)
                       for old in d.metadata.get("previous_ids", [])}
        self.conn = db.connect()
        self.searcher = HybridSearcher(self.conn, TENANT_ID, OllamaEmbedder(DEFAULT_MODEL), aliases=aliases,
                                       links=load_links())
        self.translator = ClaudeTranslator(LLM_MODEL, target="English", domain="cybersecurity")
        self.reranker = ClaudeReranker(LLM_MODEL)
        self.answerer = Answerer(Claude(LLM_MODEL, purpose="answer"), persona=PERSONA)
        self.lock = threading.Lock()  # one database connection: serialise requests

    def ask(self, question: str) -> tuple[Answer, dict]:
        with self.lock:
            t0 = time.perf_counter()
            hits = self.searcher.hybrid(question, k=10, translator=self.translator, pin_ids=True,
                                        reranker=self.reranker, expand_top=EXPAND_TOP)
            t1 = time.perf_counter()
            answer = self.answerer.answer(question, hits, self.searcher.chunk_text)
            t2 = time.perf_counter()
        return answer, {"retrieval_s": round(t1 - t0, 2), "answer_s": round(t2 - t1, 2)}


def to_json(answer: Answer, timing: dict) -> dict:
    return {
        "question": answer.question, "answer": answer.text, "refused": answer.refused, "timing": timing,
        "sources": [{"n": s.n, "doc_id": s.doc_id, "title": s.title, "url": s.url, "cited": s.n in answer.cited}
                    for s in answer.sources],
        "checks": {"citation_coverage": round(answer.citation_coverage, 3), "uncited": answer.uncited,
                   "invalid_citations": answer.invalid_citations, "language_ok": answer.language_ok,
                   "removed_uncited": answer.removed, "language_repaired": answer.language_repaired},
    }


if __name__ == "__main__":
    assistant = Assistant()
    for q in sys.argv[1:] or ["How do I detect Kerberoasting?"]:
        print(json.dumps(to_json(*assistant.ask(q)), ensure_ascii=False, indent=1))
