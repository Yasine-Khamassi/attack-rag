# CLAUDE.md — attack-rag (PUBLIC repo)

Read this first every session. Then read `docs/LOG.md` (latest entry) and `docs/DECISIONS.md`.

## What this repo is
A hybrid-search RAG system over MITRE ATT&CK that answers SOC analyst questions with cited sources.
It is a public portfolio project for junior SOC / Security Analyst / AI engineering roles.

The repo also holds the **shared RAG core** (`core/`), a reusable Python package that any corpus can
plug into. Corpus-specific code lives in `products/<name>/`.

## How I want you to work
- **Explain every step.** Before writing code for a step, say what it does and why in plain words.
  After the step, stop, show the result (numbers, sample output), and wait for me before the next step.
- After every step, update:
  - `docs/steps/NN-<name>.md` — explanation of the step for a beginner (what, why, how, what to say in an interview)
  - `docs/LOG.md` — append-only: date, what was done, numbers, problems, next step
  - `docs/DECISIONS.md` — any choice made (chose / rejected / why / cost). Never edit old entries.
- Look at real data before building on it. Print samples.
- Commit after each completed step with a clear message. **Do not add yourself as co-author.**
- If a Makefile exists, use its targets.
- Keep `core/` free of corpus vocabulary (no "ATT&CK", "MITRE", "technique", "tactic", "STIX").
  Corpus-specific code lives in `products/<name>/`.
- No invented numbers in docs or README. Every metric comes from a script in this repo.

## The plan (phases; each ends with a gate)
| Phase | Core (built once) | ATT&CK product | Gate |
|---|---|---|---|
| 0 Foundations | One document schema with `tenant_id`; split into `core/` + `products/attack/` | Ingestion done (see below) | Data loads into the shared schema |
| 1 Retrieval | Multilingual embeddings (must handle Arabic too), pgvector + BM25, Arabic normalization, RRF fusion, reranker | Index all docs; technique IDs found by keyword search | 20 spot-check questions return the right docs |
| 2 Evaluation | Harness: recall@k, MRR, nDCG, faithfulness; CI gate | 100-question golden set; dense vs BM25 vs hybrid vs +rerank | Baseline scores; CI fails on regression |
| 3 Answering | Cited answers, refuse when sources are thin, persona per product | API + simple web page, links to attack.mitre.org | Every claim cites a retrieved doc |
| 4 Product | Tool framework + allowlist, tenant isolation tests, per-conversation logs | README results table, 90-second demo video | Tenant A can never retrieve tenant B |
| 5 Security | Guardrails, red-team suite, SIEM logging | Poisoned-document attacks, attack success rate before/after | ASR published before and after |
| 6 Scale | Caching, model routing, cost per conversation | Done → move to security projects | — |

Do not start a phase until the previous gate passes.

## Current state (2026-10-29)
- **Phase 0 done** (gate passed: all 2,530 docs validate against `core.schema.Document`).
- Step 1: ingestion -> `data/processed/attack_docs.jsonl` (2,530 docs: 706 techniques, 703 detection strategies,
  842 software, 178 groups, 57 campaigns, 44 mitigations) and `attack_relations.jsonl` (21,660 relationships).
- Step 2: split into `core/` (shared `Document` schema) and `products/attack/ingest.py`. `tenant_id = "attack"` (D5).
- **Phase 1 in progress.** Step 3a done: Postgres + pgvector (`docker-compose.yml`; local fallback `pgserver`
  via `core/db.py`, D9), CI on GitHub Actions with a pgvector service, revoked-ID redirects (D8). 24 tests pass.
- Corpus is ATT&CK **v20**: detection lives in DETxxxx/ANxxxx objects (D3); 157 revoked IDs redirect (T1070.001 -> T1685.005).
- Dev machine (Windows): no `make`, no Docker/WSL. Ollama 0.40.6 is installed (start with `ollama serve`).

## Next step
Step 3b: embedding-model probe. Compare bge-m3, snowflake-arctic-embed2 and embeddinggemma (Ollama, local)
on English, Arabic and mixed questions with known target docs; log recall@5 / MRR / speed; choose the model (D10).
Then step 4: chunking + dense index in pgvector + BM25.

## Stack
Python 3.11+, Postgres + pgvector (Docker), rank_bm25 or Postgres full-text, Claude API, FastAPI, pytest,
GitHub Actions.

## Commands
- `make install` — create `.venv` and install the package + pytest
- `make ingest` — download ATT&CK (cached in `data/raw/`) and build `data/processed/*.jsonl`
- `make test` — run pytest (real-data tests skip until `make ingest` has run; DB tests use DATABASE_URL or pgserver)
- `make db` — Postgres + pgvector in Docker (optional; without it `core/db.py` starts pgserver)
