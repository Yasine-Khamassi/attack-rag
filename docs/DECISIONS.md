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

## D8: Revoked IDs redirect to their replacement (supersedes the cost note in D2)
- **Chose:** keep skipping revoked objects (D2), but follow `revoked-by` relationships (including chains) and put the old IDs on the replacement: a `Previously: T1070.001` header line and `metadata.previous_ids`.
- **Rejected:** indexing revoked objects (outdated text in results); a separate lookup table only (dense and keyword search would still miss the old ID).
- **Why:** ATT&CK v20 revoked IDs analysts still use daily (T1070.001 -> T1685.005). Old reports, SIEM rules and people keep the old IDs for years.
- **Cost:** a few extra header tokens per affected doc (157 IDs). Must re-check after each ATT&CK release.

## D9: Postgres + pgvector via Docker Compose, with a no-Docker local fallback
- **Chose:** `docker-compose.yml` (pgvector/pgvector:0.8.7-pg16) as the standard setup and in CI; `core/db.py` falls back to `pgserver` (pip-installable Postgres 16 + pgvector) when `DATABASE_URL` is unset.
- **Rejected:** a native Windows Postgres install (pgvector must be compiled with MSVC); SQLite + sqlite-vec (different SQL, would not match production); a hosted database (cost, data leaves the machine).
- **Why:** the dev machine has no Docker/WSL yet; work should not wait on that. Same SQL and same extension either way.
- **Cost:** pgserver ships pgvector 0.6.2, CI/Docker run 0.8.7. Stick to features both have (HNSW, cosine ops); iterative index scans (0.8+) only after local moves to Docker.

## D10: Dense retrieval uses bge-m3 (local, via Ollama)
- **Chose:** bge-m3 (568M params, 1024 dims, 8k context), run locally through Ollama.
- **Rejected:** snowflake-arctic-embed2 (best English, MRR 0.819, but weakest Arabic, 0.465); embeddinggemma (best overall MRR 0.690 vs 0.684, smaller and 768 dims, but Arabic MRR 0.604 vs 0.688); hosted embedding APIs (cost per re-index, data leaves the machine).
- **Why:** Arabic is the hardest case and bge-m3 leads there, with the closest English/Arabic agreement (0.826). Overall scores are within noise (12 questions per language), so the tiebreak is the weakest language. 8k context also leaves room for larger chunks.
- **Cost:** 1024 dims = 33% more vector storage than embeddinggemma; ~6 docs/s on this CPU. Re-test against embeddinggemma on the phase 2 golden set; switch if it wins there.

## D11: Hybrid search is required, not optional
- **Chose:** keep the planned dense + keyword (BM25) + RRF design.
- **Why (measured):** all three embedding models missed bare-ID questions (T1003.001, T1070.001) entirely in the step 3b probe. IDs are the most common thing analysts type; keyword search finds them exactly.
- **Cost:** two indexes to build and keep in sync; fusion adds a step to tune.

## D12: Keyword search uses an in-process BM25 over chunks loaded per tenant
- **Chose:** `core/bm25.py` built from the tenant's rows in `chunks` when a searcher is created.
- **Rejected:** Postgres full-text (`ts_rank` has no IDF, and its parser splits IDs like "t1003.001"); ParadeDB `pg_search` (real BM25 in Postgres, but not available in the local pgserver build).
- **Why:** exact control over tokenisation (identifiers, Arabic), real BM25 scoring, trivially fast at ~3k chunks.
- **Cost:** memory and start-up time grow with tenant size; rebuild needed when the index changes. Revisit above ~100k chunks per tenant (move to pg_search or a persisted index).

## D13: Risk: HNSW + filters can return too few rows (tenant filter, concurrent writes)
- **Finding:** pgvector's HNSW scan takes `ef_search` nearest candidates and only then applies `WHERE` and row visibility. During a re-index (uncommitted rows in the graph) dense results dropped.
- **Chose (for now):** `hnsw.ef_search = 200`; never evaluate while indexing.
- **Risk:** with many tenants in one table, a small tenant's rows may not be among the candidates at all, so its search returns little or nothing.
- **Plan:** before multi-tenant use (phase 4): pgvector >= 0.8 iterative scans (`hnsw.iterative_scan`), or per-tenant partial indexes / partitions, plus a test with one large and one tiny tenant.

## D14: Arabic questions are also searched in an English translation (local LLM)
- **Chose:** `core/query.py` translates Arabic-script questions with qwen2.5:3b (temperature 0, keep IDs/terms), and hybrid search fuses dense + keyword for both the original and the translation.
- **Rejected:** llama3.2:3b (same speed, mistranslated terms such as "PowerShell" -> "PoshShell"); hosted LLM (no API key on the dev machine; cost); no translation (keyword search is blind to Arabic questions).
- **Why:** probe Arabic hit@5 0.75 -> 1.0; rescues questions dense alone missed entirely (ransomware, scheduled tasks, MFA).
- **Cost:** ~2.3 s per Arabic question on CPU; hit@1 can drop (siblings crowd in), which the reranker (step 4b) must fix. Translation quality depends on a 3B model.

## D15: Claude Haiku 5.5 for translation and reranking; bge-m3 stays for embeddings (supersedes D14's model)
- **Chose:** `claude-haiku-5-5` via the Anthropic SDK (`core/llm.py`) translates Arabic questions (`ClaudeTranslator`) and reranks the fused top 30 listwise (`ClaudeReranker`). Embeddings stay bge-m3 (local, Ollama).
- **Rejected:** local qwen2.5:3b translation + bge-reranker-v2-m3 cross-encoder (probe hit@1 0.656 / MRR 0.785 vs Haiku 0.906 / 0.932; rerank 42 s per question on CPU vs ~1.7 s); mmarco-mMiniLMv2 (fast, 7 s, but MRR 0.794 and it misranked a simple Arabic case); Claude for embeddings (the Anthropic API has no embeddings endpoint).
- **Why:** best ranking quality measured, no CPU load, and reads Arabic directly, so the reranker needs only the original question.
- **Cost:** ~$0.001 per question (rerank ~10.3k input tokens, ~78 output); translation ~$0.00001. Needs network + API key; calls are logged in `data/llm_usage.jsonl` and refused above `LLM_BUDGET_USD`. The local stack stays available (`--stack local`) as an offline fallback and baseline.

## D16: Documents include their relationships as text
- **Chose:** at ingest, append lists like "Software used: Mimikatz (S0002); ..." to groups, campaigns, software, mitigations and techniques (mitigations, detections, parent), 10 items per line. Techniques' "used by" lists are left out (hundreds of entries).
- **Rejected:** keeping relationships only in `attack_relations.jsonl` (search never sees them); graph traversal at query time (more moving parts; planned as a stretch goal, not needed for this).
- **Why:** "Which software does APT29 use?" had no document containing the answer: the group doc held only MITRE's description, so the reranker (correctly) preferred individual malware docs and dropped the group doc. attack.mitre.org pages show these lists; the documents now do too.
- **Cost:** median document 671 -> 1,036 chars, chunks 2,977 -> 3,294; most documents had to be re-embedded.

## D17: Documents named by ID in the question are pinned first
- **Chose:** `HybridSearcher(pin_ids=True)`: identifier tokens in the question that match a doc ID (or a revoked ID via `aliases`) put that doc at the top, in the order typed.
- **Why:** "What is T1486?" ranked detection strategies that list T1486 above T1486 itself. Someone who types an ID means that object. Spot-check mixed hit@1 0.667 -> 1.0.
- **Cost:** a question that mentions an ID only in passing ("unlike T1059, how does ...") still gets that doc first; acceptable since it is relevant context.

## D18: Relationship questions are judged against ATT&CK's own links ("relevant_via")
- **Chose:** a question may list `relevant_via` specs, e.g. `{"source": "G0032", "relation": "uses", "type": "software"}`; every document ATT&CK links that way counts as a correct answer, in addition to the listed IDs. The check also reports the strict score (listed IDs only).
- **Rejected:** keeping only the group doc as the answer (the reranker returned three Lazarus malware docs, each saying "Used by groups: Lazarus Group (G0032)", and the eval scored that as a miss); hand-picking extra IDs (subjective, incomplete).
- **Why:** relevance judgments must accept every correct answer, and for "which software does X use" ATT&CK's relationship data defines exactly what is correct. Applied to s20 (Lazarus software, 27 docs) and mx04 (APT29 tools and techniques).
- **Cost:** broad questions become easier to "hit" (mx04 now accepts ~115 docs). The phase 2 golden set should use graded relevance (the group page that lists everything > one linked doc).

## D19: LLM-reranked results are judged over several runs
- **Finding:** two runs of the identical Haiku pipeline gave spot-check hit@1 0.75 and 0.65 (English 0.75 vs 0.50), probe MRR 0.922 vs 0.945. Sampling cannot be pinned (`temperature` is rejected by the API since 2026-10-21).
- **Chose:** treat a single run's hit@1 differences of this size as noise; the phase 2 harness runs the reranked pipeline at least 3 times and reports mean and spread. Recall-style metrics (hit@5) were stable across runs and carry more weight in gates.
- **Cost:** ~3x API spend per eval run (still ~$0.6 for the current sets).
