"""The permission-aware search filter: a user's departments OR company-wide chunks."""

import pytest

from app.core.config import Settings
from app.rag.ingestion.chunkers import SectionChunker
from app.rag.ingestion.embedder import HashingEmbedder
from app.rag.vectorstore import QdrantVectorStore, SearchFilter, collection_name
from app.schemas.chunk import DocumentContext
from app.schemas.extraction import Block, DocumentMetadata, ExtractedDocument, Page

EMBEDDER = HashingEmbedder()
QUESTION = "what are the rules for this document"


def _add(store: QdrantVectorStore, document_id: str, department: str, visibility: str) -> None:
    document = ExtractedDocument(
        pages=[
            Page(
                number=1, blocks=[Block(kind="body", section_title="1. Rules", text="- the rules")]
            )
        ],
        metadata=DocumentMetadata(doc_code=document_id),
    )
    context = DocumentContext(
        document_id=document_id,
        scope="enterprise",
        owner_id="o",
        source_name=f"{document_id}.pdf",
        department=department,
        visibility=visibility,  # type: ignore[arg-type]
    )
    chunks = SectionChunker().chunk(document, context)
    store.upsert(chunks, EMBEDDER.embed([c.embedding_text for c in chunks]), EMBEDDER.model_name)


@pytest.fixture
def store(settings: Settings) -> QdrantVectorStore:
    s = QdrantVectorStore(settings.vector_store_path, collection_name("enterprise"), 256)
    _add(s, "safety", "safety", "company")
    _add(s, "hr", "hr", "company")
    _add(s, "production", "production", "department")
    _add(s, "quality", "quality", "department")
    return s


def _found(store: QdrantVectorStore, search_filter: SearchFilter | None) -> set[str]:
    hits = store.query(EMBEDDER.embed([QUESTION])[0], top_k=20, search_filter=search_filter)
    return {str(h.metadata["document_id"]) for h in hits}


def test_visibility_is_stored_with_every_chunk(store: QdrantVectorStore) -> None:
    hits = store.query(EMBEDDER.embed([QUESTION])[0], top_k=20)
    assert {(h.metadata["document_id"], h.metadata["visibility"]) for h in hits} == {
        ("safety", "company"),
        ("hr", "company"),
        ("production", "department"),
        ("quality", "department"),
    }


def test_own_departments_plus_company_wide(store: QdrantVectorStore) -> None:
    flt = SearchFilter(departments=["production"], include_company_wide=True)
    assert _found(store, flt) == {"production", "safety", "hr"}


def test_other_departments_stay_hidden(store: QdrantVectorStore) -> None:
    found = _found(store, SearchFilter(departments=["production"], include_company_wide=True))
    assert "quality" not in found


def test_without_the_company_wide_flag_only_own_departments(store: QdrantVectorStore) -> None:
    assert _found(store, SearchFilter(departments=["production"])) == {"production"}


def test_a_user_with_no_departments_sees_only_company_wide(store: QdrantVectorStore) -> None:
    flt = SearchFilter(departments=[], include_company_wide=True)
    assert _found(store, flt) == {"safety", "hr"}


def test_no_departments_and_no_company_wide_finds_nothing(store: QdrantVectorStore) -> None:
    assert _found(store, SearchFilter(departments=[], include_company_wide=False)) == set()


def test_narrowing_only_shrinks_never_widens(store: QdrantVectorStore) -> None:
    allowed = dict(departments=["production"], include_company_wide=True)
    assert _found(store, SearchFilter(narrow_department="hr", **allowed)) == {"hr"}
    assert _found(store, SearchFilter(narrow_department="production", **allowed)) == {"production"}
    # quality is not allowed for this user, so asking for it reveals nothing
    assert _found(store, SearchFilter(narrow_department="quality", **allowed)) == set()


def test_an_admin_can_narrow_to_any_department(store: QdrantVectorStore) -> None:
    assert _found(store, SearchFilter(narrow_department="quality")) == {"quality"}
    assert _found(store, None) == {"safety", "hr", "production", "quality"}
