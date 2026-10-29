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
