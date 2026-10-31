# attack-rag: status (ground truth)

Read this before saying what exists or works. Each line says when and how it was checked; anything not here is
unverified. History: [LOG.md](LOG.md). Reasons: [DECISIONS.md](DECISIONS.md). Open work: [tasks/](tasks/README.md).

Last full check: **2026-10-31**.

| Fact | Value | Checked |
|---|---|---|
| Repo | github.com/Yasine-Khamassi/attack-rag, **public**, branch `master` | push 2026-10-31 |
| Latest core tag | **core-v0.3.3** (self-talk guard D43; keyword fallback D42; AI Gateway option and serverless support D41) | tag pushed 2026-10-31 |
| Tests | **151 pass** (`python -m pytest -q`) | 2026-10-31 |
| CI | GitHub Actions green on 3652a1e (core 0.3.2) | `gh run list` 2026-10-31 |
| Leak scan | `bash ../leak-scan.sh`: CLEAN | 2026-10-31 |
| Phases | all 6 done (retrieval, eval, cited answers, tenant isolation, security, cost) | LOG 2026-10-30 |
| Retrieval | golden set 116 questions: hit@5 0.951, recall@10 0.877, nDCG 0.766 (3 cold runs) | LOG phase 2 |
| Answers | 100% cited, faithfulness 0.97, refusals 12/12 | LOG phase 3 |
| Security | prompt-injection success 22% -> 6% (direct 37.5% -> 0%) | LOG phase 5 |
| Cost | ~$0.001 per question with Claude Haiku 5.5 | LOG phase 6 |
| Local services | Ollama `bge-m3` at `127.0.0.1:11434` (D40); local Postgres via pgserver in `data/pgdata` (recovers ~40 s after an unclean stop) | 2026-10-31 |

## Run
`docs/steps/` has one file per build step. Commands are in CLAUDE.md "Commands". Embeddings: `make_embedder()` picks
Ollama by default, `EMBEDDINGS_PROVIDER=cloudflare|gateway` on servers; Claude through Vercel AI Gateway with
`LLM_GATEWAY=vercel` (D41).

## Rules that stay true
- Public repo: run the leak scan before every commit.
- The API key lives only in the gitignored `.env`; never print or commit it.
