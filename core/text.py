"""Text normalisation and tokenisation for keyword search (English + Arabic).

Keyword search only matches identical tokens, so both the documents and the query go through the same
`tokenize()`. Two things matter most:
- identifiers like "T1003.001" or "CVE-2024-3400" must survive as one token (a plain word splitter would
  cut them into "t1003" + "001" and lose the exact match);
- Arabic has several spellings of the same letter (أ إ آ ا, ة ه, ى ي) plus optional diacritics; without
  normalising them, the same word typed two ways never matches.
"""

from __future__ import annotations

import re
import unicodedata

# Arabic diacritics (tashkeel), Quranic marks, and tatweel (the stretching character ـ).
_AR_MARKS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_AR_LETTERS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})
# Arabic-Indic and Persian digits -> ASCII, so "٢٠٢٤" matches "2024".
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# Identifier: letters followed by digits, optionally with .digit / -digit groups (T1003.001, CVE-2024-3400,
# AN0498). Tried before plain words so it wins.
_TOKEN = re.compile(r"[a-z]+-?\d+(?:[.-]\d+)*|\w+")
# Arabic clitics on the front of a word, stacked like this: conjunction "و"/"ف" (and/so), then
# preposition "ب"/"ك"/"ل" (with/like/for), then the article "ال" ("لل" = "ل" + "ال"). E.g. "وبالهجوم" -> "هجوم".
_AR_PREFIX = re.compile(r"^[وف]?(?:[بك]?ال|ل?ال|لل)(?=\w{2,})")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = _AR_MARKS.sub("", text)
    return text.translate(_AR_LETTERS).translate(_DIGITS)


def _is_arabic(token: str) -> bool:
    return "؀" <= token[0] <= "ۿ"


def identifiers(text: str) -> list[str]:
    """Identifier-like tokens exactly as typed (normalised), without the base-ID expansion of `tokenize`."""
    return [t for t in _TOKEN.findall(normalize(text)) if t[0].isalpha() and any(ch.isdigit() for ch in t)]


def tokenize(text: str) -> list[str]:
    """Normalised tokens. Identifiers stay whole and also add their base ("t1003.001" -> + "t1003"),
    so a question about the parent ID still matches its sub-IDs. Arabic words lose the article "ال"."""
    tokens: list[str] = []
    for tok in _TOKEN.findall(normalize(text)):
        if _is_arabic(tok):
            tok = _AR_PREFIX.sub("", tok)
        tokens.append(tok)
        if "." in tok and tok[0].isalpha():
            tokens.append(tok.split(".", 1)[0])
    return tokens
