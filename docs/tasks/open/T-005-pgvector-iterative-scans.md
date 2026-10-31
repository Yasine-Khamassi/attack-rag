# T-005 pgvector >= 0.8 iterative scans locally
Status: todo
Who: Claude

**Why:** small tenants sharing an HNSW index can starve (D13); exact scans work around it, iterative scans fix it properly.

**Done when:** the local Postgres has pgvector >= 0.8, `hnsw.iterative_scan` is on, and the tenancy test passes without the exact-scan path.
