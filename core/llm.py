"""Claude API access with a hard spending limit.

Every call's token usage is recorded with its cost in a ledger: by default appended to data/llm_usage.jsonl
(or the file named by LLM_USAGE_LOG). Before each call the total is compared with LLM_BUDGET_USD (from the
environment or .env); at or over the limit the call is refused with BudgetExceeded, so a bug or a loop can never
run up the bill. A server with several short-lived processes plugs in a shared ledger (set_ledger), so the limit
covers all of them, not each one separately (DECISIONS D41).

The API key is read from ANTHROPIC_API_KEY (environment or the gitignored .env). It is never logged.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USAGE_LOG = ROOT / "data" / "llm_usage.jsonl"

# USD per million tokens (input, output), first-party API, prompts up to 100K tokens.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
DEFAULT_MODEL = "claude-haiku-5-5"
WARN_AT = 0.8  # print a warning once spend passes this share of the budget


class BudgetExceeded(RuntimeError):
    pass


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader: KEY=VALUE lines, # comments. Existing environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


class FileLedger:
    """Spend records as JSON lines in one file."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def spent(self) -> float:
        if not self.path.exists():
            return 0.0
        with self.path.open(encoding="utf-8") as f:
            return sum(json.loads(line)["cost_usd"] for line in f if line.strip())

    def record(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


_ledger = None


def set_ledger(ledger) -> None:
    """Use `ledger` (any object with spent() -> float and record(dict)) for every Claude call without its own log;
    None goes back to the file."""
    global _ledger
    _ledger = ledger


def ledger():
    return _ledger or FileLedger(Path(os.environ.get("LLM_USAGE_LOG") or USAGE_LOG))


def spent_usd(log: Path | None = None) -> float:
    return FileLedger(log).spent() if log else ledger().spent()


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES[model]
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


class Claude:
    _warned = False
    total_cost = 0.0  # USD spent by this process, all instances (per-turn cost = difference)

    def __init__(self, model: str = DEFAULT_MODEL, budget_usd: float | None = None, purpose: str = "",
                 log: Path | None = None):
        import anthropic  # imported here so the rest of core works without the SDK installed

        load_env()
        if model not in PRICES:
            raise ValueError(f"unknown model {model!r}; add its price to PRICES first")
        # An explicit log file wins; otherwise the shared ledger, looked up at call time (set_ledger may run later).
        self.model, self.purpose, self.log = model, purpose, log
        self._ledger = FileLedger(log) if log else None
        self.budget = float(budget_usd if budget_usd is not None else os.environ.get("LLM_BUDGET_USD", "5"))
        self.client = anthropic.Anthropic()

    def complete(self, prompt: str, system: str | None = None, max_tokens: int = 1024,
                 effort: str = "low") -> str:
        response = self.create([{"role": "user", "content": prompt}], system=system, max_tokens=max_tokens,
                               effort=effort)
        return "".join(b.text for b in response.content if b.type == "text").strip()

    def create(self, messages: list, system: str | None = None, max_tokens: int = 1024, effort: str = "low",
               tools: list | None = None):
        """One Messages API call with the budget check and usage logging. Returns the SDK response."""
        book = self._ledger or ledger()
        spent = book.spent()
        if spent >= self.budget:
            raise BudgetExceeded(f"LLM budget reached: ${spent:.4f} of ${self.budget:.2f} spent")
        if spent >= WARN_AT * self.budget and not Claude._warned:
            Claude._warned = True
            print(f"WARNING: Claude spend ${spent:.2f} is over {WARN_AT:.0%} of the ${self.budget:.2f} budget",
                  file=sys.stderr)
        kwargs = {"system": system} if system else {}
        if tools:
            kwargs["tools"] = tools
        if self.model == "claude-haiku-5-5":
            # Simple, short tasks: no thinking, lowest effort (Haiku 5.5 allows disabling at effort <= high).
            kwargs["thinking"] = {"type": "disabled"}
        start = time.perf_counter()
        response = self.client.messages.create(
            model=self.model, max_tokens=max_tokens, output_config={"effort": effort}, messages=messages, **kwargs)
        usage = response.usage
        cost = cost_usd(self.model, usage.input_tokens, usage.output_tokens)
        Claude.total_cost += cost
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": self.model, "purpose": self.purpose,
            "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
            "cost_usd": round(cost, 8),
            "seconds": round(time.perf_counter() - start, 2), "stop_reason": response.stop_reason,
        }
        book.record(record)
        if response.stop_reason == "refusal":
            raise RuntimeError(f"model refused ({getattr(response.stop_details, 'category', None)})")
        return response
