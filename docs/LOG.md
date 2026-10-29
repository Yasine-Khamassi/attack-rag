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

## 2026-10-29 · Step 2: Shared core + document schema (phase 0)

**Done**
- Split the repo: `core/schema.py` (shared `Document` model, pydantic) and `products/attack/ingest.py` (moved from `src/ingest/attack.py`, now emits `Document` records).
- `pyproject.toml` (package `attack-rag`, Python >=3.11, deps: pydantic; dev: pytest), `Makefile` (`install`, `ingest`, `test`).
- Tests: schema rules, ingestion on a 9-object fixture bundle, full real-output validation, and a guard that `core/` has no corpus vocabulary.

**Numbers**
- Re-ingest output identical to step 1: 2,530 documents (706 techniques, 703 detection, 842 software, 178 groups, 57 campaigns, 44 mitigations), 21,660 relationships, length min 83 / median 671 / max 4,906 chars.
- Tests: 20 passed (17 unit + 3 on real data). All 2,530 docs validate, doc_ids unique, every relationship endpoint exists.

**Problems found**
- `make` is not installed on the Windows dev machine; ran the same commands directly. Makefile is still the source of truth (CI is Linux).
- Docker is not installed either; needed for phase 1 (Postgres + pgvector).

**Gate (phase 0): passed.** Data loads into the shared schema.

**Next**
- Phase 1 start: Docker Compose with Postgres + pgvector. Before choosing the embedding model, test 2-3 multilingual models (English technique IDs + Arabic sentences) and log the result.

## 2026-10-29 · Step 3a: Postgres + pgvector, revoked-ID redirects (phase 1)

**Done**
- `docker-compose.yml` (pgvector/pgvector:0.8.7-pg16), `.env.example`, `make db`.
- `core/db.py`: uses `DATABASE_URL` if set; otherwise starts a local Postgres + pgvector from the `pgserver` package in `data/pgdata/` (no Docker, no admin). `tests/test_db.py` checks the extension and an HNSW cosine search.
- GitHub Actions CI (`.github/workflows/ci.yml`): pytest against a pgvector service container on every push.
- Ingest: revoked ATT&CK IDs now redirect to their replacement ("Previously: T1070.001" header line + `metadata.previous_ids`).

**Numbers**
- Local DB: PostgreSQL 16.2 + pgvector 0.6.2 (pgserver); HNSW available.
- 157 revoked IDs redirected (149 techniques, 6 groups, 2 software); 3 were chains (A -> B -> C), all end at an active object.
- Documents unchanged in count: 2,530. Tests: 24 passed.

**Problems found**
- The downloaded bundle is ATT&CK **v20**. It revoked well-known IDs, e.g. T1070.001 "Clear Windows Event Logs" -> T1685.005 (2026-04-14). Without redirects, a question about T1070.001 found nothing. Fixed (D8, supersedes D2's cost note).
- No Docker or WSL on the dev machine. Docker Desktop needs WSL2 (admin + reboot), so it was not installed automatically; pgserver used locally instead (D9).
- pgserver's bundled `psql` helper breaks on paths with spaces; connect with psycopg instead.

**Next**
- Step 3b: embedding-model probe (bge-m3, snowflake-arctic-embed2, embeddinggemma via Ollama), English + Arabic + mixed questions.

## 2026-10-29 · Step 3b: Embedding-model probe (phase 1)

**Done**
- `core/embeddings.py`: `OllamaEmbedder` (batching, per-model query/doc prefixes, L2 normalisation, dimension check), model registry, `DEFAULT_MODEL = "bge-m3"`.
- `eval/embedding_probe.jsonl`: 32 questions (12 English, the same 12 in Arabic, 8 Arabic mixed with English IDs/tool names) with target doc IDs.
- `products/attack/embedding_probe.py`: embeds all 2,530 docs per model (cached in `data/probe/`), reports hit@1, hit@5, MRR@10 per language and overall, EN-AR agreement, speed. Results in `eval/results/embedding_probe.json`.
- `tests/test_embeddings.py` (fake server: prefixes, batching, normalisation, dim check). Tests: 28 passed.

**Numbers** (hit@1 / hit@5 / MRR@10)
- bge-m3: EN 0.583 / 0.833 / 0.720 · AR 0.667 / 0.750 / 0.688 · mixed 0.500 / 0.750 / 0.625 · all 0.594 / 0.781 / 0.684 · EN-AR query cosine 0.826
- snowflake-arctic-embed2: EN 0.667 / 1.000 / 0.819 · AR 0.333 / 0.667 / 0.465 · mixed 0.625 / 0.750 / 0.656 · all 0.531 / 0.812 / 0.646 · cosine 0.695
- embeddinggemma: EN 0.583 / 1.000 / 0.778 · AR 0.417 / 0.750 / 0.604 · mixed 0.625 / 0.750 / 0.688 · all 0.531 / 0.844 / 0.690 · cosine 0.728
- Speed (CPU): 6.3 / 6.3 / 6.7 docs/s to embed; about 150 ms per query warm for all three. Full probe run: 20 min.

**Problems found**
- Bare-ID questions ("ما هي التقنية T1003.001؟", "ما معنى T1070.001؟") miss the top 10 for all three models: dense retrieval cannot match identifiers. Keyword search must cover them.
- Arabic "ransomware" question failed for all models (doc title is "Data Encrypted for Impact").
- The first query after a model loads is ~0.5 s; measure latency warm.
- Arabic probe questions were written for the probe; need a native-speaker review before reuse in the golden set.

**Next**
- Step 4: chunking + dense index (bge-m3 vectors in pgvector, HNSW) + keyword index (BM25 / Postgres full-text, Arabic normalisation), then RRF fusion. Phase 1 gate: 20 spot-check questions return the right docs.
