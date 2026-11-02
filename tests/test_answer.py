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


# --- questions back to the reader, conversation context, explicit language -------------------------------------------
def guarded(reply, question="How do I start?", **kw):
    llm = FakeLLM(reply)
    return Answerer(llm, persona="p", **kw).answer(question, SOURCES, CHUNKS), llm


def test_a_question_back_is_refused_unless_allowed():
    only = "Which part of this do you want to know about?"
    assert guarded(only)[0].refused
    a = guarded(only, allow_questions=True)[0]
    assert not a.refused and a.text == only


def test_a_closing_question_survives_only_when_allowed():
    cited = "Attackers inject code into processes [1]."
    closing = "Do you want the detection steps as well?"
    assert guarded(f"{cited} {closing}")[0].text == cited
    assert guarded(f"{cited} {closing}", allow_questions=True)[0].text == f"{cited} {closing}"


def test_a_claim_is_not_let_through_by_a_question_mark():
    cited = "Attackers inject code into processes [1]."
    for sneaky in ("Did you know that the fix costs 500 this week?", "Have you seen https://example.com/a yet?",
                   "Would it surprise you that " + "very " * 20 + "many systems are open?",
                   "Every system here is patched and fully safe today, anything else?",
                   "كل الأنظمة هنا محمية بالكامل وما فيها أي خطر، تبغى شي ثاني؟",
                   "Do you want the fix that stops 50% of these attacks?"):
        assert guarded(f"{cited} {sneaky}", allow_questions=True)[0].text == cited
        assert guarded(sneaky, allow_questions=True)[0].refused
    assert guarded("Did you know that the fix costs 500 this week?", allow_questions=True)[0].refused


def test_a_question_may_repeat_a_number_the_reader_gave():
    asked = "Which version of 2002 do you mean exactly?"
    assert not guarded(asked, question="What changed in 2002?", allow_questions=True)[0].refused
    assert not guarded(asked, question="What changed in ٢٠٠٢?", allow_questions=True)[0].refused  # Arabic digits
    assert guarded(asked, question="What changed lately?", allow_questions=True)[0].refused
    # A small number isn't a reference: "can I get 90 off" must not make "Do you want the 90 off?" safe to say.
    assert guarded("Do you want the 90 off applied now?", question="can I get 90 off", allow_questions=True)[0].refused


def test_a_short_opening_before_the_question_is_fine():
    assert not guarded("Sure, which part do you mean?", allow_questions=True)[0].refused


def test_an_uncited_claim_beside_a_question_still_goes():
    a = guarded("This product line is very widely used by everyone. Which part do you mean?", allow_questions=True)[0]
    assert a.text == "Which part do you mean?" and a.removed


def test_custom_rules_replace_the_defaults():
    _, llm = guarded("Attackers inject code [1].", rules="Our own rules.")
    assert "Our own rules." in llm.calls[0]["system"] and "numbered sources below" not in llm.calls[0]["system"]
    _, llm = guarded("Attackers inject code [1].")
    assert "numbered sources below" in llm.calls[0]["system"]


def test_history_is_context_and_comes_before_the_sources():
    llm = FakeLLM("Attackers inject code into processes [1].")
    Answerer(llm, persona="p").answer("And then?", SOURCES, CHUNKS, history="User: first\nYou: second")
    prompt = llm.calls[0]["prompt"]
    assert prompt.index("<conversation_so_far>") < prompt.index("Sources:") and "User: first" in prompt
    llm = FakeLLM("Attackers inject code into processes [1].")
    Answerer(llm, persona="p").answer("And then?", SOURCES, CHUNKS)
    assert "conversation_so_far" not in llm.calls[0]["prompt"]


def test_an_explicit_language_decides_the_check_and_the_prompt():
    from core.query import written_in
    assert written_in("X Y uses 110 ports", "en") and not written_in("هذا النص بالعربي فقط", "en")
    assert written_in("سعره عشرة", "ar") and written_in("", "ar") and written_in("", "en")
    # Long Arabic names can tip the letter count, but this is English with names in it.
    english = "With this tool you can get several good results like كشف الهجمات السريع for your team today"
    assert written_in(english, "en") and not written_in(english, "ar")
    llm = FakeLLM("هذا جواب بالعربي كما طلب السائل تماما [1].")
    a = Answerer(llm, persona="p").answer("How much is X?", SOURCES, CHUNKS, language="ar")
    assert a.language_ok and not a.language_repaired and "Write the answer in Arabic." in llm.calls[0]["prompt"]
    assert "Write the answer in" not in guarded("Attackers inject code [1].")[1].calls[0]["prompt"]

def test_a_stretch_of_english_inside_an_arabic_answer_is_caught():
    from core.query import written_in
    mixed = "In most cases, the attackers are using Mimikatz، PowerSploit، كلها أدوات تسرق البيانات من الأجهزة."
    assert not written_in(mixed, "ar")
    assert written_in("Cobalt Strike Beacon أداة تسرق البيانات من الأجهزة.", "ar")  # a name isn't a sentence
    assert written_in("cobalt strike أداة تسرق البيانات من الأجهزة.", "ar")  # nor is a lowercase one
    assert written_in("تواصل معنا على help@example.com أو https://example.com/a/b.", "ar")

def test_a_failed_language_rewrite_never_reaches_the_reader():
    class Rewrites(FakeLLM):
        def complete(self, prompt, system=None, max_tokens=1024, effort="low"):
            self.calls.append({"prompt": prompt, "system": system})
            return self.reply if system else "The text you've given is already fine, so here is a note about it [1]."
    a = Answerer(Rewrites("The first answer came out in the wrong language entirely [1]."), persona="p").answer(
        "سؤال بالعربي؟", SOURCES, CHUNKS, language="ar")
    assert a.refused and a.language_repaired
    # Without an explicit language, existing callers keep whatever the rewrite gave.
    b = Answerer(Rewrites("The first answer came out in the wrong language entirely [1]."), persona="p").answer(
        "سؤال بالعربي؟", SOURCES, CHUNKS)
    assert not b.refused and "already fine" in b.text


def test_the_language_is_repeated_after_tool_results():
    class ToolLLM:
        def __init__(self):
            self.messages = []

        def create(self, messages, system=None, max_tokens=1024, effort="low", tools=None):
            self.messages = list(messages)
            if len(messages) == 1:
                call = SimpleNamespace(type="tool_use", id="1", name="lookup", input={})
                return SimpleNamespace(content=[call])
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="الجواب هنا مع رقم المصدر الصحيح [1].")])

    tools = SimpleNamespace(specs=lambda t: [{"name": "lookup"}], call=lambda t, n, a: ("result", False))
    llm = ToolLLM()
    Answerer(llm, persona="p").answer("سؤال؟", SOURCES, CHUNKS, tools=tools, tenant_id="t", language="ar")
    last = llm.messages[-1]["content"]
    assert last[0]["type"] == "tool_result" and "Write the answer in Arabic." in last[-1]["text"]
    llm = ToolLLM()
    Answerer(llm, persona="p").answer("سؤال؟", SOURCES, CHUNKS, tools=tools, tenant_id="t")
    assert all(block["type"] == "tool_result" for block in llm.messages[-1]["content"])

def test_thinking_is_passed_on_only_when_asked():
    seen = {}

    class Records:
        def complete(self, prompt, system=None, max_tokens=1024, effort="low", thinking=False):
            seen.update(thinking=thinking, max_tokens=max_tokens)
            return "Attackers inject code into processes [1]."
    Answerer(Records(), persona="p", thinking=True).answer("How?", SOURCES, CHUNKS)
    assert seen == {"thinking": True, "max_tokens": 2048}  # thinking counts as output
    Answerer(Records(), persona="p").answer("How?", SOURCES, CHUNKS)
    assert seen == {"thinking": False, "max_tokens": 1024}

def test_a_name_in_lowercase_english_isnt_an_english_sentence():
    from core.query import written_in
    assert written_in("تستخدم المجموعة cobalt strike beacon لسرقة البيانات من الأجهزة.", "ar")
    assert not written_in("تستخدم المجموعة أدوات كثيرة، this is the one for you لسرقة البيانات.", "ar")
    assert written_in("تستخدم المجموعة cat and mouse game لسرقة البيانات من الأجهزة.", "ar", strict=False)


def test_a_rewrite_that_keeps_a_name_is_accepted():
    class Rewrites(FakeLLM):
        def complete(self, prompt, system=None, max_tokens=1024, effort="low"):
            return self.reply if system else "تستخدم المجموعة cat and mouse game لسرقة البيانات من الأجهزة [1]."
    a = Answerer(Rewrites("The group plays the cat and mouse game to steal data right now [1]."), persona="p").answer(
        "سؤال بالعربي؟", SOURCES, CHUNKS, language="ar")
    assert not a.refused and a.language_repaired and "mouse" in a.text
