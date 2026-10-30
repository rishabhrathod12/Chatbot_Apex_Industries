from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.rag.ingestion.embedder import Embedder, EmbeddingError, HashingEmbedder
from app.rag.ingestion.pipeline import Scope, ingest_document
from app.rag.vectorstore import QdrantVectorStore, VectorStore, collection_name
from app.repositories import documents as repo
from tests.conftest import SAMPLE_DOCS, add_stored_document

SAFETY_PDF = SAMPLE_DOCS / "03_Factory_Safety_Manual.pdf"


def _store_provider(settings: Settings) -> Any:
    def provider(scope: Scope, dimensions: int) -> VectorStore:
        return QdrantVectorStore(settings.vector_store_path, collection_name(scope), dimensions)

    return provider


def _ingest(
    db: Session, settings: Settings, document_id: str, embedder: Embedder | None = None, **kw: Any
) -> None:
    ingest_document(
        db,
        document_id,
        settings=settings,
        embedder_provider=lambda: embedder or HashingEmbedder(),
        store_provider=kw.get("store_provider") or _store_provider(settings),
    )


def _count(settings: Settings, document_id: str | None = None) -> int:
    return _store_provider(settings)("enterprise", 256).count(document_id)


def test_ingesting_a_pdf_marks_it_ready_and_indexes_its_chunks(
    session: Session, settings: Settings
) -> None:
    doc = add_stored_document(session, settings, SAFETY_PDF, department="safety")
    _ingest(session, settings, doc.id)

    assert doc.status == "ready"
    assert doc.status_reason is None
    assert doc.chunk_count == 9
    assert doc.page_count == 3
    assert doc.doc_code == "APM-HSE-MAN-003"
    assert doc.title == "Factory Health & Safety Manual"
    assert doc.doc_version == "1.0"
    assert _count(settings, doc.id) == 9


def test_ingesting_twice_does_not_duplicate_vectors(session: Session, settings: Settings) -> None:
    doc = add_stored_document(session, settings, SAFETY_PDF, department="safety")
    _ingest(session, settings, doc.id)
    _ingest(session, settings, doc.id)
    assert _count(settings, doc.id) == 9
    assert doc.status == "ready"


def test_reindexing_removes_chunks_that_no_longer_exist(
    session: Session, settings: Settings
) -> None:
    doc = add_stored_document(session, settings, SAFETY_PDF, department="safety")
    _ingest(session, settings, doc.id)

    # Simulate an older, longer version of this document still sitting in the store.
    from app.rag.ingestion.chunkers import SectionChunker
    from app.schemas.chunk import DocumentContext
    from app.schemas.extraction import Block, DocumentMetadata, ExtractedDocument, Page

    stale = SectionChunker().chunk(
        ExtractedDocument(
            pages=[
                Page(number=9, blocks=[Block(kind="body", section_title="99. Old", text="old")])
            ],
            metadata=DocumentMetadata(version="0.9"),
        ),
        DocumentContext(document_id=doc.id, scope="enterprise", owner_id="x", source_name="o.pdf"),
    )
    embedder = HashingEmbedder()
    _store_provider(settings)("enterprise", 256).upsert(
        stale, embedder.embed([c.embedding_text for c in stale]), embedder.model_name
    )
    assert _count(settings, doc.id) == 10

    _ingest(session, settings, doc.id)
    assert _count(settings, doc.id) == 9


def test_embedding_failure_leaves_nothing_indexed(session: Session, settings: Settings) -> None:
    class Broken:
        model_name = "broken"
        dimensions = 256

        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            raise EmbeddingError("embedding_request_failed")

    doc = add_stored_document(session, settings, SAFETY_PDF, department="safety")
    _ingest(session, settings, doc.id, embedder=Broken())
    assert doc.status == "failed"
    assert doc.status_reason == "embedding_request_failed"
    assert doc.chunk_count == 0
    assert _count(settings) == 0


def test_missing_api_key_is_recorded_on_the_document(session: Session, settings: Settings) -> None:
    doc = add_stored_document(session, settings, SAFETY_PDF, department="safety")

    def no_key() -> Embedder:
        raise EmbeddingError("embedding_not_configured")

    ingest_document(
        session,
        doc.id,
        settings=settings,
        embedder_provider=no_key,
        store_provider=_store_provider(settings),
    )
    assert (doc.status, doc.status_reason) == ("failed", "embedding_not_configured")


def test_extraction_failure_is_recorded(session: Session, settings: Settings) -> None:
    doc = add_stored_document(session, settings, b"%PDF-1.7 not really", department="hr")
    _ingest(session, settings, doc.id)
    assert (doc.status, doc.status_reason) == ("failed", "corrupt_pdf")


def test_document_with_no_indexable_text_fails_clearly(
    session: Session, settings: Settings
) -> None:
    doc = add_stored_document(session, settings, b"   \n\n  ", ext=".txt", department="hr")
    _ingest(session, settings, doc.id)
    assert (doc.status, doc.status_reason) == ("failed", "empty_document")


def test_failure_after_writing_removes_the_documents_vectors(
    session: Session, settings: Settings
) -> None:
    class FailsAfterUpsert:
        def __init__(self, inner: VectorStore) -> None:
            self._inner = inner

        def upsert(self, *args: Any, **kwargs: Any) -> None:
            self._inner.upsert(*args, **kwargs)

        def delete_stale(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("disk full")

        def delete_document(self, document_id: str) -> None:
            self._inner.delete_document(document_id)

    real = _store_provider(settings)
    doc = add_stored_document(session, settings, SAFETY_PDF, department="safety")
    _ingest(
        session,
        settings,
        doc.id,
        store_provider=lambda scope, dims: FailsAfterUpsert(real(scope, dims)),
    )
    assert (doc.status, doc.status_reason) == ("failed", "internal_error")
    assert _count(settings, doc.id) == 0  # not half-indexed


def test_unknown_document_id_is_ignored(session: Session, settings: Settings) -> None:
    _ingest(session, settings, "does-not-exist")
    assert repo.list_all(session) == []


def test_personal_documents_go_to_the_personal_collection(
    session: Session, settings: Settings
) -> None:
    doc = add_stored_document(
        session, settings, SAFETY_PDF, scope="personal", department="", owner_id="alice"
    )
    _ingest(session, settings, doc.id)
    store = _store_provider(settings)
    assert store("personal", 256).count(doc.id) == 9
    assert store("enterprise", 256).count(doc.id) == 0


def test_txt_documents_are_indexed_without_pages(session: Session, settings: Settings) -> None:
    text = b"1. Scope\nWear safety boots.\n\n2. Records\nKeep the log."
    doc = add_stored_document(session, settings, text, ext=".txt", department="safety")
    _ingest(session, settings, doc.id)
    assert (doc.status, doc.chunk_count, doc.page_count) == ("ready", 2, None)
