"""Per-conversation log: every turn (question, answer, sources, guard events, timing, cost) is stored in the
`conversations` table, tenant-scoped by row-level security (DECISIONS D31). Used for support review, billing
(AI conversations per tenant) and evaluation of real traffic."""

from __future__ import annotations

import json

import psycopg


def log_turn(conn: psycopg.Connection, tenant_id: str, conversation_id: str, record: dict) -> int:
    """`record` = the API's answer JSON (question, answer, refused, sources, checks, timing) + optional cost_usd."""
    row = conn.execute(
        """INSERT INTO conversations (tenant_id, conversation_id, question, answer, refused, sources, checks,
                                      timing, cost_usd)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (tenant_id, conversation_id, record["question"], record["answer"], record["refused"],
         json.dumps(record.get("sources", []), ensure_ascii=False), json.dumps(record.get("checks", {}), ensure_ascii=False),
         json.dumps(record.get("timing", {})), float(record.get("cost_usd", 0.0)))).fetchone()
    conn.commit()
    return row[0]


def history(conn: psycopg.Connection, conversation_id: str, limit: int = 20) -> list[dict]:
    rows = conn.execute("""SELECT ts, question, answer, refused, cost_usd FROM conversations
                           WHERE conversation_id = %s ORDER BY ts DESC LIMIT %s""", (conversation_id, limit)).fetchall()
    return [{"ts": r[0].isoformat(), "question": r[1], "answer": r[2], "refused": r[3], "cost_usd": r[4]}
            for r in reversed(rows)]


def usage(conn: psycopg.Connection) -> dict:
    """What the current tenant used: turns, conversations, answered vs refused, API cost."""
    r = conn.execute("""SELECT count(*), count(DISTINCT conversation_id), count(*) FILTER (WHERE NOT refused),
                               coalesce(sum(cost_usd), 0) FROM conversations""").fetchone()
    return {"turns": r[0], "conversations": r[1], "answered": r[2], "cost_usd": round(r[3], 6)}
