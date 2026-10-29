"""Faithfulness: is every claim in an answer supported by the sources it cites?

A stronger model than the one that wrote the answer reads each claim with the full text of its cited sources and
says yes (fully supported), partial, or no. Faithfulness = (yes + 0.5 * partial) / claims. Claims without a valid
citation count as unsupported without asking the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.answer import _CITATION, Answer, claim_sentences

PROMPT = """You check whether answers are supported by their sources. For each numbered claim, look ONLY at the
sources it cites (the [n] markers) and decide:
yes     = everything the claim states is in those sources
partial = some of it is in the sources, some is not
no      = the cited sources do not support it

Reply with one line per claim, exactly in this form and nothing else:
1: yes
2: partial

Sources:
{sources}

Claims:
{claims}"""
_VERDICT = re.compile(r"^\s*(\d+)\s*[:.)-]\s*(yes|partial|no)\b", re.IGNORECASE | re.MULTILINE)
SCORE = {"yes": 1.0, "partial": 0.5, "no": 0.0}


@dataclass(frozen=True)
class Verdict:
    faithfulness: float           # 0..1
    verdicts: list[tuple[str, str]]  # (claim, yes|partial|no|uncited)


class FaithfulnessJudge:
    def __init__(self, llm, effort: str = "low"):
        self.llm, self.effort = llm, effort

    def judge(self, answer: Answer) -> Verdict | None:
        """None for refusals (nothing to judge)."""
        if answer.refused:
            return None
        claims = claim_sentences(answer.text)
        if not claims:
            return Verdict(1.0, [])
        valid = {s.n for s in answer.sources}
        cited = [c for c in claims if {int(n) for n in _CITATION.findall(c)} & valid]
        results: dict[str, str] = {c: "uncited" for c in claims if c not in cited}
        if cited:
            used = sorted({int(n) for c in cited for n in _CITATION.findall(c)} & valid)
            sources = "\n\n".join(f"[{s.n}] {s.text}" for s in answer.sources if s.n in used)
            numbered = "\n".join(f"{i}: {c}" for i, c in enumerate(cited, 1))
            reply = self.llm.complete(PROMPT.format(sources=sources, claims=numbered), max_tokens=2000,
                                      effort=self.effort)
            found = {int(i): v.lower() for i, v in _VERDICT.findall(reply)}
            for i, c in enumerate(cited, 1):
                results[c] = found.get(i, "no")  # an unparseable verdict counts against the answer
        ordered = [(c, results[c]) for c in claims]
        return Verdict(sum(SCORE.get(v, 0.0) for _, v in ordered) / len(ordered), ordered)
