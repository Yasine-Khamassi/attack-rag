"""Tools for the ATT&CK assistant. Example: the full page for an ID, with every relationship list, for questions
the top-6 sources only partly answer ("all software APT29 uses")."""

from __future__ import annotations

import re

from core.search import HybridSearcher
from core.tools import Tool, ToolRegistry

ID = re.compile(r"^(?:T\d{4}(?:\.\d{3})?|[GSMC]\d{4}|DET\d{4})$")


def attack_tools(searcher: HybridSearcher) -> ToolRegistry:
    def lookup(tenant_id: str, args: dict) -> str:
        if tenant_id != searcher.tenant_id:  # defence in depth: the searcher is bound to one tenant
            raise PermissionError("tenant mismatch")
        attack_id = args["attack_id"].strip().upper()
        if not ID.match(attack_id):
            return f"{attack_id!r} is not an ATT&CK ID (examples: T1059.001, G0016, S0002, M1038, DET0455)"
        doc_id = searcher.id_map.get(attack_id.lower(), attack_id)  # revoked IDs resolve to their replacement
        chunks = searcher.doc_chunks.get(doc_id)
        if not chunks:
            return f"No ATT&CK object with ID {attack_id}"
        header, _, _ = searcher.chunk_text[chunks[0]].partition("\n\n")
        bodies = [searcher.chunk_text[c].partition("\n\n")[2] for c in chunks]
        title, url = searcher.doc_info[doc_id]
        return f"{header}\nURL: {url}\n\n" + "\n".join(bodies)

    tool = Tool(
        "lookup_attack_object",
        "Full MITRE ATT&CK page for one ID: description and complete lists (software used, techniques used, "
        "mitigations with advice, detections). Use it when the question needs a complete list or an object the "
        "sources don't cover.",
        {"attack_id": {"type": "string", "description": "An ATT&CK ID such as T1059.001, G0016, S0002, M1038"}},
        lookup, required=("attack_id",))
    return ToolRegistry([tool], allowlist={searcher.tenant_id: {tool.name}})
