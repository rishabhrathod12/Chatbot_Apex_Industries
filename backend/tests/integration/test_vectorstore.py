import pytest

from app.core.config import Settings
from app.rag.ingestion.chunkers import SectionChunker
from app.rag.ingestion.embedder import HashingEmbedder
from app.rag.vectorstore import (
    QdrantVectorStore,
    SearchFilter,
    VectorStoreError,
    close_all,
    collection_name,
)
from app.schemas.chunk import Chunk, DocumentContext
from app.schemas.extraction import Block, DocumentMetadata, ExtractedDocument, Page

EMBEDDER = HashingEmbedder()


def _chunks(
    document_id: str,
    department: str,
    texts: list[str],
    scope: str = "enterprise",
    owner: str = "u1",
) -> list[Chunk]:
    blocks = [
        Block(kind="body", section_title=f"{i}. Section {i}", text=t) for i, t in enumerate(texts)
    ]
    document = ExtractedDocument(
        pages=[Page(number=2, blocks=blocks)],
        metadata=DocumentMetadata(doc_code=f"CODE-{document_id}", title=f"Title {document_id}"),
    )
    context = DocumentContext(
        document_id=document_id,
        scope=scope,  # type: ignore[arg-type]
        owner_id=owner,
        source_name=f"{document_id}.pdf",
        department=department,
    )
    return SectionChunker().chunk(document, context)


def _store(settings: Settings, scope: str = "enterprise", dims: int = 256) -> QdrantVectorStore:
    return QdrantVectorStore(settings.vector_store_path, collection_name(scope), dims)  # type: ignore[arg-type]


def _add(store: QdrantVectorStore, chunks: list[Chunk]) -> None:
    store.upsert(chunks, EMBEDDER.embed([c.embedding_text for c in chunks]), EMBEDDER.model_name)


def _ask(store: QdrantVectorStore, question: str, **kw: object):  # noqa: ANN202
    return store.query(EMBEDDER.embed([question])[0], top_k=kw.pop("top_k", 5), **kw)  # type: ignore[arg-type]


@pytest.fixture
def populated(settings: Settings) -> QdrantVectorStore:
    store = _store(settings)
    _add(store, _chunks("hr1", "hr", ["- submit a leave request through the HR system"]))
    _add(store, _chunks("saf1", "safety", ["- never bypass guards or emergency stop devices"]))
    _add(store, _chunks("prod1", "production", ["- keep doors closed during automatic operation"]))
    return store


def test_query_returns_closest_chunk_with_text_and_metadata(populated: QdrantVectorStore) -> None:
    hits = _ask(populated, "emergency stop devices bypass guards")
    top = hits[0]
    assert top.metadata["document_id"] == "saf1"
    assert top.metadata["department"] == "safety"
    assert top.metadata["doc_code"] == "CODE-saf1"
    assert top.metadata["page_start"] == 2
    assert "emergency stop" in top.text
    assert "text" not in top.metadata
    assert hits[0].score >= hits[-1].score


def test_department_filter_only_returns_allowed_departments(populated: QdrantVectorStore) -> None:
    hits = _ask(populated, "leave request", search_filter=SearchFilter(departments=["safety"]))
    assert {h.metadata["department"] for h in hits} == {"safety"}

    both = _ask(
        populated, "leave request", search_filter=SearchFilter(departments=["hr", "safety"])
    )
    assert {h.metadata["department"] for h in both} == {"hr", "safety"}


def test_empty_department_list_returns_nothing_not_everything(populated: QdrantVectorStore) -> None:
    assert _ask(populated, "leave request", search_filter=SearchFilter(departments=[])) == []
    assert _ask(populated, "leave request", search_filter=SearchFilter(document_ids=[])) == []


def test_no_filter_searches_everything(populated: QdrantVectorStore) -> None:
    assert len(_ask(populated, "leave request")) == 3


def test_document_filter_and_combination(populated: QdrantVectorStore) -> None:
    only = _ask(populated, "anything", search_filter=SearchFilter(document_ids=["prod1"]))
    assert [h.metadata["document_id"] for h in only] == ["prod1"]
    none = _ask(
        populated,
        "anything",
        search_filter=SearchFilter(departments=["hr"], document_ids=["prod1"]),
    )
    assert none == []


def test_owner_filter_isolates_personal_documents(settings: Settings) -> None:
    store = _store(settings, "personal")
    _add(store, _chunks("mine", "", ["- my private note about budgets"], "personal", "alice"))
    _add(store, _chunks("theirs", "", ["- their private note about budgets"], "personal", "bob"))
    hits = _ask(store, "private note budgets", search_filter=SearchFilter(owner_id="alice"))
    assert {h.metadata["document_id"] for h in hits} == {"mine"}


def test_personal_and_enterprise_collections_are_separate(settings: Settings) -> None:
    enterprise = _store(settings, "enterprise")
    personal = _store(settings, "personal")
    _add(enterprise, _chunks("e1", "hr", ["- company wide policy"]))
    assert enterprise.count() == 1
    assert personal.count() == 0
    assert _ask(personal, "company policy") == []


def test_upsert_is_idempotent(settings: Settings) -> None:
    store = _store(settings)
    chunks = _chunks("d1", "hr", ["- one", "- two"])
    _add(store, chunks)
    _add(store, chunks)
    assert store.count() == 2
    assert store.count("d1") == 2


def test_delete_document_removes_only_that_document(populated: QdrantVectorStore) -> None:
    populated.delete_document("hr1")
    assert populated.count("hr1") == 0
    assert populated.count() == 2
    assert all(h.metadata["document_id"] != "hr1" for h in _ask(populated, "leave request"))


def test_delete_stale_keeps_listed_chunks(settings: Settings) -> None:
    store = _store(settings)
    chunks = _chunks("d1", "hr", ["- one", "- two", "- three"])
    _add(store, chunks)
    _add(store, _chunks("other", "hr", ["- unrelated"]))
    store.delete_stale("d1", [chunks[0].chunk_id])
    assert store.count("d1") == 1
    assert store.count("other") == 1  # other documents are never touched
    store.delete_stale("d1", [])
    assert store.count("d1") == 0


def test_data_survives_closing_and_reopening(settings: Settings) -> None:
    _add(_store(settings), _chunks("d1", "hr", ["- persisted"]))
    close_all()
    assert _store(settings).count() == 1


def test_changing_embedding_size_is_refused(settings: Settings) -> None:
    _store(settings, dims=256)
    with pytest.raises(VectorStoreError) as exc:
        _store(settings, dims=1536)
    assert exc.value.reason == "embedding_dimension_mismatch"


def test_bad_upserts_are_rejected(settings: Settings) -> None:
    store = _store(settings)
    chunks = _chunks("d1", "hr", ["- one", "- two"])
    with pytest.raises(VectorStoreError) as count_error:
        store.upsert(chunks, [[0.0] * 256], "m")
    assert count_error.value.reason == "chunk_embedding_count_mismatch"
    with pytest.raises(VectorStoreError) as size_error:
        store.upsert(chunks[:1], [[0.0] * 8], "m")
    assert size_error.value.reason == "embedding_dimension_mismatch"
    assert store.count() == 0


def test_non_positive_top_k_returns_nothing(populated: QdrantVectorStore) -> None:
    assert _ask(populated, "leave", top_k=0) == []
