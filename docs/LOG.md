# Project log

Append-only. One entry per work session: what was done, numbers, problems, next step.

---

## 2026-10-29 · Step 1: Ingestion

**Done**
- Repo structure, `src/ingest/attack.py`.
- Downloaded ATT&CK Enterprise STIX bundle, extracted active objects into `data/processed/attack_docs.jsonl`.

**Numbers**
- 2,530 documents: 706 techniques, 703 detection strategies, 842 software, 178 groups, 57 campaigns, 44 mitigations.
- 21,660 relationships (uses / mitigates / detects / subtechnique-of / attributed-to).
- Document length: min 83, median 671, max 4,906 characters.

**Problems found**
- `x_mitre_detection` field is gone in current ATT&CK. Detection moved to detection-strategy + analytic objects. Fixed by building separate detection documents (see D3).

**Next**
- Step 2: chunking + embeddings + pgvector. Decide embedding model (must be multilingual: analysts may ask in Arabic as well as English).
