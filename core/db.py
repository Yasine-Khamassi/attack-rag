"""Database connection. Postgres + pgvector everywhere; how we reach it depends on the machine.

1. DATABASE_URL set (Docker Compose, CI, production) -> use it.
2. Otherwise, if the optional `pgserver` package is installed -> start a local Postgres + pgvector
   in `data/pgdata/` (no Docker, no admin rights). Development only.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LOCAL_PGDATA = ROOT / "data" / "pgdata"


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    try:
        import pgserver
    except ImportError as e:
        raise RuntimeError(
            "No database: set DATABASE_URL (docker compose up -d db) or pip install -e '.[local-db]'"
        ) from e
    # cleanup_mode=None keeps the server running between processes; data persists in data/pgdata/.
    return pgserver.get_server(LOCAL_PGDATA, cleanup_mode=None).get_uri()


def connect(**kwargs) -> psycopg.Connection:
    """Open a connection and make sure the vector extension exists."""
    conn = psycopg.connect(database_url(), **kwargs)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.commit()
    return conn
