"""Web API with a fake assistant (no database, model server or API calls)."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # needed by fastapi.testclient
pytest.importorskip("anthropic")

from fastapi.testclient import TestClient  # noqa: E402

from core.answer import Source, check  # noqa: E402
from core.llm import BudgetExceeded  # noqa: E402
from products.attack import api  # noqa: E402


class FakeAssistant:
    def __init__(self, reply="Attackers request service tickets to crack offline [1].", fail=False):
        self.reply, self.fail = reply, fail

    def ask(self, question, conversation_id=None):
        self.conversation_id = conversation_id
        if self.fail:
            raise BudgetExceeded("LLM budget reached")
        src = [Source(1, "T1558.003", "T1558.003#0", "Kerberoasting", "https://attack.mitre.org/techniques/T1558/003", "x")]
        return check(question, self.reply, src), {"retrieval_s": 0.1, "answer_s": 0.1}


@pytest.fixture
def client(monkeypatch):
    def use(fake):
        monkeypatch.setattr(api, "assistant", lambda: fake)
        return TestClient(api.app)
    return use


def test_page_and_health(client):
    c = client(FakeAssistant())
    assert c.get("/api/health").json() == {"ok": True}
    page = c.get("/")
    assert page.status_code == 200 and "ATT&amp;CK Analyst Assistant" in page.text


def test_ask_returns_cited_answer(client):
    d = client(FakeAssistant()).post("/api/ask", json={"question": "How does Kerberoasting work?"}).json()
    assert d["refused"] is False and d["checks"]["citation_coverage"] == 1.0
    assert d["sources"][0] == {"n": 1, "doc_id": "T1558.003", "title": "Kerberoasting",
                               "url": "https://attack.mitre.org/techniques/T1558/003", "cited": True}


def test_validation_and_budget(client):
    assert client(FakeAssistant()).post("/api/ask", json={"question": ""}).status_code == 422
    r = client(FakeAssistant(fail=True)).post("/api/ask", json={"question": "anything"})
    assert r.status_code == 503 and "budget" in r.json()["detail"]


def test_conversation_id_is_passed_and_validated(client):
    fake = FakeAssistant()
    c = client(fake)
    assert c.post("/api/ask", json={"question": "q?", "conversation_id": "abc-123"}).status_code == 200
    assert fake.conversation_id == "abc-123"
    assert c.post("/api/ask", json={"question": "q?", "conversation_id": "bad id; drop"}).status_code == 422
