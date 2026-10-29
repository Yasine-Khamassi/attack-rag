"""
Ingest MITRE ATT&CK (Enterprise) into core `Document` records (JSONL).

Input : the official ATT&CK STIX 2.1 bundle (one big JSON file from MITRE's GitHub).
Output: data/processed/attack_docs.jsonl      -> one core.Document per technique / group / mitigation /
                                                  software / campaign / detection strategy
        data/processed/attack_relations.jsonl -> "who uses what" edges (kept for later: graph RAG stretch goal)

Run:  make ingest   (or: python -m products.attack.ingest)
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

from core.schema import Document

# ATT&CK is public data, so all of it belongs to one tenant (decision D5).
TENANT_ID = "attack"
SOURCE = "mitre-attack-enterprise"

ATTACK_URL = (
    "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/"
    "master/enterprise-attack/enterprise-attack.json"
)
ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = ROOT / "data" / "raw" / "enterprise-attack.json"
DOCS_PATH = ROOT / "data" / "processed" / "attack_docs.jsonl"
REL_PATH = ROOT / "data" / "processed" / "attack_relations.jsonl"

# STIX object type -> our document type
TYPE_MAP = {
    "attack-pattern": "technique",
    "intrusion-set": "group",
    "course-of-action": "mitigation",
    "malware": "software",
    "tool": "software",
    "campaign": "campaign",
}


def download(force: bool = False) -> dict:
    """Download the bundle once and cache it. Re-runs read the local copy."""
    if force or not RAW_PATH.exists():
        RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading ATT&CK bundle -> {RAW_PATH}")
        urllib.request.urlretrieve(ATTACK_URL, RAW_PATH)
    return json.loads(RAW_PATH.read_text(encoding="utf-8"))


def attack_id_and_url(obj: dict) -> tuple[str | None, str | None]:
    """ATT&CK IDs (T1059, G0016, M1036, S0154) live in external_references."""
    for ref in obj.get("external_references", []):
        if ref.get("source_name") == "mitre-attack":
            return ref.get("external_id"), ref.get("url")
    return None, None


CITATION_RE = re.compile(r"\(Citation:[^)]*\)")
LINK_RE = re.compile(r"\[([^\]]+)\]\(https?://attack\.mitre\.org[^)]*\)")


def clean(text: str | None) -> str:
    """Strip ATT&CK citation markers and internal markdown links; keep the readable words."""
    if not text:
        return ""
    text = CITATION_RE.sub("", text)
    text = LINK_RE.sub(r"\1", text)  # "[PowerShell](https://attack...)" -> "PowerShell"
    text = re.sub(r"<code>(.*?)</code>", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def is_active(obj: dict) -> bool:
    """Revoked/deprecated objects are history, not knowledge. Indexing them pollutes retrieval."""
    return not obj.get("revoked", False) and not obj.get("x_mitre_deprecated", False)


def previous_ids(objects: list[dict]) -> dict[str, list[str]]:
    """Active STIX id -> old ATT&CK IDs that were revoked in its favour (e.g. T1685.005 <- T1070.001).
    Analysts and old reports keep using revoked IDs for years, so the replacement must still answer to them."""
    by_stix = {o["id"]: o for o in objects}
    revoked_by = {o["source_ref"]: o["target_ref"] for o in objects
                  if o["type"] == "relationship" and o["relationship_type"] == "revoked-by"}
    result: dict[str, list[str]] = {}
    for old, new in revoked_by.items():
        while new in revoked_by:  # follow chains: A revoked by B, B revoked by C -> C
            new = revoked_by[new]
        old_id, _ = attack_id_and_url(by_stix.get(old, {}))
        if old_id:
            result.setdefault(new, []).append(old_id)
    return {k: sorted(v) for k, v in result.items()}


def build_doc(obj: dict, old_ids: list[str] | None = None) -> Document | None:
    doc_type = TYPE_MAP.get(obj["type"])
    if doc_type is None or not is_active(obj):
        return None
    ext_id, url = attack_id_and_url(obj)
    if not ext_id:
        return None

    tactics = [p["phase_name"] for p in obj.get("kill_chain_phases", [])
               if p.get("kill_chain_name") == "mitre-attack"]

    # The text we will chunk + embed. Header lines make IDs and names searchable by keyword.
    parts = [f"{ext_id}: {obj['name']}", f"Type: {doc_type}"]
    if old_ids:
        parts.append("Previously: " + ", ".join(old_ids))
    if obj.get("aliases") or obj.get("x_mitre_aliases"):
        parts.append("Aliases: " + ", ".join(obj.get("aliases") or obj.get("x_mitre_aliases")))
    if tactics:
        parts.append("Tactics: " + ", ".join(tactics))
    if obj.get("x_mitre_platforms"):
        parts.append("Platforms: " + ", ".join(obj["x_mitre_platforms"]))
    parts.append("")
    parts.append(clean(obj.get("description")))
    # Older ATT&CK versions carry a free-text detection field; newer ones moved detection elsewhere.
    if obj.get("x_mitre_detection"):
        parts.append("\nDetection:\n" + clean(obj["x_mitre_detection"]))

    return Document.build(
        doc_id=ext_id,                       # stable key, used everywhere downstream
        tenant_id=TENANT_ID,
        source=SOURCE,
        type=doc_type,
        title=obj["name"],
        text="\n".join(parts).strip(),
        url=url,
        metadata={
            "stix_id": obj["id"],
            "previous_ids": old_ids or [],
            "is_subtechnique": obj.get("x_mitre_is_subtechnique", False),
            "tactics": tactics,
            "platforms": obj.get("x_mitre_platforms", []),
            "version": obj.get("x_mitre_version"),
            "modified": obj.get("modified"),
        },
    )


def build_detection_doc(obj: dict, by_stix: dict, techniques: list[str]) -> Document | None:
    """One document per detection strategy: name, techniques it covers, and every analytic's
    platform, log sources and logic. This is the SOC-facing content."""
    if not is_active(obj):
        return None
    ext_id, url = attack_id_and_url(obj)
    if not ext_id:
        return None
    parts = [f"{ext_id}: {obj['name']}", "Type: detection"]
    if techniques:
        parts.append("Detects techniques: " + ", ".join(techniques))
    platforms: set[str] = set()
    for ref in obj.get("x_mitre_analytic_refs", []):
        an = by_stix.get(ref)
        if not an or not is_active(an):
            continue
        an_id, _ = attack_id_and_url(an)
        plats = an.get("x_mitre_platforms", [])
        platforms.update(plats)
        logs = [f"{l.get('name')} ({l.get('channel')})" for l in an.get("x_mitre_log_source_references", [])]
        parts.append(f"\nAnalytic {an_id} [{', '.join(plats)}]: {clean(an.get('description'))}")
        if logs:
            parts.append("Log sources: " + "; ".join(logs))
    return Document.build(
        doc_id=ext_id,
        tenant_id=TENANT_ID,
        source=SOURCE,
        type="detection",
        title=obj["name"],
        text="\n".join(parts).strip(),
        url=url,
        metadata={
            "stix_id": obj["id"],
            "is_subtechnique": False,
            "tactics": [],
            "platforms": sorted(platforms),
            "detects": techniques,
            "version": obj.get("x_mitre_version"),
            "modified": obj.get("modified"),
        },
    )


def build_all(objects: list[dict]) -> tuple[list[Document], list[dict]]:
    """STIX objects -> (documents, relationships). Pure function, so tests can run it on a tiny bundle."""
    docs: list[Document] = []
    stix_to_ext: dict[str, str] = {}
    old_ids = previous_ids(objects)
    for obj in objects:
        doc = build_doc(obj, old_ids.get(obj["id"]))
        if doc:
            docs.append(doc)
            stix_to_ext[doc.metadata["stix_id"]] = doc.doc_id

    # Detection strategies (DETxxxx) are separate objects in current ATT&CK.
    # Each one points to analytics (ANxxxx) that hold the actual "what to look for" text.
    by_stix = {o["id"]: o for o in objects}
    detects: dict[str, list[str]] = {}
    for obj in objects:
        if obj["type"] == "relationship" and obj["relationship_type"] == "detects" and is_active(obj):
            tech = stix_to_ext.get(obj["target_ref"])
            if tech:
                detects.setdefault(obj["source_ref"], []).append(tech)
    for obj in objects:
        if obj["type"] == "x-mitre-detection-strategy":
            doc = build_detection_doc(obj, by_stix, sorted(detects.get(obj["id"], [])))
            if doc:
                docs.append(doc)
                stix_to_ext[doc.metadata["stix_id"]] = doc.doc_id

    # Relationships: "G0016 uses T1059.001", "M1042 mitigates T1059", "DET0103 detects T1070.005" ...
    rels = []
    for obj in objects:
        if obj["type"] != "relationship" or not is_active(obj):
            continue
        src, tgt = stix_to_ext.get(obj["source_ref"]), stix_to_ext.get(obj["target_ref"])
        if src and tgt:
            rel = {"source": src, "relation": obj["relationship_type"], "target": tgt}
            if obj["relationship_type"] == "mitigates" and obj.get("description"):
                # The technique-specific advice ("block execution of ... in Office macros"): on attack.mitre.org
                # it is the text next to each mitigation on a technique page.
                rel["description"] = clean(obj["description"])
            rels.append(rel)
    return add_relationship_sections(docs, rels), rels


# Which relationships to write into which document, as (relation, direction, other doc type, label).
# "out" = this doc is the source ("G0016 uses S0002"); "in" = this doc is the target ("S0002 used by G0016").
# Left out on purpose: techniques' "used by" lists (hundreds of groups and software per technique).
SECTIONS = {
    "group": [("uses", "out", "software", "Software used"), ("uses", "out", "technique", "Techniques used"),
              ("attributed-to", "in", "campaign", "Campaigns")],
    "campaign": [("attributed-to", "out", "group", "Attributed to"), ("uses", "out", "software", "Software used"),
                 ("uses", "out", "technique", "Techniques used")],
    "software": [("uses", "in", "group", "Used by groups"), ("uses", "in", "campaign", "Used in campaigns"),
                 ("uses", "out", "technique", "Techniques used")],
    "mitigation": [("mitigates", "out", "technique", "Mitigates")],
    "technique": [("subtechnique-of", "out", "technique", "Sub-technique of"),
                  ("mitigates", "in", "mitigation", "Mitigations"), ("detects", "in", "detection", "Detected by")],
}
ITEMS_PER_LINE = 10  # short lines, so the chunker can split long lists cleanly


def add_relationship_sections(docs: list[Document], rels: list[dict]) -> list[Document]:
    """Append 'Software used: Mimikatz (S0002); ...' style sections, like the lists on attack.mitre.org pages.
    Without them a question like 'which software does APT29 use?' has no document that contains the answer:
    the relationships only exist in attack_relations.jsonl, which search never sees."""
    by_id = {d.doc_id: d for d in docs}
    out: dict[tuple[str, str], list[str]] = {}
    for r in rels:
        out.setdefault((r["source"], r["relation"]), []).append(r["target"])
    into: dict[tuple[str, str], list[str]] = {}
    for r in rels:
        into.setdefault((r["target"], r["relation"]), []).append(r["source"])
    advice = {(r["source"], r["target"]): r["description"] for r in rels if r.get("description")}

    result = []
    for d in docs:
        lines = []
        for relation, direction, other_type, label in SECTIONS.get(d.type, []):
            linked = (out if direction == "out" else into).get((d.doc_id, relation), [])
            if d.type == "technique" and relation == "mitigates":
                # One line per mitigation with its technique-specific guidance, not just the name.
                for m in sorted(linked):
                    guidance = advice.get((m, d.doc_id))
                    lines.append(f"Mitigation {by_id[m].title} ({m})" + (f": {guidance}" if guidance else ""))
                continue
            items = sorted({f"{by_id[o].title} ({o})" for o in linked if by_id[o].type == other_type})
            for i in range(0, len(items), ITEMS_PER_LINE):
                lines.append(f"{label}: " + "; ".join(items[i:i + ITEMS_PER_LINE]))
        if lines:
            d = Document.build(**{**d.model_dump(exclude={"content_hash"}), "text": d.text + "\n\n" + "\n".join(lines)})
        result.append(d)
    return result


def main() -> None:
    docs, rels = build_all(download()["objects"])

    DOCS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DOCS_PATH.open("w", encoding="utf-8") as f:
        for d in docs:
            f.write(d.model_dump_json() + "\n")
    with REL_PATH.open("w", encoding="utf-8") as f:
        for r in rels:
            f.write(json.dumps(r) + "\n")

    # Print a summary: these numbers go in docs/LOG.md
    counts: dict[str, int] = {}
    for d in docs:
        counts[d.type] = counts.get(d.type, 0) + 1
    lengths = sorted(len(d.text) for d in docs)
    print(f"Documents: {len(docs)}  {counts}")
    print(f"Relationships: {len(rels)}")
    print(f"Revoked IDs redirected: {sum(len(d.metadata.get('previous_ids', [])) for d in docs)}")
    print(f"Text length chars: min={lengths[0]} median={lengths[len(lengths)//2]} max={lengths[-1]}")
    print(f"Wrote {DOCS_PATH.relative_to(ROOT)} and {REL_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
