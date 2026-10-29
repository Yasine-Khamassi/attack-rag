"""Split documents into chunks that are small enough to embed well and to cite precisely.

Each document's text starts with a short header block (ID, name, type ...) followed by a blank line.
Every chunk repeats that header, so a chunk from the middle of a long document still says what it belongs
to, both for the embedding and for keyword search on the ID.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.schema import Document, content_hash

MAX_CHARS = 1500
MAX_HEADER_CHARS = 400
_SENTENCE_END = re.compile(r"(?<=[.!?؟])\s+")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str          # "<doc_id>#<n>", n starts at 0
    doc_id: str
    tenant_id: str
    ordinal: int
    text: str
    content_hash: str
    type: str
    title: str
    url: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


def _split_long(paragraph: str, limit: int) -> list[str]:
    """A paragraph longer than the limit is split at sentence ends (or hard-cut if one sentence is too long)."""
    pieces, current = [], ""
    for sentence in _SENTENCE_END.split(paragraph):
        while len(sentence) > limit:
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        if current and len(current) + 1 + len(sentence) > limit:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def chunk_document(doc: Document, max_chars: int = MAX_CHARS) -> list[Chunk]:
    header, sep, body = doc.text.partition("\n\n")
    if not sep or len(header) > MAX_HEADER_CHARS:
        header, body = "", doc.text

    if len(doc.text) <= max_chars:
        bodies = [None]  # whole document as one chunk, text unchanged
    else:
        limit = max_chars - len(header) - 2
        paragraphs = [p for part in body.split("\n") if (p := part.strip())]
        bodies, current = [], ""
        for para in (piece for p in paragraphs for piece in _split_long(p, limit)):
            if current and len(current) + 1 + len(para) > limit:
                bodies.append(current)
                current = para
            else:
                current = f"{current}\n{para}" if current else para
        if current:
            bodies.append(current)

    chunks = []
    for n, part in enumerate(bodies):
        text = doc.text if part is None else (f"{header}\n\n{part}" if header else part)
        chunks.append(Chunk(
            chunk_id=f"{doc.doc_id}#{n}", doc_id=doc.doc_id, tenant_id=doc.tenant_id, ordinal=n,
            text=text, content_hash=content_hash(text), type=doc.type, title=doc.title, url=doc.url,
            metadata=doc.metadata,
        ))
    return chunks
