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
- Phase 0 in progress.
- Step 1 done: `src/ingest/attack.py` → `data/processed/attack_docs.jsonl` (2,530 docs: 706 techniques,
  703 detection strategies, 842 software, 178 groups, 57 campaigns, 44 mitigations) and
  `attack_relations.jsonl` (21,660 relationships).
- Finding: current ATT&CK has no `x_mitre_detection` field; detection lives in detection-strategy
  (DETxxxx) + analytic (ANxxxx) objects. Built separate detection docs (DECISIONS D3).

## Next step
1. Restructure: move ingestion to `products/attack/ingest.py`; create `core/schema.py` with the shared
   document model (`doc_id`, `tenant_id`, `source`, `type`, `title`, `text`, `url`, `metadata`, `content_hash`).
2. Add `pyproject.toml`, a Makefile (`make ingest`, `make test`), and a test that the ATT&CK output validates
   against the core schema.
3. Then phase 1: Docker Compose with Postgres + pgvector. Before choosing the embedding model, run a small
   test of 2–3 multilingual models (English technique IDs + Arabic sentences) and log the result.

## Stack
Python 3.11+, Postgres + pgvector (Docker), rank_bm25 or Postgres full-text, Claude API, FastAPI, pytest,
GitHub Actions.

## Commands
- `python -m src.ingest.attack` — download ATT&CK and build processed docs (will move in the restructure)
