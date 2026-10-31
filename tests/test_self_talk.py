"""Self-talk guard (D43): narration about the answer's language or "let me check" is removed; real answers stay."""

import pytest

from core.answer import strip_self_talk


@pytest.mark.parametrize("text,expected", [
    ("The question is in English, so I answer in English. T1059 covers command interpreters [1].",
     "T1059 covers command interpreters [1]."),
    ("Since the question was asked in English, I will answer in English. Mitigation M1038 applies [2].",
     "Mitigation M1038 applies [2]."),
    ("I'll answer in Arabic. التقنية T1059 تخص مفسر الأوامر [1].", "التقنية T1059 تخص مفسر الأوامر [1]."),
    ("The closest technique is... let me check the sources: T1566 Phishing [1].",
     "The closest technique is T1566 Phishing [1]."),
    ("السؤال بالعربي، فسأجيب بالعربي. التقنية T1566 هي التصيد [1].", "التقنية T1566 هي التصيد [1]."),
])
def test_self_talk_is_removed(text, expected):
    cleaned, removed = strip_self_talk(text)
    assert cleaned == expected and removed


@pytest.mark.parametrize("text", [
    "Technique T1059 covers command interpreters [1]. Let me know if you need sub-techniques.",
    "The report is written in English and Arabic [2].",
    "Detection relies on process creation logs [1].",
    "الكشف يعتمد على سجلات العمليات [1].",
])
def test_real_answers_are_untouched(text):
    assert strip_self_talk(text) == (text, [])
