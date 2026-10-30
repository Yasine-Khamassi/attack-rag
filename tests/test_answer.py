from types import SimpleNamespace

import pytest

from core.answer import NO_ANSWER, Answerer, check, claim_sentences

SOURCES = [SimpleNamespace(doc_id=f"D{i}", chunk_id=f"D{i}#0", title=f"Doc {i}", url=None) for i in (1, 2)]
CHUNKS = {"D1#0": "first source text", "D2#0": "second source text"}


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def complete(self, prompt, system=None, max_tokens=1024, effort="low"):
        self.calls.append({"prompt": prompt, "system": system})
        return self.reply


def answer(reply, question="How does this attack work?"):
    llm = FakeLLM(reply)
    return Answerer(llm, persona="You help analysts.").answer(question, SOURCES, CHUNKS, guards=False), llm


def test_fully_cited_answer():
    a, llm = answer("Attackers inject code into processes [1]. Defenders watch memory access patterns [2][1].")
    assert not a.refused and a.cited == [1, 2] and a.uncited == [] and a.invalid_citations == []
    assert a.citation_coverage == 1.0 and a.language_ok
    assert '<source n="1">\nfirst source text' in llm.calls[0]["prompt"]
    assert "You help analysts." in llm.calls[0]["system"]


def test_uncited_and_invalid_citations_are_reported():
    a, _ = answer("Attackers inject code into processes [1]. Bad ref here too [7].\nThis line has no source at all.")
    assert a.uncited == ["This line has no source at all."] and a.invalid_citations == [7] and a.cited == [1]
    assert a.citation_coverage == pytest.approx(2 / 3)


def test_refusal():
    a, _ = answer(NO_ANSWER)
    assert a.refused and a.citation_coverage == 1.0 and a.cited == []


def test_no_sources_means_refusal_without_calling_the_model():
    llm = FakeLLM("should not be used")
    a = Answerer(llm, persona="p").answer("q", [], {})
    assert a.refused and llm.calls == []


def test_language_must_match_question():
    assert not check("كيف يعمل هذا الهجوم؟", "Attackers inject code into processes [1].", []).language_ok
    assert check("كيف يعمل هذا الهجوم؟", "يحقن المهاجمون شيفرة داخل العمليات الأخرى [1].", []).language_ok


def test_short_fragments_are_not_claims():
    assert claim_sentences("Mitigations:\n- Restrict PowerShell execution to administrators [1].") == [
        "- Restrict PowerShell execution to administrators [1]."]


def test_stray_full_stop_inside_a_cited_claim():
    text = "- Block files such as .scr and exe. or pif from untrusted links [3].\n- Restrict scripts to admins [4]."
    assert claim_sentences(text) == ["- Block files such as .scr and exe. or pif from untrusted links [3].",
                                     "- Restrict scripts to admins [4]."]
    a = check("q", text, [])
    assert a.uncited == [] and a.invalid_citations == [3, 4]


def test_text_after_the_last_citation_is_uncited():
    a = check("q", "Attackers dump LSASS memory [1]. Then they move laterally with stolen hashes.", [])
    assert a.uncited == ["Then they move laterally with stolen hashes."]


def test_faithfulness_judge():
    from core.judge import FaithfulnessJudge

    class Judge:
        def __init__(self):
            self.prompts = []

        def complete(self, prompt, max_tokens=2000, effort="low"):
            self.prompts.append(prompt)
            return "1: yes\n2: Partial"

    sources = [SimpleNamespace(n=1, text="alpha text"), SimpleNamespace(n=2, text="beta text"),
               SimpleNamespace(n=3, text="unused text")]
    a = check("q", "Attackers dump LSASS memory [1].\nThey use stolen hashes later [2].\nNo source for this line.",
              sources)
    llm = Judge()
    v = FaithfulnessJudge(llm).judge(a)
    assert [x for _, x in v.verdicts] == ["yes", "partial", "uncited"]
    assert v.faithfulness == pytest.approx((1 + 0.5 + 0) / 3)
    assert "[1] alpha text" in llm.prompts[0] and "unused text" not in llm.prompts[0]
    assert FaithfulnessJudge(llm).judge(check("q", NO_ANSWER, sources)) is None


def test_list_lead_in_is_a_heading():
    text = "Turla uses several malware families and tools, including:\n- Carbon backdoor for espionage [1]."
    assert claim_sentences(text) == ["- Carbon backdoor for espionage [1]."]


def test_citation_guard_removes_uncited_claims():
    from core.answer import strip_uncited
    text = ("Attackers request service tickets [1]. The sources do not describe recent campaigns.\n"
            "Carbon is one of its tools:\n- Carbon backdoor for espionage [2].\n"
            "You can review these IDs in ATT&CK for details.")
    kept, removed = strip_uncited(text)
    assert kept == "Attackers request service tickets [1].\nCarbon is one of its tools:\n- Carbon backdoor for espionage [2]."
    assert removed == ["The sources do not describe recent campaigns.", "You can review these IDs in ATT&CK for details."]


class SequenceLLM:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, prompt, system=None, max_tokens=1024, effort="low"):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def test_guards_in_answerer():
    llm = SequenceLLM("T1122 is a revoked ID of T1546.015 [1]. In the Arabic answer, IDs stay in Latin script.",
                      "T1122 معرّف قديم للتقنية T1546.015 [1]. في الإجابة العربية تبقى المعرفات بالحروف اللاتينية.")
    a = Answerer(llm, persona="p").answer("ما معنى T1122؟", SOURCES, CHUNKS)
    assert a.language_repaired and "Rewrite this answer in Arabic" in llm.prompts[1]
    assert a.text == "T1122 معرّف قديم للتقنية T1546.015 [1]." and a.removed and a.uncited == []
    assert a.language_ok and a.citation_coverage == 1.0


def test_nothing_cited_becomes_refusal():
    llm = SequenceLLM("This answer has no citations at all, sadly.")
    a = Answerer(llm, persona="p").answer("How does it work?", SOURCES, CHUNKS)
    assert a.refused and a.removed == ["This answer has no citations at all, sadly."]
    raw = Answerer(SequenceLLM("This answer has no citations at all, sadly."), persona="p").answer(
        "How does it work?", SOURCES, CHUNKS, guards=False)
    assert not raw.refused and raw.uncited


def test_language_counts_words_not_only_letters():
    from core.query import arabic_share, is_arabic
    mixed = "ما هو Mimikatz وPowerSploit؟"  # Arabic sentence, Latin names: far more Latin letters than Arabic
    assert arabic_share(mixed) < 0.3 and is_arabic(mixed)
    assert is_arabic("ما هي التقنية T1003.001؟") and not is_arabic("What is T1059?") and not is_arabic("T1003")
    assert check(mixed, "تستخدم المجموعة البرمجية [1].", [SimpleNamespace(n=1, doc_id="x", text="")]).language_ok
