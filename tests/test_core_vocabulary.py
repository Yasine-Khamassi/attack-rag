"""core/ must stay corpus-agnostic (see CLAUDE.md). This fails if a corpus word leaks into it."""

import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
BANNED = re.compile(r"att&ck|\bmitre\b|technique|\btactics?\b|\bstix\b", re.IGNORECASE)


def test_core_has_no_corpus_vocabulary():
    hits = [
        f"{path.name}:{n}: {line.strip()}"
        for path in CORE.rglob("*.py")
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if BANNED.search(line)
    ]
    assert not hits, "corpus vocabulary in core/:\n" + "\n".join(hits)
