"""Postgres + pgvector smoke test. Runs in CI (DATABASE_URL from the service container) and locally
with Docker or pgserver; skipped when neither is available."""

import pytest

from core import db


@pytest.fixture(scope="module")
def conn():
    try:
        c = db.connect(autocommit=True)
    except RuntimeError as e:
        pytest.skip(str(e))
    yield c
    c.close()


def test_vector_extension(conn):
    version = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
    assert version is not None


def test_cosine_search_with_hnsw(conn):
    conn.execute("CREATE TEMP TABLE t (id int, v vector(3))")
    conn.execute("INSERT INTO t VALUES (1, '[1,0,0]'), (2, '[0,1,0]'), (3, '[0.9,0.1,0]')")
    conn.execute("CREATE INDEX ON t USING hnsw (v vector_cosine_ops)")
    rows = conn.execute("SELECT id FROM t ORDER BY v <=> '[1,0,0]' LIMIT 2").fetchall()
    assert [r[0] for r in rows] == [1, 3]
