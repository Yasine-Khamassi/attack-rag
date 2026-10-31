# T-006 Keep reasoning text out of answers
Status: todo
Who: Claude

**Why:** with weak context a model sometimes writes its own reasoning into the answer ("let me check ...", "the question is in English, so ..."), seen 2026-10-31 in a downstream deployment.

**Done when:** the answer step strips or prevents self-talk; a test covers both strings; answer eval still 100% cited.
