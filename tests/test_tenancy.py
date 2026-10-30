"""Phase 4 gate: tenant A can never retrieve tenant B, enforced by Postgres row-level security, not only by the
application's WHERE clauses. Also reproduces and fixes the small-tenant HNSW problem (DECISIONS D13, D32)."""

import uuid

import psycopg
import pytest

from core import db
from core.index import index_documents
from core.schema import Document
from core.search import HybridSearcher
from tests.test_index_search import FakeEmbedder

BIG, TINY = "tenant-big", "tenant-tiny"


def doc(doc_id, tenant, text):
    return Document.build(doc_id=doc_id, tenant_id=tenant, source="test", type="note", title=doc_id,
                          text=f"{doc_id}: {doc_id}\nType: note\n\n{text}")


DOCS = ([doc(f"B{i:03d}", BIG, f"credential dumping memory lsass variant {i}") for i in range(300)]
        + [doc("T001", TINY, "phishing attachment invoice"), doc("T002", TINY, "scheduled task persistence"),
           doc("T003", TINY, "password spraying accounts")])


@pytest.fixture(scope="module")
def schema():
    try:
        admin = db.connect()
    except RuntimeError as e:
        pytest.skip(str(e))
    name = f"test_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE SCHEMA {name}")
    admin.execute(f"SET search_path TO {name}, public")
    admin.commit()
    index_documents(admin, DOCS, FakeEmbedder(), progress=lambda *_: None)
    yield name
    admin.rollback()
    admin.execute(f"DROP SCHEMA {name} CASCADE")
    admin.commit()
    admin.close()


@pytest.fixture
def app(schema):
    conns = []

    def open_for(tenant):
        c = db.connect_app(tenant, search_path=schema)
        conns.append(c)
        return c

    yield open_for
    for c in conns:
        c.close()


def test_app_role_is_not_privileged(app):
    c = app(TINY)
    row = c.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()
    assert c.execute("SELECT current_user").fetchone()[0] == db.APP_ROLE and row == (False, False)


def test_rls_hides_other_tenants_even_without_a_where_clause(app):
    tiny, big = app(TINY), app(BIG)
    assert tiny.execute("SELECT count(*) FROM chunks").fetchone()[0] == 3
    assert big.execute("SELECT count(*) FROM chunks").fetchone()[0] == 300
    assert tiny.execute("SELECT count(*) FROM chunks WHERE tenant_id = %s", (BIG,)).fetchone()[0] == 0
    assert {r[0] for r in tiny.execute("SELECT DISTINCT tenant_id FROM chunks")} == {TINY}


def test_app_role_cannot_write_or_disable_rls(app):
    c = app(TINY)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("DELETE FROM chunks")
    c.rollback()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("ALTER TABLE chunks DISABLE ROW LEVEL SECURITY")
    c.rollback()


def test_searcher_refuses_a_connection_pinned_to_another_tenant(app):
    with pytest.raises(PermissionError):
        HybridSearcher(app(TINY), BIG, FakeEmbedder())


@pytest.mark.parametrize("mode", ["dense", "keyword", "hybrid"])
def test_search_through_app_role_stays_in_tenant(app, mode):
    # The query matches big-tenant text best, but tiny-tenant text too: results must all be tiny-tenant docs.
    hits = getattr(HybridSearcher(app(TINY), TINY, FakeEmbedder()), mode)("credential dumping lsass phishing", k=10)
    assert hits and {h.doc_id for h in hits} <= {"T001", "T002", "T003"}


def test_small_tenant_hnsw_starvation_and_fix(app):
    """The query is close to the big tenant's vectors. Forcing the HNSW path with a small ef_search, the index
    returns only big-tenant candidates, RLS/WHERE removes them, and the tiny tenant gets nothing. The exact path
    (default for small tenants) returns all three of its documents."""
    c = app(TINY)
    hnsw = HybridSearcher(c, TINY, FakeEmbedder(), exact_max_rows=0)
    for setting in ("hnsw.ef_search = 10", "enable_seqscan = off", "enable_sort = off", "enable_bitmapscan = off"):
        c.execute(f"SET {setting}")
    if hnsw.iterative:
        pytest.skip("pgvector >= 0.8 iterative scans already avoid the starvation")
    starved = hnsw.dense("credential dumping memory lsass", k=10)
    exact = HybridSearcher(c, TINY, FakeEmbedder()).dense("credential dumping memory lsass", k=10)
    assert len(starved) < 3
    assert {h.doc_id for h in exact} == {"T001", "T002", "T003"}


def test_conversation_log_is_tenant_scoped(app):
    tiny, big = app(TINY), app(BIG)
    tiny.execute("INSERT INTO conversations (tenant_id, conversation_id, question, answer, refused) "
                 "VALUES (%s, 'c1', 'q', 'a', false)", (TINY,))
    tiny.commit()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # RLS: can't write rows for another tenant
        tiny.execute("INSERT INTO conversations (tenant_id, conversation_id, question, answer, refused) "
                     "VALUES (%s, 'c1', 'q', 'a', false)", (BIG,))
    tiny.rollback()
    assert tiny.execute("SELECT count(*) FROM conversations").fetchone()[0] == 1
    assert big.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0
