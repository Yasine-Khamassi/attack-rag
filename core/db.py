"""Database connection. Postgres + pgvector everywhere; how we reach it depends on the machine.

1. DATABASE_URL set (Docker Compose, CI, production) -> use it.
2. Otherwise, if the optional `pgserver` package is installed -> start a local Postgres + pgvector
   in `data/pgdata/` (no Docker, no admin rights). Development only.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import psycopg.conninfo
import psycopg.sql

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
    """Admin connection (indexing, migrations). Make sure the vector extension exists."""
    conn = psycopg.connect(database_url(), **kwargs)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.commit()
    return conn


# --- Tenant isolation (DECISIONS D31) -------------------------------------------------------------------------
# The serving path connects as APP_ROLE: a login role that is not a superuser and does not own the tables, so
# Postgres row-level security applies to every query it runs. Policies only show rows whose tenant_id equals the
# session setting app.tenant_id, which connect_app() sets once when the connection opens.

APP_ROLE = "rag_app"


def app_password() -> str:
    return os.environ.get("APP_DB_PASSWORD", "rag-app-dev-only")


def admin_bypasses_rls(conn: psycopg.Connection) -> bool:
    """True when the connected role ignores row-level security (superuser or BYPASSRLS), as the local and CI
    admin does. Hosted Postgres often gives the owner neither (DECISIONS D41)."""
    row = conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()
    return bool(row and row[0])


def ensure_app_role(conn: psycopg.Connection) -> None:
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone()
    if not exists:
        # Role names can't be query parameters; APP_ROLE is a constant, the password is passed as a literal.
        conn.execute(psycopg.sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(
            psycopg.sql.Identifier(APP_ROLE), psycopg.sql.Literal(app_password())))
    conn.commit()


def connect_app(tenant_id: str, search_path: str | None = None, **kwargs) -> psycopg.Connection:
    """Serving connection for one tenant: logs in as APP_ROLE and pins app.tenant_id for the whole session."""
    if not tenant_id:
        raise ValueError("tenant_id is required")
    url = psycopg.conninfo.make_conninfo(database_url(), user=APP_ROLE, password=app_password())
    conn = psycopg.connect(url, **kwargs)
    if search_path:
        conn.execute(psycopg.sql.SQL("SET search_path TO {}, public").format(psycopg.sql.Identifier(search_path)))
    conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
    conn.commit()
    return conn
