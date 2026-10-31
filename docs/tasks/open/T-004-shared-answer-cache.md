# T-004 Shared answer cache
Status: todo
Who: Claude

**Why:** the answer cache is per process; serverless hosts run many short-lived processes (D41), so repeated questions pay again.

**Done when:** an optional Postgres-backed cache (like the spend ledger) is used when configured; test with two cache instances.
