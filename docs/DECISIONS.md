# Design decisions

One entry per decision. Format: what we chose, what we rejected, why, what it costs us.
Never edit an old entry; add a new one that supersedes it.

---

## D1: Corpus is MITRE ATT&CK Enterprise (STIX bundle)
- **Chose:** the official STIX 2.1 JSON from mitre-attack/attack-stix-data on GitHub.
- **Rejected:** scraping attack.mitre.org HTML; random security blog posts.
- **Why:** structured, public, versioned, has real IDs (T1059.001) and real relationships. Scraping HTML is fragile; blogs have no ground truth to evaluate against.
- **Cost:** English only, and descriptions are short. Will add CISA advisories later for longer prose.

## D2: Skip revoked and deprecated objects
- **Why:** they describe old versions of techniques. Indexing them puts outdated answers into retrieval results.
- **Cost:** questions about old technique IDs won't resolve. Could add a redirect table from `revoked-by` relationships later.

## D3: Detection strategies are their own documents
- **Finding:** current ATT&CK removed the free-text `x_mitre_detection` field from techniques. Detection now lives in `x-mitre-detection-strategy` objects (DETxxxx) that contain analytics (ANxxxx), linked to techniques by `detects` relationships.
- **Chose:** one document per detection strategy containing its analytics, platforms, log sources, and the technique IDs it detects.
- **Why:** detection is the most valuable content for a SOC user. Putting technique IDs in the header lets keyword search connect "detect T1059.001" to the right detection doc.
- **Cost:** the answer to "how do I detect X" now needs retrieval to find two documents (technique + detection) instead of one. Good test case for hybrid search.

## D4: Stable `doc_id` = ATT&CK external ID
- **Why:** every later stage (chunks, eval golden set, citations) references documents by this ID. If it changed on re-ingest, the eval set would break.

## D5: Public corpus uses one tenant, `tenant_id = "attack"`
- **Chose:** every ATT&CK document gets the same `tenant_id`.
- **Rejected:** a nullable `tenant_id` meaning "public"; per-document tenants.
- **Why:** a required, never-empty `tenant_id` means retrieval can always filter by it with no special case for null. Public data is just one more tenant.
- **Cost:** sharing public docs across tenants later needs an explicit "also search tenant X" rule rather than a null match.

## D6: Schema is a pydantic model that rejects unknown fields
- **Chose:** `core.schema.Document` (pydantic v2, `extra="forbid"`, frozen, hash checked against text). Corpus-specific fields go in `metadata`.
- **Rejected:** plain dicts / TypedDict (no runtime check); a dataclass (would need hand-written validation); one column per corpus field.
- **Why:** bad documents fail at ingest time with a clear error instead of producing odd retrieval later. Pydantic is also what FastAPI uses in phase 3.
- **Cost:** a runtime dependency in the core; `metadata` contents are not type-checked, so filters on metadata need their own tests.

## D7: Real-data tests skip when the data isn't built
- **Chose:** tests that read `data/processed/` call `pytest.skip` if the file is missing; a small hand-made bundle covers the logic always.
- **Why:** CI stays fast and doesn't download 50 MB per run, while local runs after `make ingest` check all 2,530 real docs.
- **Cost:** CI does not check the real bundle. Revisit in phase 2 when the eval CI job needs the data anyway.
