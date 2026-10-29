"""The one document model every corpus is converted into before indexing.

Retrieval, evaluation and guardrails only ever see `Document`. A corpus adapter's whole job
is to turn its raw data into these records; anything specific to that corpus goes in `metadata`.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def content_hash(text: str) -> str:
    """Short SHA-256 of the text. On re-ingest, only documents whose hash changed get re-embedded."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class Document(BaseModel):
    # extra="forbid": a field the schema doesn't know is a bug, not data. It belongs in `metadata`.
    # frozen: documents are values; build a new one instead of mutating.
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    doc_id: str = Field(min_length=1, description="Stable ID, unique within a tenant. Never changes on re-ingest.")
    tenant_id: str = Field(min_length=1, description="Owner of the document. Every query is filtered by it.")
    source: str = Field(min_length=1, description="Where the document came from, e.g. a dataset name.")
    type: str = Field(min_length=1, description="Document kind, defined by the corpus adapter.")
    title: str = Field(min_length=1)
    text: str = Field(min_length=1, description="What gets chunked and embedded.")
    url: str | None = Field(default=None, description="Link shown in citations.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Corpus-specific fields, used for filters.")
    content_hash: str = Field(pattern=r"^[0-9a-f]{16}$")

    @model_validator(mode="after")
    def _hash_matches_text(self) -> Document:
        if self.content_hash != content_hash(self.text):
            raise ValueError("content_hash does not match text")
        return self

    @classmethod
    def build(cls, **fields: Any) -> Document:
        """Create a document and compute its content_hash, so callers can't get it wrong."""
        return cls(**fields, content_hash=content_hash(fields["text"].strip()))
