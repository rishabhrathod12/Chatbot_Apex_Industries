"""Hybrid retrieval (meaning + keywords) against the real vector store."""

from collections.abc import Sequence

import pytest

from app.core.config import Settings
from app.rag.ingestion.chunkers import SectionChunker
from app.rag.ingestion.embedder import HashingEmbedder
from app.rag.retrieval import retrieve, semantic_search
from app.rag.vectorstore import (
    QdrantVectorStore,
    SearchFilter,
    SearchHit,
    StoredChunk,
    collection_name,
)
from app.schemas.chunk import DocumentContext
from app.schemas.extraction import Block, DocumentMetadata, ExtractedDocument, Page
from tests.conftest import IndexedSamples

EMBEDDER = HashingEmbedder()


# ---------- the store's two new read-only abilities ----------


def _add(store: QdrantVectorStore, doc: str, dept: str, visibility: str, text: str) -> None:
    document = ExtractedDocument(
        pages=[Page(number=1, blocks=[Block(kind="body", section_title="1. Rules", text=text)])],
        metadata=DocumentMetadata(doc_code=doc, title=f"Title {doc}"),
    )
    context = DocumentContext(
        document_id=doc,
        scope="enterprise",
        owner_id="o",
        source_name=f"{doc}.pdf",
        department=dept,
        visibility=visibility,  # type: ignore[arg-type]
    )
    chunks = SectionChunker().chunk(document, context)
    store.upsert(chunks, EMBEDDER.embed([c.embedding_text for c in chunks]), EMBEDDER.model_name)


@pytest.fixture
def store(settings: Settings) -> QdrantVectorStore:
    s = QdrantVectorStore(settings.vector_store_path, collection_name("enterprise"), 256)
    _add(s, "safety", "safety", "company", "- light curtains must never be bypassed")
    _add(s, "hr", "hr", "company", "- submit leave requests through the HR system")
    _add(
        s,
        "production",
        "production",
        "department",
        "- keep doors closed during automatic operation",
    )
    _add(s, "quality", "quality", "department", "- the concession must be approved by Quality")
    return s


def _ids(chunks: Sequence[StoredChunk | SearchHit]) -> set[str]:
    return {str(c.metadata["document_id"]) for c in chunks}


def test_scan_lists_exactly_the_permitted_chunks(store: QdrantVectorStore) -> None:
    assert _ids(store.scan(None, 100)) == {"safety", "hr", "production", "quality"}
    mine = SearchFilter(departments=["production"], include_company_wide=True)
    assert _ids(store.scan(mine, 100)) == {"production", "safety", "hr"}
    assert store.scan(SearchFilter(departments=[]), 100) == []  # fails closed, like search
    assert store.scan(None, 0) == []


def test_scan_returns_the_text_without_leaking_it_into_the_metadata(
    store: QdrantVectorStore,
) -> None:
    chunk = store.scan(SearchFilter(document_ids=["safety"]), 10)[0]
    assert "light curtains" in chunk.text
    assert "text" not in chunk.metadata


def test_scan_respects_its_limit(store: QdrantVectorStore) -> None:
    assert len(store.scan(None, 2)) == 2


def test_vectors_for_returns_the_stored_vectors(store: QdrantVectorStore) -> None:
    chunk = store.scan(SearchFilter(document_ids=["hr"]), 10)[0]
    vectors = store.vectors_for([chunk.chunk_id, "does-not-exist"])
    assert set(vectors) == {chunk.chunk_id}
    assert len(vectors[chunk.chunk_id]) == 256
    assert store.vectors_for([]) == {}


# ---------- hybrid retrieval ----------


def _retrieve(store: QdrantVectorStore, query: str, **kw: object) -> list[SearchHit]:
    return retrieve(EMBEDDER, store, query, top_k=10, **kw)  # type: ignore[arg-type]


def test_semantic_mode_is_the_plain_vector_search(store: QdrantVectorStore) -> None:
    plain = semantic_search(EMBEDDER, store, "light curtains bypassed", top_k=10)
    via_retrieve = _retrieve(store, "light curtains bypassed", mode="semantic")
    assert [h.chunk_id for h in plain] == [h.chunk_id for h in via_retrieve]


def test_hybrid_finds_a_chunk_that_vector_search_missed(store: QdrantVectorStore) -> None:
    """With room for only one meaning-based candidate, the keyword match still gets in."""
    hits = _retrieve(store, "concession approved", mode="hybrid", candidates=1)
    assert "quality" in _ids(hits)


def test_every_hit_carries_a_meaning_similarity_even_if_only_keywords_found_it(
    store: QdrantVectorStore,
) -> None:
    hits = _retrieve(store, "concession approved Quality", mode="hybrid", candidates=1)
    quality = next(h for h in hits if h.metadata["document_id"] == "quality")
    assert -1.0 <= quality.score <= 1.0
    assert quality.score != 0.0


def test_hybrid_results_are_ordered_and_deterministic(store: QdrantVectorStore) -> None:
    first = [h.chunk_id for h in _retrieve(store, "doors closed operation", mode="hybrid")]
    second = [h.chunk_id for h in _retrieve(store, "doors closed operation", mode="hybrid")]
    assert first == second
    assert first[0].startswith("production")


def test_hybrid_respects_top_k(store: QdrantVectorStore) -> None:
    assert len(retrieve(EMBEDDER, store, "rules", top_k=2, mode="hybrid")) == 2


def test_hybrid_only_ever_returns_permitted_chunks(store: QdrantVectorStore) -> None:
    mine = SearchFilter(departments=["production"], include_company_wide=True)
    for query in (
        "concession approved Quality",
        "light curtains",
        "leave requests",
        "doors closed",
    ):
        found = _ids(_retrieve(store, query, mode="hybrid", search_filter=mine))
        assert "quality" not in found, query
        assert found <= {"production", "safety", "hr"}


def test_hybrid_respects_a_filter_that_allows_nothing(store: QdrantVectorStore) -> None:
    nothing = SearchFilter(departments=[], include_company_wide=False)
    assert _retrieve(store, "light curtains", mode="hybrid", search_filter=nothing) == []


def test_a_forbidden_keyword_match_is_invisible_even_to_exact_word_search(
    store: QdrantVectorStore,
) -> None:
    """'concession' appears only in the Quality document. Someone who cannot read it must get
    nothing for it, however exact the word."""
    stores_user = SearchFilter(departments=["stores"], include_company_wide=True)
    found = _ids(_retrieve(store, "concession", mode="hybrid", search_filter=stores_user))
    assert "quality" not in found


def test_hybrid_works_on_the_real_sample_documents(indexed_samples: IndexedSamples) -> None:
    hits = retrieve(
        indexed_samples.embedder,
        indexed_samples.store,
        "What is APM-QLT-SOP-006 about?",
        top_k=3,
        mode="hybrid",
    )
    assert hits[0].metadata["doc_code"] == "APM-QLT-SOP-006"
    assert hits[0].metadata["kind"] == "title_block"
