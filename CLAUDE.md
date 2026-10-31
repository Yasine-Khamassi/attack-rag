# CLAUDE.md — attack-rag (PUBLIC repo)

**Start here:** `docs/STATUS.md` (verified facts with dates; trust it over memory), then `docs/tasks/README.md`
(open tasks, one file each). When a task finishes, move its file to `docs/tasks/done/` and update STATUS + LOG.

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

## Current state (2026-10-30)
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
- Step 6: mitigation advice ingested (D22), link expansion (D23, D25), mitigate labels revised (D24). Current
  baseline (full, 3 runs): hit@5 0.957, recall@10 0.912, MRR 0.851, nDCG@10 0.786. `eval_golden --rescore`
  re-scores saved rankings for free. Claude spend to date $2.58 (cap $5 in .env).

- **Phase 3 gate passed** (step 7): `core/answer.py` (cite every sentence or `NO_ANSWER`, language + citation
  guards, D26/D29), `core/judge.py` (Sonnet 5.5 faithfulness, D27), `products/attack/assistant.py`, `api.py` +
  `static/index.html` (`uvicorn products.attack.api:app --port 8000`). Fresh sample: 100% fully cited, 12/12
  refusals, faithfulness 0.960. Prompt changes must be validated with `eval_answers --seed 12` (D28).
- Claude spend to date $4.11; cap `LLM_BUDGET_USD=42.58` in .env = owner's $95 balance - $50 floor - $5 margin.

- **Phase 4 gate passed** (step 8, D31-D33): serving path uses `db.connect_app(tenant)` (login role `rag_app`,
  no superuser, no BYPASSRLS; `app.tenant_id` pinned per session) + RLS on `chunks` and `conversations`; exact
  search for tenants <= 20k chunks (HNSW starvation reproduced in `tests/test_tenancy.py`); `core/convlog.py`
  (per-turn log), `core/tools.py` (strict schemas, allowlist, tenant injected by code) + `lookup_attack_object`.
  Serving connections run in autocommit (D37).
- **Phase 5 gate passed** (step 9, D34-D35): `core/guardrails.py` (input / source / output), `core/security_log.py`
  (ECS JSON -> `data/security_events.jsonl`), `products/attack/redteam.py`: ASR 22.2% -> 5.6% (direct 37.5% -> 0;
  semantic poisoning 50% residual, mitigation = provenance). 0 false positives on corpus + golden questions.
- **Phase 6 done** (step 10, D30, D36, D38): cheap rerank (top 15 x 600 chars), `core/cache.py`, `core/routing.py`,
  `products/attack/cost_report.py`: $0.0010/question, ~$3 per 1,000 conversations. Golden baseline (3 cold runs): hit@5 0.951, recall@10 0.877, MRR 0.856, nDCG 0.766. Shared pipeline in
  `products/attack/settings.py` + `pipeline.py`. Evals reset caches per run (D38).
- Answers (fresh sample, full pipeline): 100% cited, faithfulness 0.970, refusals 12/12.
- Never edit regexes or `\b` through shell heredocs (they became backspace characters twice); use the Edit tool.
  `tests/test_source_hygiene.py` guards the code.
- Tag `core-v0.3.3` = the core used by the downstream product (self-talk guard D43, keyword fallback D42, serverless-ready: `make_embedder()` with
  `EMBEDDINGS_PROVIDER=ollama|cloudflare|gateway`, `LLM_GATEWAY=vercel`, `set_ledger()` for a shared spend limit; D41).

## Next step
All six phases are done. Owner: record the 90-second demo video, review the Arabic golden questions, rotate the
API key that was pasted in chat. Possible core work: document provenance / trusted sources (D35), pgvector >= 0.8
iterative scans, a shared (Redis/Postgres) answer cache for multi-process deployments.

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
