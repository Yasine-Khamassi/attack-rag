"""Shared RAG core: document schema, retrieval, evaluation and guardrails.

Nothing in this package may know which corpus it is serving. Corpus-specific code
lives outside `core/` and talks to it only through the types defined here.
"""

from core.schema import Document, content_hash

__all__ = ["Document", "content_hash"]
__version__ = "0.1.0"
