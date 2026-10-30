"""Security events as JSON lines in an Elastic Common Schema (ECS)-style shape, ready for a SIEM
(Elastic, Splunk, Sentinel: one JSON object per line, tail the file or ship it with a log forwarder).

Example event:
{"@timestamp": "2026-10-30T03:00:00Z", "event": {"kind": "alert", "category": ["intrusion_detection"],
 "type": ["denied"], "action": "source_quarantined", "module": "rag-guardrails"},
 "rule": {"name": "INJECTED: ..."}, "tenant": {"id": "attack"}, "rag": {"conversation_id": "...",
 "where": "source", "ref": "T9001#0", "excerpt": "..."}}
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = Path(os.environ.get("SECURITY_LOG", ROOT / "data" / "security_events.jsonl"))
_lock = threading.Lock()

ACTIONS = {"input": "request_refused", "source": "source_quarantined", "output": "output_sanitized"}


def event(finding, tenant_id: str, conversation_id: str | None = None, path: Path | None = None) -> dict:
    record = {
        "@timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": {"kind": "alert", "category": ["intrusion_detection"],
                  "type": ["denied"] if finding.where != "output" else ["change"],
                  "action": ACTIONS.get(finding.where, finding.where), "module": "rag-guardrails"},
        "rule": {"name": finding.rule},
        "tenant": {"id": tenant_id},
        "rag": {"conversation_id": conversation_id, "where": finding.where, "ref": finding.ref,
                "excerpt": finding.excerpt},
    }
    target = path or LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with _lock, target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record
