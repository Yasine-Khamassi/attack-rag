"""Claude wrappers without network calls: budget enforcement, usage logging, rerank parsing."""

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("anthropic")

from core.llm import BudgetExceeded, Claude, cost_usd, spent_usd  # noqa: E402
from core.rerank import ClaudeReranker  # noqa: E402


@pytest.fixture
def fake_claude(monkeypatch, tmp_path):
    """Claude whose client returns a canned reply; usage goes to a temp log."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    reply = {"text": "ok"}

    def create(**kwargs):
        reply["kwargs"] = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=reply["text"])], stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=100))

    def make(budget=1.0):
        c = Claude("claude-haiku-5-5", budget_usd=budget, purpose="test", log=tmp_path / "usage.jsonl")
        c.client = SimpleNamespace(messages=SimpleNamespace(create=create))
        return c

    return make, reply, tmp_path / "usage.jsonl"


def test_cost():
    assert cost_usd("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)


def test_calls_are_logged_and_budget_enforced(fake_claude):
    make, reply, log = fake_claude
    c = make(budget=0.00015)  # one call costs 0.0001 + 0.00005 = 0.00015
    assert c.complete("hi") == "ok"
    rec = json.loads(log.read_text().splitlines()[0])
    assert rec["input_tokens"] == 1000 and rec["cost_usd"] == pytest.approx(0.00015) and "key" not in json.dumps(rec)
    assert spent_usd(log) == pytest.approx(0.00015)
    with pytest.raises(BudgetExceeded):
        c.complete("again")
    assert len(log.read_text().splitlines()) == 1  # refused before calling


def test_haiku_request_shape(fake_claude):
    make, reply, _ = fake_claude
    make().complete("hi", system="sys")
    kw = reply["kwargs"]
    assert kw["model"] == "claude-haiku-5-5" and kw["thinking"] == {"type": "disabled"}
    assert kw["output_config"] == {"effort": "low"} and kw["system"] == "sys" and "temperature" not in kw


def test_unknown_model_rejected(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    with pytest.raises(ValueError):
        Claude("claude-unknown")


def test_listwise_rerank_parsing(fake_claude, monkeypatch):
    make, reply, _ = fake_claude
    r = ClaudeReranker.__new__(ClaudeReranker)
    r.name, r.max_chars, r.llm = "claude-haiku-5-5", 800, make()
    from core.cache import TTLCache
    r.cache = TTLCache()
    reply["text"] = "3, 1\n9, 3"  # 9 is out of range, the repeated 3 is ignored
    s = r.scores("q", ["a", "b", "c", "d"])
    assert s[2] > s[0] > s[1] > s[3]  # ranked first, ranked second, then incoming order for the rest


def test_warns_once_near_budget(fake_claude, capsys):
    make, _, _ = fake_claude
    Claude._warned = False
    c = make(budget=0.00018)  # one call = 0.00015 -> 83% of budget after the first call
    c.complete("one")
    c.complete("two")
    assert capsys.readouterr().err.count("WARNING: Claude spend") == 1
    Claude._warned = False


def test_gateway_mode_uses_the_deployment_token(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_GATEWAY", "vercel")
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.setenv("VERCEL_OIDC_TOKEN", "oidc-token")
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=1))

    c = Claude("claude-haiku-5-5", budget_usd=1.0, purpose="test", log=tmp_path / "usage.jsonl")
    assert str(c.client.base_url).rstrip("/") == "https://ai-gateway.vercel.sh"
    c.client = SimpleNamespace(with_options=lambda api_key: seen.update(api_key=api_key) or SimpleNamespace(
        messages=SimpleNamespace(create=create)))
    assert c.complete("hi") == "ok"
    assert seen["api_key"] == "oidc-token" and seen["model"] == "anthropic/claude-haiku-5.5"
    monkeypatch.delenv("VERCEL_OIDC_TOKEN")
    with pytest.raises(RuntimeError, match="AI Gateway credential"):
        c.complete("hi")
