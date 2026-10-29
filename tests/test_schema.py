import pytest
from pydantic import ValidationError

from core.schema import Document, content_hash

FIELDS = dict(doc_id="d1", tenant_id="t1", source="s", type="note", title="A title", text="Some text")


def test_build_computes_hash():
    doc = Document.build(**FIELDS)
    assert doc.content_hash == content_hash("Some text")
    assert doc.metadata == {} and doc.url is None


def test_wrong_hash_is_rejected():
    with pytest.raises(ValidationError, match="content_hash"):
        Document(**FIELDS, content_hash="0" * 16)


@pytest.mark.parametrize("field", ["doc_id", "tenant_id", "source", "type", "title", "text"])
def test_required_fields_cannot_be_blank(field):
    with pytest.raises(ValidationError):
        Document.build(**{**FIELDS, field: "   "})


def test_tenant_id_is_required():
    fields = {k: v for k, v in FIELDS.items() if k != "tenant_id"}
    with pytest.raises(ValidationError, match="tenant_id"):
        Document.build(**fields)


def test_unknown_fields_are_rejected():
    # Corpus-specific fields must go in metadata, not at the top level.
    with pytest.raises(ValidationError, match="platforms"):
        Document.build(**FIELDS, platforms=["Windows"])


def test_documents_are_immutable():
    doc = Document.build(**FIELDS)
    with pytest.raises(ValidationError):
        doc.text = "changed"


def test_json_round_trip():
    doc = Document.build(**FIELDS, url="https://example.com", metadata={"k": [1, 2]})
    assert Document.model_validate_json(doc.model_dump_json()) == doc
