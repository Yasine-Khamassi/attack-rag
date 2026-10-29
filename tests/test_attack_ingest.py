"""ATT&CK ingestion produces valid core Documents (Phase 0 gate: data loads into the shared schema)."""

import json
from collections import Counter

import pytest

from core.schema import Document
from products.attack.ingest import DOCS_PATH, REL_PATH, TENANT_ID, build_all


def ref(ext_id, path):
    return [{"source_name": "mitre-attack", "external_id": ext_id, "url": f"https://attack.mitre.org/{path}"}]


# A tiny hand-made bundle covering each case the real one has.
BUNDLE = [
    {"type": "attack-pattern", "id": "attack-pattern--1", "name": "PowerShell",
     "external_references": ref("T1059.001", "techniques/T1059/001"),
     "kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": "execution"}],
     "x_mitre_platforms": ["Windows"], "x_mitre_is_subtechnique": True,
     "description": "Adversaries may abuse [PowerShell](https://attack.mitre.org/techniques/T1059/001) "
                    "commands.(Citation: Some Report)"},
    {"type": "attack-pattern", "id": "attack-pattern--old", "name": "Old technique", "revoked": True,
     "external_references": ref("T9999", "techniques/T9999")},
    {"type": "intrusion-set", "id": "intrusion-set--1", "name": "APT29", "aliases": ["APT29", "Cozy Bear"],
     "external_references": ref("G0016", "groups/G0016"), "description": "A threat group."},
    {"type": "malware", "id": "malware--noid", "name": "No external ID", "external_references": []},
    {"type": "x-mitre-analytic", "id": "x-mitre-analytic--1", "x_mitre_platforms": ["Windows"],
     "external_references": ref("AN0001", "detectionstrategies/DET0001"),
     "description": "Watch for encoded commands.",
     "x_mitre_log_source_references": [{"name": "WinEventLog:PowerShell", "channel": "EventCode=4104"}]},
    {"type": "x-mitre-detection-strategy", "id": "x-mitre-detection-strategy--1", "name": "Detect PowerShell",
     "external_references": ref("DET0001", "detectionstrategies/DET0001"),
     "x_mitre_analytic_refs": ["x-mitre-analytic--1"]},
    {"type": "relationship", "id": "relationship--1", "relationship_type": "uses",
     "source_ref": "intrusion-set--1", "target_ref": "attack-pattern--1"},
    {"type": "relationship", "id": "relationship--2", "relationship_type": "detects",
     "source_ref": "x-mitre-detection-strategy--1", "target_ref": "attack-pattern--1"},
    {"type": "relationship", "id": "relationship--3", "relationship_type": "uses",
     "source_ref": "intrusion-set--1", "target_ref": "attack-pattern--old"},
]


@pytest.fixture(scope="module")
def built():
    docs, rels = build_all(BUNDLE)
    return {d.doc_id: d for d in docs}, rels


def test_keeps_active_objects_with_ids(built):
    docs, _ = built
    assert set(docs) == {"T1059.001", "G0016", "DET0001"}  # revoked and ID-less objects dropped


def test_technique_document(built):
    doc = built[0]["T1059.001"]
    assert doc.tenant_id == TENANT_ID and doc.type == "technique" and doc.title == "PowerShell"
    assert doc.text.startswith("T1059.001: PowerShell\nType: technique")
    assert "(Citation" not in doc.text and "](https://" not in doc.text
    assert doc.metadata["tactics"] == ["execution"] and doc.metadata["is_subtechnique"] is True


def test_detection_document_joins_analytics(built):
    doc = built[0]["DET0001"]
    assert doc.type == "detection" and doc.metadata["detects"] == ["T1059.001"]
    assert "Detects techniques: T1059.001" in doc.text and "EventCode=4104" in doc.text


def test_relationships_skip_dropped_objects(built):
    assert built[1] == [
        {"source": "G0016", "relation": "uses", "target": "T1059.001"},
        {"source": "DET0001", "relation": "detects", "target": "T1059.001"},
    ]


# --- Full output. Needs `make ingest` first; skipped otherwise so CI without the 50 MB download still runs.

@pytest.fixture(scope="module")
def real_docs():
    if not DOCS_PATH.exists():
        pytest.skip(f"{DOCS_PATH.name} not built; run `make ingest`")
    with DOCS_PATH.open(encoding="utf-8") as f:
        return [Document.model_validate_json(line) for line in f]


def test_every_real_document_validates(real_docs):
    assert len(real_docs) > 2000
    assert {d.tenant_id for d in real_docs} == {TENANT_ID}


def test_real_doc_ids_are_unique(real_docs):
    dupes = [k for k, n in Counter(d.doc_id for d in real_docs).items() if n > 1]
    assert not dupes


def test_real_relationships_point_at_documents(real_docs):
    ids = {d.doc_id for d in real_docs}
    with REL_PATH.open(encoding="utf-8") as f:
        rels = [json.loads(line) for line in f]
    assert rels and all(r["source"] in ids and r["target"] in ids for r in rels)
