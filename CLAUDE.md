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
- Steps 1-2: ingestion (2,530 docs, 21,660 relationships) and the `core/` + `products/attack/` split (D5-D7).
- **Phase 1 in progress.**
  - Step 3a: Postgres + pgvector (`docker-compose.yml`; local fallback `pgserver`, D9), CI green on GitHub
    Actions, revoked-ID redirects (157 IDs, D8).
  - Step 3b: embedding probe -> **bge-m3** (D10). Dense misses bare IDs for every model -> hybrid (D11).
  - Step 4a (committed): chunking (2,977 chunks), BM25 (D12), pgvector index + embedding cache, tenant-scoped
    hybrid search (RRF), Arabic->English query translation with qwen2.5:3b (D14). Spot-check gate 17-18/20.
    HNSW + filter risk recorded (D13).
  - Step 4b (in progress, not committed): exact-ID pinning (`HybridSearcher.pinned`, aliases = revoked IDs)
    and cross-encoder reranking (`core/rerank.py`: bge-reranker-v2-m3 vs mmarco-mMiniLMv2). Ablation run:
    `python -m products.attack.retrieval_check` -> `eval/results/retrieval_check.json`.
- Corpus is ATT&CK **v20**: detection lives in DETxxxx/ANxxxx objects (D3).
- Dev machine (Windows, CPU only): no `make`, no Docker/WSL. Ollama 0.40.6 (`ollama serve`) with bge-m3,
  snowflake-arctic-embed2, embeddinggemma, qwen2.5:3b, llama3.2:3b. venv has torch 2.15 CPU +
  sentence-transformers 6.2 (extra `rerank`). Reranker weights in the Hugging Face cache.

## Next step
Finish step 4b: read the ablation numbers, write `docs/steps/04-hybrid-search.md`, LOG, DECISIONS (reranker,
pinning), commit + push. If the phase 1 gate (20/20 spot checks in top 5) passes, phase 2: the 100-question
golden set (graded relevance: parent vs sub-technique), eval harness (recall@k, MRR, nDCG, faithfulness) and a
CI regression gate.

## Stack
Python 3.11+, Postgres + pgvector (Docker), rank_bm25 or Postgres full-text, Claude API, FastAPI, pytest,
GitHub Actions.

## Commands
- `make install` — create `.venv` and install the package + pytest
- `make ingest` — download ATT&CK (cached in `data/raw/`) and build `data/processed/*.jsonl`
- `make test` — run pytest (real-data tests skip until `make ingest` has run; DB tests use DATABASE_URL or pgserver)
- `make db` — Postgres + pgvector in Docker (optional; without it `core/db.py` starts pgserver)
- `python -m products.attack.index` — chunk + embed + write the pgvector index (needs `ollama serve`)
- `python -m products.attack.retrieval_check` — dense/keyword/hybrid/+translate/+pin/+rerank ablation + phase 1 gate
- `python -m products.attack.embedding_probe [model ...]` — embedding-model probe (needs `ollama serve`; ~7 min/model first run, cached after)
