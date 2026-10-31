"""Tool framework: allowlist, strict argument validation, tenant injection, tool results as citable sources."""

from types import SimpleNamespace

from core.answer import Answerer
from core.tools import Tool, ToolRegistry

seen = []


def lookup(tenant_id, args):
    seen.append((tenant_id, args))
    if args["item_id"] == "boom":
        raise ValueError("bad id")
    return f"{args['item_id']} belongs to {tenant_id}: details here"


LOOKUP = Tool("lookup_item", "Look up an item by ID.", {"item_id": {"type": "string", "description": "the ID"}},
              lookup, required=("item_id",))
REG = ToolRegistry([LOOKUP], allowlist={"tenant-a": {"lookup_item"}, "tenant-b": set()})


def test_allowlist_per_tenant():
    assert [s["name"] for s in REG.specs("tenant-a")] == ["lookup_item"]
    assert REG.specs("tenant-b") == [] and REG.specs("unknown") == []
    assert REG.call("tenant-b", "lookup_item", {"item_id": "x"}) == ("tool 'lookup_item' is not available", True)


def test_spec_is_strict():
    spec = LOOKUP.spec()
    assert spec["strict"] is True and spec["input_schema"]["additionalProperties"] is False


def test_validation_and_tenant_injection():
    seen.clear()
    assert REG.call("tenant-a", "lookup_item", {"item_id": "x", "tenant_id": "tenant-b"})[1] is True  # extra arg
    assert REG.call("tenant-a", "lookup_item", {})[1] is True                                         # missing
    assert REG.call("tenant-a", "lookup_item", {"item_id": 5})[1] is True                             # wrong type
    assert seen == []                                                                                 # never ran
    text, err = REG.call("tenant-a", "lookup_item", {"item_id": "x"})
    assert not err and text == "x belongs to tenant-a: details here" and seen == [("tenant-a", {"item_id": "x"})]
    assert REG.call("tenant-a", "lookup_item", {"item_id": "boom"}) == ("tool failed: ValueError", True)  # detail -> log (D44)


def response(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


class ToolLLM:
    """First call: asks for the tool. Second call: answers citing the tool result as source [2]."""

    def __init__(self):
        self.calls = []

    def create(self, messages, system=None, max_tokens=1024, effort="low", tools=None):
        self.calls.append({"messages": messages, "tools": tools})
        if len(self.calls) == 1:
            return response(SimpleNamespace(type="tool_use", id="tu1", name="lookup_item", input={"item_id": "X9"}),
                            stop="tool_use")
        return response(SimpleNamespace(type="text", text="Item X9 has these details according to the lookup [2]."))

    def complete(self, prompt, system=None, max_tokens=1024, effort="low"):
        raise AssertionError("tools path must use create()")


def test_answerer_uses_tool_results_as_sources():
    hits = [SimpleNamespace(doc_id="D1", chunk_id="D1#0", title="Doc 1", url=None)]
    llm = ToolLLM()
    a = Answerer(llm, persona="p").answer("What is X9?", hits, {"D1#0": "unrelated text"},
                                         tools=REG, tenant_id="tenant-a")
    assert not a.refused and a.cited == [2] and a.sources[1].doc_id == "tool:lookup_item"
    assert "X9 belongs to tenant-a" in a.sources[1].text
    tool_result = llm.calls[1]["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "tu1" and tool_result["content"].startswith("Source [2]:")


def test_no_tools_for_tenant_means_plain_answer():
    class Plain:
        def complete(self, prompt, system=None, max_tokens=1024, effort="low"):
            assert "Tools:" not in system
            return "Doc says this [1]."

    hits = [SimpleNamespace(doc_id="D1", chunk_id="D1#0", title="Doc 1", url=None)]
    a = Answerer(Plain(), persona="p").answer("q?", hits, {"D1#0": "t"}, tools=REG, tenant_id="tenant-b")
    assert a.cited == [1]


def test_number_arguments_accept_ints_and_floats_not_bools():
    """D45: "number" was missing from the type map, so it fell back to string and refused every real number."""
    price = Tool("price", "Price of an amount.", {"amount": {"type": "number", "description": "amount"}},
                 lambda tenant_id, a: f"{a['amount']}", required=("amount",))
    assert price.validate({"amount": 249}) is None and price.validate({"amount": 62.25}) is None
    assert price.validate({"amount": "249"}) and price.validate({"amount": True})
    assert LOOKUP.validate({"item_id": True})  # a bool is not a string either
