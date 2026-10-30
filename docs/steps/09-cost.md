# Step 10 (phase 6): Caching, routing and cost per conversation, explained

Every question costs money: translation, rerank and answer are Claude Haiku 5.5 calls. Phase 6 makes the cost
visible per question, cuts the biggest item, and skips work that doesn't change the answer.

## Where the money went

Measured from `data/llm_usage.jsonl` (every Claude call is logged with tokens and price): during evaluation the
**listwise rerank was 81% of the spend**, because it sent 30 candidates of ~3,000 characters each (~24k input
tokens per call).

Fix (D30): rerank the top 15 candidates, 600 characters each. Rerank cost per call: $0.00246 -> $0.00064 (3.8x
cheaper). Quality trade-off, measured on the golden set (3 runs): MRR and hit@5 held, recall@10 fell
0.912 -> 0.873. The CI gate caught the drop and failed; the baseline was re-set deliberately, with the reason
recorded.

## Caches

`core/cache.py`: in-memory TTL + LRU caches.

| Cache | Key | Lifetime | Why it is safe |
|---|---|---|---|
| Query embedding | model + text | 7 days | embeddings are deterministic |
| Rerank result | question + candidate IDs + text hash | 24 h | same inputs, same ordering wanted |
| Answer | tenant + **index fingerprint** + normalised question | 24 h | re-indexing changes the fingerprint, so stale answers can't be served |

Refusals, hand-offs and answers with security findings are never cached. Evaluations reset the rerank and
translation caches on every run, otherwise three runs replay one (D38).

## Routing

`core/routing.py` decides per question what the pipeline needs. A question that is just one ATT&CK ID ("T1003.001",
"ما هي التقنية T1059؟") is pinned by exact-ID lookup anyway, so translation and rerank are skipped. Everything else
takes the full path.

## Results

`python -m products.attack.cost_report` reads the per-question cost the assistant records (all Claude calls for
that question; the evaluation judge excluded) from the fresh-sample answer eval:

| | Cost |
|---|---|
| Mean per question | $0.0010 |
| p95 per question | $0.0014 |
| ID lookup (routed) | $0.0005 |
| Full path | $0.0012 |
| Per conversation (3 questions, no cache hits) | $0.003 |
| Per 1,000 conversations | ~$3 |

Answer quality on the same run (full pipeline with tools, guards, routing): 100% of answers fully cited,
faithfulness 0.970, refusals 12/12.

## Things to be able to explain in an interview

- **How did you find what to optimise?** Logged every call with its purpose and price, then summed by purpose. The
  answer (rerank, 81%) was not the obvious one (the answer call).
- **How do you cache LLM answers without serving stale ones?** Key by a fingerprint of the index content.
- **How do you know the cheaper pipeline is still good?** The golden-set gate; a drop has to be accepted on purpose.
