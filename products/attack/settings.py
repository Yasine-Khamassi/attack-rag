"""The production pipeline settings, shared by the assistant, the API and the evals (so they can't drift apart)."""

LLM_MODEL = "claude-haiku-5-5"   # translation, reranking, answers (owner's rule: Haiku by default)
JUDGE_MODEL = "claude-sonnet-5-5"  # evaluation only (faithfulness)

# Reranking is ~80% of API spend (DECISIONS D30): top 15 candidates, 600-char excerpts, no link expansion.
RERANK_DEPTH = 15
RERANK_CHARS = 600
EXPAND_TOP = 0  # link expansion showed no gain (D25); 5 = previous setting

# Tools the answering model may call (phase 4, D33). Enabled after the answer eval showed no regression.
USE_TOOLS = True

# Answer cache lifetime (phase 6, D36). The key includes the index fingerprint, so new data invalidates it.
ANSWER_CACHE_SECONDS = 24 * 3600
