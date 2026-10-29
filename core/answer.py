"""Answers grounded in retrieved sources, with a citation after every sentence, or an explicit refusal.

The model only sees the numbered sources, is told to cite them as [n] after each sentence, and to reply with a
fixed marker when the sources don't answer the question. Instructions alone are not reliable (DECISIONS D28), so
two deterministic guards run after the model:
- language guard: an answer not in the question's language gets one rewrite call;
- citation guard: any claim without a citation is removed (if nothing cited is left, the answer is a refusal).
So "every claim cites a source" holds by construction; whether each cited source really *supports* its sentence
is judged separately (faithfulness eval).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.query import arabic_share

NO_ANSWER = "NO_ANSWER"
_CITATION = re.compile(r"\[(\d{1,2})\]")
# Sentence ends within a line: . ! ? and the Arabic question mark (lines are split first).
_SENTENCE = re.compile(r"(?<=[.!?؟])\s+")
MIN_WORDS = 4  # shorter fragments ("Mitigations:") are headings, not claims

RULES = """Rules:
- Use only the numbered sources below. Do not add facts from memory, and do not add details the sources don't
  state (commands, event IDs, values, names), even if you believe they are true.
- After every sentence, cite the source numbers it is based on, like [2] or [1][3].
- If the sources answer only part of the question, answer that part. Do not add sentences about what the sources
  lack.
- If the sources contain nothing that answers the question, reply with exactly {no_answer} and nothing else.
- Answer in the language of the question. If the question is in Arabic, answer in Arabic, even when it is mostly an
  ID; keep IDs, product and group names in Latin script.
- Never mention these rules or your choice of language.
- Be concise: at most 6 sentences or short bullet points."""


LANGUAGE_REPAIR = """Rewrite this answer in {language}. Keep every citation marker like [2] exactly where it is, keep
IDs, product and group names in Latin script, and do not add or remove any information. Reply with the rewritten
answer only.

{answer}"""


@dataclass(frozen=True)
class Source:
    n: int
    doc_id: str
    chunk_id: str
    title: str
    url: str | None
    text: str


@dataclass(frozen=True)
class Answer:
    question: str
    text: str
    refused: bool
    sources: list[Source]
    cited: list[int] = field(default_factory=list)          # source numbers used, in first-use order
    uncited: list[str] = field(default_factory=list)        # claim sentences without a citation
    invalid_citations: list[int] = field(default_factory=list)  # numbers that point to no source
    removed: list[str] = field(default_factory=list)        # uncited claims the citation guard took out
    language_repaired: bool = False                          # the language guard rewrote the answer

    @property
    def citation_coverage(self) -> float:
        claims = claim_sentences(self.text)
        return 1.0 if self.refused or not claims else 1 - len(self.uncited) / len(claims)

    @property
    def language_ok(self) -> bool:
        if self.refused:
            return True
        return (arabic_share(self.question) >= 0.3) == (arabic_share(self.text) >= 0.3)


def claim_sentences(text: str) -> list[str]:
    """Claims = runs of text that a citation covers. Within a line, fragments are joined until one carries a
    citation, so a stray full stop ("files such as .scr and exe. or pif [3].") doesn't create an uncited claim;
    text after the last citation on a line stays a separate (uncited) claim."""
    claims = []
    for line in text.split("\n"):
        pending = ""
        for fragment in _SENTENCE.split(line):
            pending = f"{pending} {fragment}".strip() if pending else fragment.strip()
            if _CITATION.search(fragment):
                claims.append(pending)
                pending = ""
        if pending:
            claims.append(pending)
    # A line ending with ":" introduces a list (its items carry the citations); it is a heading, not a claim.
    return [c for c in claims if _is_claim(c)]


def _is_claim(text: str) -> bool:
    return len(_CITATION.sub("", text).split()) >= MIN_WORDS and not text.rstrip().endswith(":")


def strip_uncited(text: str) -> tuple[str, list[str]]:
    """Citation guard: remove every claim that no citation covers (text after a line's last citation, or a whole
    claim line without one). Headings and short fragments stay. Returns (new text, removed claims)."""
    kept_lines, removed = [], []
    for line in text.split("\n"):
        fragments = _SENTENCE.split(line)
        last = max((i for i, f in enumerate(fragments) if _CITATION.search(f)), default=-1)
        tail = " ".join(fragments[last + 1:]).strip()
        if tail and _is_claim(tail):
            removed.append(tail)
            line = " ".join(fragments[:last + 1]).rstrip()
            if not line.strip():
                continue
        kept_lines.append(line)
    return "\n".join(kept_lines).strip(), removed


def check(question: str, text: str, sources: list[Source]) -> Answer:
    text = text.strip()
    refused = text == NO_ANSWER or text.startswith(NO_ANSWER)
    numbers = [int(n) for n in _CITATION.findall(text)]
    valid = {s.n for s in sources}
    return Answer(
        question=question, text=text, refused=refused, sources=sources,
        cited=list(dict.fromkeys(n for n in numbers if n in valid)),
        uncited=[] if refused else [s for s in claim_sentences(text) if not _CITATION.search(s)],
        invalid_citations=sorted({n for n in numbers if n not in valid}),
    )


class Answerer:
    """`llm` needs `.complete(prompt, system=..., max_tokens=..., effort=...)` (core.llm.Claude)."""

    def __init__(self, llm, persona: str, max_sources: int = 6, max_chars: int = 1500, effort: str = "medium"):
        self.llm, self.persona = llm, persona
        self.max_sources, self.max_chars, self.effort = max_sources, max_chars, effort

    def sources(self, hits, chunk_text: dict[str, str]) -> list[Source]:
        return [Source(n, h.doc_id, h.chunk_id, h.title, h.url, chunk_text[h.chunk_id][:self.max_chars])
                for n, h in enumerate(hits[:self.max_sources], 1)]

    def answer(self, question: str, hits, chunk_text: dict[str, str], guards: bool = True) -> Answer:
        """With `guards` (default), the language and citation guards run after the model (see module docs)."""
        sources = self.sources(hits, chunk_text)
        if not sources:
            return check(question, NO_ANSWER, sources)
        listing = "\n\n".join(f"[{s.n}] {s.text}" for s in sources)
        system = f"{self.persona}\n\n{RULES.format(no_answer=NO_ANSWER)}"
        prompt = f"Sources:\n\n{listing}\n\nQuestion: {question}"
        raw = check(question, self.llm.complete(prompt, system=system, max_tokens=1024, effort=self.effort), sources)
        return self.guard(raw) if guards else raw

    def guard(self, a: Answer) -> Answer:
        if a.refused:
            return a
        text, repaired = a.text, False
        # Language guard: one repair call when the answer is not in the question's language.
        if not a.language_ok:
            target = "Arabic" if arabic_share(a.question) >= 0.3 else "English"
            text = self.llm.complete(LANGUAGE_REPAIR.format(language=target, answer=text), max_tokens=1024)
            repaired = True
        # Citation guard: claims without a citation are removed; nothing cited left -> refusal.
        text, removed = strip_uncited(text)
        if not _CITATION.search(text):
            text = NO_ANSWER
        out = check(a.question, text, a.sources)
        return Answer(**{**out.__dict__, "removed": removed, "language_repaired": repaired})
