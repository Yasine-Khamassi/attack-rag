"""
Web API + a one-page UI for the ATT&CK analyst assistant.

  GET  /            the page
  POST /api/ask     {"question": "...", "conversation_id": "..."} -> answer, sources (attack.mitre.org links),
                    checks, timing + cost; the turn is written to the tenant's conversation log
  GET  /api/conversations/{id}   that conversation's turns (tenant-scoped by row-level security)
  GET  /api/usage   turns, conversations, answered, API cost for this tenant
  GET  /api/health

Run:  uvicorn products.attack.api:app --port 8000      (needs `ollama serve` and ANTHROPIC_API_KEY in .env)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from core import convlog
from core.llm import BudgetExceeded
from products.attack.assistant import Assistant, to_json

PAGE = Path(__file__).with_name("static") / "index.html"
app = FastAPI(title="ATT&CK Analyst Assistant")


class Ask(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    conversation_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


@lru_cache(maxsize=1)
def assistant() -> Assistant:
    return Assistant()  # created on first request: loads the index and connects to the database


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE.read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/ask")
def ask(body: Ask) -> dict:
    try:
        return to_json(*assistant().ask(body.question.strip(), body.conversation_id))
    except BudgetExceeded as e:
        raise HTTPException(status_code=503, detail=f"API budget reached: {e}") from e


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str) -> dict:
    a = assistant()
    with a.lock:
        return {"conversation_id": conversation_id, "turns": convlog.history(a.conn, conversation_id)}


@app.get("/api/usage")
def usage() -> dict:
    a = assistant()
    with a.lock:
        return convlog.usage(a.conn)
