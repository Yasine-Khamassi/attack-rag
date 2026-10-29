"""Query translation: search in the documents' language as well as the user's.

Keyword search can only match words that appear in the documents. A question written in another script
(Arabic question, English documents) shares no words with them except IDs and tool names, so keyword search
contributes nothing. Translating the question with a small local LLM and searching with both versions fixes
that; dense search also gets a second, same-language view of the question.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

_ARABIC = re.compile(r"[؀-ۿ]")
_LETTER = re.compile(r"[^\W\d_]")


def arabic_share(text: str) -> float:
    letters = _LETTER.findall(text)
    return len(_ARABIC.findall(text)) / len(letters) if letters else 0.0


class OllamaTranslator:
    """Translate Arabic-script questions to `target` (e.g. "English"). Other text passes through unchanged."""

    def __init__(self, model: str = "qwen2.5:3b", target: str = "English", domain: str = "",
                 url: str | None = None, threshold: float = 0.3, timeout: float = 300):
        self.model, self.target, self.threshold, self.timeout = model, target, threshold, timeout
        self.url = (url or os.environ.get("OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        subject = f"{domain} question" if domain else "question"
        self.prompt = (f"Translate this {subject} into {target}. Keep technical terms, names and IDs exactly as "
                       f"written. Reply with the {target} translation only.\n\n")
        self._cache: dict[str, str] = {}

    def needs_translation(self, text: str) -> bool:
        return arabic_share(text) >= self.threshold

    def translate(self, text: str) -> str | None:
        """Translated text, or None when the text doesn't need translating."""
        if not self.needs_translation(text):
            return None
        if text not in self._cache:
            body = json.dumps({"model": self.model, "prompt": self.prompt + text, "stream": False,
                               "options": {"temperature": 0}}).encode("utf-8")
            req = urllib.request.Request(f"{self.url}/api/generate", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                self._cache[text] = json.load(resp)["response"].strip().strip('"')
        return self._cache[text]
