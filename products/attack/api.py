"""
Web API + a one-page UI for the ATT&CK analyst assistant.

  GET  /            the page
  POST /api/ask     {"question": "..."} -> answer, sources (with attack.mitre.org links), checks, timing
  GET  /api/health

Run:  uvicorn products.attack.api:app --port 8000      (needs `ollama serve` and ANTHROPIC_API_KEY in .env)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from core.llm import BudgetExceeded
from products.attack.assistant import Assistant, to_json

PAGE = Path(__file__).with_name("static") / "index.html"
app = FastAPI(title="ATT&CK Analyst Assistant")


class Ask(BaseModel):
    question: str = Field(min_length=2, max_length=500)


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
        return to_json(*assistant().ask(body.question.strip()))
    except BudgetExceeded as e:
        raise HTTPException(status_code=503, detail=f"API budget reached: {e}") from e
