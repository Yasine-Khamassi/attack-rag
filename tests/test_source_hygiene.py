"""Source files must not contain control characters: an invisible backspace (\\x08 where \\b was meant) once broke
guardrail regexes silently (DECISIONS D34)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_control_characters_in_source():
    bad = []
    for path in [*ROOT.glob("core/**/*.py"), *ROOT.glob("products/**/*.py"), *ROOT.glob("tests/**/*.py")]:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(ord(ch) < 32 and ch != "\t" for ch in line):
                bad.append(f"{path.relative_to(ROOT)}:{n}")
    assert not bad, f"control characters in: {bad}"
