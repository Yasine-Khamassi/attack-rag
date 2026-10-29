"""
Step 1: Ingest MITRE ATT&CK (Enterprise) into clean JSONL documents.

Input : the official ATT&CK STIX 2.1 bundle (one big JSON file from MITRE's GitHub).
Output: data/processed/attack_docs.jsonl      -> one document per technique / group / mitigation / software
        data/processed/attack_relations.jsonl -> "who uses what" edges (kept for later: graph RAG stretch goal)

Run:  python -m src.ingest.attack
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from pathlib import Path

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


def build_doc(obj: dict) -> dict | None:
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

    text = "\n".join(parts).strip()
    return {
        "doc_id": ext_id,                    # stable key, used everywhere downstream
        "stix_id": obj["id"],
        "type": doc_type,
        "name": obj["name"],
        "is_subtechnique": obj.get("x_mitre_is_subtechnique", False),
        "tactics": tactics,
        "platforms": obj.get("x_mitre_platforms", []),
        "url": url,
        "source": "mitre-attack-enterprise",
        "version": obj.get("x_mitre_version"),
        "modified": obj.get("modified"),
        "content_hash": hashlib.sha256(text.encode()).hexdigest()[:16],  # detect changes on re-ingest
        "text": text,
    }


def build_detection_doc(obj: dict, by_stix: dict, techniques: list[str]) -> dict | None:
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
    text = "\n".join(parts).strip()
    return {
        "doc_id": ext_id,
        "stix_id": obj["id"],
        "type": "detection",
        "name": obj["name"],
        "is_subtechnique": False,
        "tactics": [],
        "platforms": sorted(platforms),
        "detects": techniques,
        "url": url,
        "source": "mitre-attack-enterprise",
        "version": obj.get("x_mitre_version"),
        "modified": obj.get("modified"),
        "content_hash": hashlib.sha256(text.encode()).hexdigest()[:16],
        "text": text,
    }


def main() -> None:
    bundle = download()
    objects = bundle["objects"]

    docs, stix_to_ext = [], {}
    for obj in objects:
        doc = build_doc(obj)
        if doc:
            docs.append(doc)
            stix_to_ext[doc["stix_id"]] = doc["doc_id"]

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
                stix_to_ext[doc["stix_id"]] = doc["doc_id"]

    # Relationships: "G0016 uses T1059.001", "M1042 mitigates T1059", "DET0103 detects T1070.005" ...
    rels = []
    for obj in objects:
        if obj["type"] != "relationship" or not is_active(obj):
            continue
        src, tgt = stix_to_ext.get(obj["source_ref"]), stix_to_ext.get(obj["target_ref"])
        if src and tgt:
            rels.append({"source": src, "relation": obj["relationship_type"], "target": tgt})

    DOCS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DOCS_PATH.open("w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    with REL_PATH.open("w", encoding="utf-8") as f:
        for r in rels:
            f.write(json.dumps(r) + "\n")

    # Print a summary: these numbers go in docs/LOG.md
    counts: dict[str, int] = {}
    for d in docs:
        counts[d["type"]] = counts.get(d["type"], 0) + 1
    lengths = sorted(len(d["text"]) for d in docs)
    print(f"Documents: {len(docs)}  {counts}")
    print(f"Relationships: {len(rels)}")
    print(f"Text length chars: min={lengths[0]} median={lengths[len(lengths)//2]} max={lengths[-1]}")
    print(f"Wrote {DOCS_PATH.relative_to(ROOT)} and {REL_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
