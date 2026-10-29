from core.bm25 import BM25
from core.chunking import chunk_document
from core.schema import Document
from core.text import normalize, tokenize


def test_identifiers_stay_whole_and_add_base():
    assert tokenize("Detect T1003.001 now") == ["detect", "t1003.001", "t1003", "now"]
    assert "cve-2024-3400" in tokenize("Patch CVE-2024-3400")


def test_arabic_normalisation():
    # Same word with hamza variants, diacritics and tatweel normalises to one form.
    assert normalize("أَمْـــن") == normalize("امن") == "امن"
    assert normalize("مكتبة") == "مكتبه" and normalize("على") == "علي"
    assert normalize("٢٠٢٤") == "2024"


def test_arabic_article_and_clitics_removed():
    assert tokenize("الهجوم وبالهجوم للهجوم") == ["هجوم", "هجوم", "هجوم"]
    assert tokenize("ال") == ["ال"]  # too short to strip


def test_mixed_arabic_english_query():
    assert tokenize("ما هي التقنية T1003.001؟") == ["ما", "هي", "تقنيه", "t1003.001", "t1003"]


def doc(text, doc_id="X1"):
    return Document.build(doc_id=doc_id, tenant_id="t", source="s", type="k", title="T", text=text)


def test_short_document_is_one_unchanged_chunk():
    d = doc("X1: Name\nType: k\n\nShort body.")
    [c] = chunk_document(d)
    assert c.text == d.text and c.content_hash == d.content_hash and c.chunk_id == "X1#0"


def test_long_document_chunks_repeat_header_and_respect_limit():
    header = "X1: Name\nType: k"
    body = "\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(10))
    chunks = chunk_document(doc(f"{header}\n\n{body}"), max_chars=500)
    assert len(chunks) > 1
    assert all(c.text.startswith(header + "\n\n") and len(c.text) <= 500 for c in chunks)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    # No text lost: every paragraph appears in some chunk.
    joined = "\n".join(c.text for c in chunks)
    assert all(f"Paragraph {i}." in joined for i in range(10))


def test_bm25_prefers_rare_exact_identifier():
    index = BM25(["a", "b", "c"], [
        "credential dumping from memory T1003.001",
        "credential access overview T1003",
        "phishing with attachments",
    ])
    assert index.search("T1003.001")[0][0] == "a"
    assert {i for i, _ in index.search("T1003")} == {"a", "b"}  # base ID matches sub-ID docs too
    assert index.search("nothing matches") == []
