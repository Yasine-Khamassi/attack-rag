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
- **Phase 0 done.** Steps 1-2: ingestion (2,530 docs, 21,660 relationships), `core/` + `products/attack/` split.
- **Phase 1 done** (gate: 20/20 spot checks in the top 5). Steps 3-4, details in `docs/steps/03-*`, `04-*`:
  - Postgres + pgvector (`docker-compose.yml`; local fallback `pgserver`, D9); CI green on GitHub Actions.
  - Embeddings: **bge-m3** via Ollama (D10). Keyword: BM25 in `core/bm25.py` (D12). Fusion: RRF.
  - Claude **Haiku 5.5** (`core/llm.py`) translates Arabic questions and reranks the top 30 listwise (D15).
    Every call is logged to `data/llm_usage.jsonl`; calls are refused above `LLM_BUDGET_USD` (.env, $5).
  - Exact-ID pinning incl. revoked IDs (D8, D17); relationships written into document text (D16).
  - Probe (32 q): dense hit@5 0.781 / MRR 0.698 -> full pipeline 1.0 / 0.896. Claude spend to date $0.49.
  - Known risks: HNSW + filters (D13); LLM reranker variance, judge over several runs (D19).
- Corpus is ATT&CK **v20**: detection lives in DETxxxx/ANxxxx objects (D3).
- Dev machine (Windows, CPU only): no `make`, no Docker/WSL. Ollama 0.40.6 (`ollama serve`). venv has torch CPU +
  sentence-transformers (local `--stack local` only) and anthropic 2.3. API key in gitignored `.env`.
- Model rule from the owner: use Claude Haiku 5.5 by default; Sonnet/Opus 5.5 only for one-off quality checks;
  stop all Claude use and tell the owner if remaining credit would fall to $50.

- **Phase 2 gate passed** (step 5): golden set `eval/golden.jsonl` (116 q, graded, leak-checked, D20), metrics
  `core/metrics.py`, `products/attack/eval_golden.py`, baseline `eval/baseline.json`, CI gate
  `tests/test_eval_gate.py` (D21). Full pipeline: hit@5 0.877, nDCG@10 0.768 (3 runs, spread +-0.01).
  **Rule: any change to retrieval code re-runs `eval_golden` (~$0.73) and commits the results.**
  Weak spot: mitigate questions nDCG 0.232.

## Next step
Step 6: fix "how do I mitigate X?" (likely missing: technique-specific guidance in `mitigates` relationship
descriptions). Then phase 3: answering (cited answers with Claude Haiku 5.5, refuse when sources are thin, FastAPI +
simple web page, faithfulness metric). Arabic golden questions still need a native-speaker review.

## Stack
Python 3.11+, Postgres + pgvector (Docker or pgserver), BM25 (core/bm25.py), bge-m3 embeddings (Ollama),
Claude API (Haiku 5.5), FastAPI, pytest,
GitHub Actions.

## Commands
- `make install` — create `.venv` and install the package + pytest
- `make ingest` — download ATT&CK (cached in `data/raw/`) and build `data/processed/*.jsonl`
- `make test` — run pytest (real-data tests skip until `make ingest` has run; DB tests use DATABASE_URL or pgserver)
- `make db` — Postgres + pgvector in Docker (optional; without it `core/db.py` starts pgserver)
- `python -m products.attack.index` — chunk + embed + write the pgvector index (needs `ollama serve`)
- `python -m products.attack.retrieval_check [--stack haiku|local]` — stage-by-stage ablation + phase 1 gate (~$0.11 per Haiku run)
- `python -m products.attack.embedding_probe [model ...]` — embedding-model probe (needs `ollama serve`; ~7 min/model first run, cached after)
