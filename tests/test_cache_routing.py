import time

import pytest

from core.cache import TTLCache, fingerprint, key, normalize_question
from core.routing import route


@pytest.mark.parametrize("question,pinned,expected", [
    ("T1047", ["T1047"], "id_lookup"),
    ("ما هي T1486؟", ["T1486"], "id_lookup"),
    ("ما هي التقنية T1003.001؟", ["T1003.001"], "id_lookup"),
    ("ما معنى T1070.001؟", ["T1685.005"], "id_lookup"),          # revoked ID pins its replacement
    ("How do I detect T1055 on Linux servers?", ["T1055"], "full"),
    ("Compare T1055 and T1059", ["T1055", "T1059"], "full"),
    ("How do I detect Kerberoasting?", [], "full"),
])
def test_routing(question, pinned, expected):
    assert route(question, pinned).reason == expected


def test_ttl_cache_lru_and_expiry():
    c = TTLCache(max_items=2, ttl_seconds=0.2)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1           # a is now most recent
    c.put("c", 3)                    # evicts b
    assert c.get("b") is None and c.get("c") == 3
    time.sleep(0.25)
    assert c.get("a") is None and c.hits == 2 and c.misses == 2


def test_keys_and_normalisation():
    assert key("t", "q", ["a", "b"]) == key("t", "q", ("a", "b")) != key("t", "q", ["ab"])
    assert normalize_question("  How do I detect  Kerberoasting? ") == normalize_question("how do i detect kerberoasting")
    assert fingerprint(["a", "b"]) != fingerprint(["a", "c"])
