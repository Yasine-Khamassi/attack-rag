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

## 2026-10-29 · Step 4a: Chunking, hybrid index, query translation (phase 1, in progress)

**Done**
- `core/text.py` (normalisation + tokenizer: identifiers stay whole, Arabic letter/diacritic/digit normalisation, clitic + article stripping), `core/chunking.py` (max 1,500 chars, header repeated in every chunk), `core/bm25.py` (BM25, k1=1.2, b=0.75).
- `core/index.py`: `chunks` table (pgvector, HNSW, cosine) + `embedding_cache` keyed by text hash; `core/search.py`: dense, keyword, hybrid (RRF k=60), tenant-scoped.
- `core/query.py`: optional translation of Arabic-script questions to English with a local LLM (qwen2.5:3b via Ollama).
- `products/attack/index.py`, `products/attack/retrieval_check.py`, `eval/spot_checks.jsonl` (20 new questions: 8 EN, 6 AR, 6 mixed/ID).
- Tests: 48 passed (incl. tenant isolation for dense/keyword/hybrid, cache reuse, RRF).

**Numbers**
- 2,530 docs -> 2,977 chunks (median 673 chars, max 1,500, max 4 chunks per doc). 2,123 vectors reused from the probe cache, 854 embedded in 385 s.
- Spot checks hit@5 (all 20): dense 0.75 · keyword 0.65 · hybrid 0.90 · hybrid+translation 0.85. MRR: 0.601 / 0.475 / 0.706 / 0.712.
- Probe (32) hit@5: dense 0.781 · keyword 0.562 · hybrid 0.906 · hybrid+translation **1.0**. MRR: 0.683 / 0.434 / 0.742 / 0.721.
- Keyword on Arabic questions: 0.0 (English docs share no Arabic words). Translation moves probe Arabic hit@5 0.75 -> 1.0 but hit@1 0.667 -> 0.5.
- Translation: ~2.3 s per Arabic question on CPU (qwen2.5:3b; llama3.2:3b similar speed, worse terms: "PoshShell").

**Gate: not passed yet** (17-18/20 in top 5). Misses: s12 (Arabic process injection; returns T1055.009/.005/.011 sub-techniques, label expects parent T1055), s13 (Arabic port scanning, rank 6-8), s16 "What is T1486?" (detection docs that list T1486 outrank T1486).

**Problems found**
- First retrieval run happened while the index was being rewritten: HNSW returned uncommitted/dead rows as candidates and filtered them out afterwards, so dense lost results. Re-run after: deterministic (query embeddings identical; HNSW == exact top-20 for 52/52 questions). Same mechanism applies to tenant filters (D13).
- Translation helps recall but pushes already-correct answers from rank 1 to 2-3 (sibling sub-techniques and detection docs).

**Next**
- Step 4b: exact-ID pinning (question contains a doc ID or revoked ID -> that doc first) and a multilingual cross-encoder reranker (bge-reranker-v2-m3) over the fused top candidates. Re-run the gate.

## 2026-10-29 · Step 4b: ID pinning, reranking, Claude Haiku 5.5, relationship text (phase 1, in progress)

**Done**
- `HybridSearcher(pin_ids=True)` + `aliases` (revoked IDs) (D17); reranking over the fused top 30 (`core/rerank.py`).
- Local rerankers measured: bge-reranker-v2-m3 and mmarco-mMiniLMv2 (sentence-transformers 6.2, torch 2.15 CPU).
- Claude API: `core/llm.py` (Anthropic SDK 2.3, `claude-haiku-5-5`, thinking off, effort low, every call logged to `data/llm_usage.jsonl`, hard stop at `LLM_BUDGET_USD`), `ClaudeTranslator`, `ClaudeReranker` (listwise). `retrieval_check --stack haiku|local` (D15).
- Ingest now writes relationships into documents ("Software used: ...", "Detected by: ...") (D16).
- Tests: 58 passed (budget enforcement, rerank parsing, pinning, stale-cache seeding regression).

**Numbers** (probe 32 questions / spot checks 20; hit@1 / hit@5 / MRR@10, full pipeline)
- Local stack (qwen2.5:3b + bge-reranker-v2-m3): probe 0.656 / 0.969 / 0.785 · spot 0.80 / 0.95 / 0.859 · rerank **42 s/question** on CPU. mmarco-MiniLM: probe MRR 0.794, 7 s/question.
- Haiku stack, before relationship text: probe 0.906 / 0.969 / 0.932 · spot 0.75 / 0.95 / 0.85 · rerank ~1.7 s, ~$0.00107/question (10.3k input tokens).
- Haiku stack, with relationship text (reranker saw 800 chars/chunk): probe 0.875 / **1.0** / 0.938 (Arabic MRR 0.958; APT29-tools question fixed) · spot 0.60 / 0.95 / 0.767.
- Pinning: spot mixed hit@1 0.667 -> 1.0.
- Relationship text: median doc 671 -> 1,036 chars; 3,294 chunks (max 6 per doc).
- Claude spend so far: $0.17 (all runs above).

**Problems found**
- **Bug (fixed):** `seed_from_probe` matched probe vectors to documents by count only. After the relationship text changed 1,827 documents, it filed old-text vectors under new-text hashes. Caught from the log line "Seeded 1827 vectors". Fixed: the probe file must carry the current corpus key (`corpus_key()`, shared by probe and index); the 1,827 bad cache rows were deleted and re-embedded; regression test added (`tests/test_attack_index.py`).
- The Haiku reranker dropped group docs for "what software does X use" questions because group docs held no software lists (fixed by D16) and because it only saw the first 800 chars of each chunk (lists were cut off; now 1,500).
- After D16, spot-check hit@1 fell 0.75 -> 0.60: mostly rank 1 -> 2 swaps where a detection-strategy doc for the same technique moved above the technique. Measuring LLM run-to-run variance before drawing conclusions (sampling can't be pinned: `temperature` is rejected by the API since 2026-10-21).
- s20 (Arabic "which software does Lazarus use") still misses.

**Next**
- Two Haiku runs with full chunks to measure variance; then decide on D16 placement, write the step 4 explainer, close the phase 1 gate.

## 2026-10-29 · Step 4b result: phase 1 gate passed

**Done**
- Reranker sees full chunks (1,500 chars, was 800). Two identical Haiku runs to measure variance (D19).
- `relevant_via` labels for relationship questions (D18); `retrieval_check` keeps each mode's top 10 and reports strict scores too.
- `docs/steps/04-hybrid-search.md`.

**Numbers** (Haiku stack, full pipeline; hit@1 / hit@5 / MRR@10)
- Spot checks: dense 0.45 / 0.85 / 0.622 -> hybrid 0.50 / 0.85 / 0.654 -> +translate 0.50 / 0.85 / 0.682 -> +pin 0.65 / 0.95 / 0.807 -> +rerank **0.80 / 1.0 / 0.892**.
- Probe: dense 0.625 / 0.781 / 0.698 -> full pipeline **0.812 / 1.0 / 0.896**.
- **Phase 1 gate: 20/20 spot checks with a correct doc in the top 5 (strict labels also 20/20 this run). PASS.**
- Variance (identical config): spot hit@1 0.75 vs 0.65, probe MRR 0.922 vs 0.945; hit@5 stable (0.95-1.0).
- Rerank with full chunks: ~20.1k input tokens, $0.00205 and 1.77 s per question. One 52-question run ~$0.11.
- Latency (CPU, warm, translation cached): dense ~2.1 s (bge-m3 query embedding on CPU), keyword 5 ms, full pipeline ~5.1 s.
- Claude spend to date: $0.49 (453 calls). Budget cap in `.env`: $5.

**Problems found**
- D15's cost estimate ($0.001/question) was for 800-char excerpts; full chunks double it ($0.002).
- Query embedding on CPU (~2 s) is now the slowest local step, not the reranker.

**Next**
- Phase 2: golden set of 100+ questions (EN/AR/mixed, graded relevance, Arabic reviewed by a native speaker), eval harness (recall@k, MRR, nDCG, faithfulness), several runs per reranked config, CI regression gate.

## 2026-10-30 · Step 5: Golden set, graded metrics, CI regression gate (phase 2)

**Done**
- `eval/golden.jsonl`: 116 questions (49 English, 49 Arabic, 18 mixed/ID) generated from ATT&CK templates, labels from the data, graded relevance (D20). Haiku rewrites + Arabic translations, cached by prompt hash in `eval/golden_llm_cache.json`.
- `core/metrics.py` (hit@5, recall@10, MRR@10, nDCG@10), `products/attack/eval_golden.py`, `eval/baseline.json`, `tests/test_eval_gate.py` (D21).
- Tests: 66 passed (incl. 5 gate tests).

**Numbers** (116 questions; full pipeline = mean of 3 runs, min-max in brackets)
- dense: hit@5 0.569 · recall@10 0.601 · MRR 0.486 · nDCG@10 0.454 · 2.1 s/question
- keyword: 0.655 · 0.746 · 0.556 · 0.581 · 7 ms
- hybrid + ID pinning: 0.802 · 0.838 · 0.678 · 0.660 · 2.1 s
- **full (+ Haiku translation + rerank): 0.877 (0.871-0.888) · 0.873 · 0.784 · 0.768 (0.764-0.770) · 5.2 s**
- Full by language (nDCG / hit@5): Arabic 0.745 / 0.864 · English 0.733 / 0.844 · mixed 0.922 / 1.0.
- Full by template (nDCG / hit@5): alias 0.979 / 1.0 · campaign 0.974 / 1.0 · revoked ID 1.0 / 1.0 · ID 0.897 / 1.0 · detect 0.858 / 0.967 · definition 0.804 / 0.944 · group->software 0.779 / 0.945 · software->groups 0.684 / 0.972 · **mitigate 0.232 / 0.271**.
- Cost: golden set generation $0.0058; one eval (3 full runs) $0.73. Claude spend to date ~$1.23.

**Problems found**
- Haiku leaked answers into rewritten questions (group names, IDs; once via the Arabic translation). Fixed with description-only input, an explicit rule and an automatic leak check (D20); the CI gate re-checks it.
- Committing LLM prompts would have copied MITRE description text into the repo; the cache is keyed by prompt hash instead.
- "How do I mitigate X?" is far below every other template (nDCG 0.232).

**Gate (phase 2): baseline scores recorded, CI fails on regression. Passed.**

**Next**
- Step 6: mitigation questions. Check whether technique-specific mitigation guidance (in `mitigates` relationship descriptions) is missing from the documents.
