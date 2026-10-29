import io
import json

from core import query
from core.query import OllamaTranslator, arabic_share


def test_arabic_share():
    assert arabic_share("How do I detect T1055?") == 0
    assert arabic_share("كيف أكتشف T1055؟") > 0.7
    assert arabic_share("1234 ...") == 0


def test_translates_only_arabic_and_caches(monkeypatch):
    sent = []

    def fake_urlopen(req, timeout):
        sent.append(json.loads(req.data))
        return io.BytesIO(json.dumps({"response": ' "How do I detect T1055?" \n'}).encode())

    monkeypatch.setattr(query.urllib.request, "urlopen", fake_urlopen)
    t = OllamaTranslator("m", target="English", domain="cybersecurity", url="http://x")
    assert t.translate("How do I detect T1055?") is None and not sent
    assert t.translate("كيف أكتشف T1055؟") == "How do I detect T1055?"
    assert t.translate("كيف أكتشف T1055؟") == "How do I detect T1055?"
    assert len(sent) == 1  # second call came from the cache
    assert "cybersecurity question into English" in sent[0]["prompt"] and sent[0]["options"]["temperature"] == 0
