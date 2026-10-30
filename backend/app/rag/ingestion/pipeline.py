import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_session_factory
from app.models.document import Document
from app.rag.ingestion.chunkers import SectionChunker
from app.rag.ingestion.embedder import Embedder, EmbeddingError
from app.rag.ingestion.extractors import ExtractionError, extract
from app.rag.vectorstore import VectorStore, VectorStoreError
from app.repositories import documents as repo
from app.schemas.chunk import DocumentContext

logger = logging.getLogger("app.ingestion")

Scope = Literal["personal", "enterprise"]
EmbedderProvider = Callable[[], Embedder]
StoreProvider = Callable[[Scope, int], VectorStore]


def _visibility(document: Document) -> Literal["personal", "department", "company"]:
    if document.scope == "personal":
        return "personal"
    return "company" if document.visibility == "company" else "department"


def _fail(db: Session, document: Document, reason: str) -> None:
    document.status = "failed"
    document.status_reason = reason
    document.chunk_count = 0
    db.commit()
    logger.warning("ingestion failed", extra={"document_id": document.id, "reason": reason})


def ingest_document(
    db: Session,
    document_id: str,
    *,
    settings: Settings,
    embedder_provider: EmbedderProvider,
    store_provider: StoreProvider,
) -> None:
    """Extract, chunk, embed and index one stored document, and record the outcome on its row.

    Embeddings are computed before anything is written to the vector store, so an embedding
    failure leaves the store untouched. Any failure after writing starts removes the document's
    vectors again, so a document is either fully indexed or not searchable at all.
    """
    document = repo.get(db, document_id)
    if document is None:
        return

    started = time.perf_counter()
    document.status = "processing"
    document.status_reason = None
    db.commit()

    scope: Scope = "enterprise" if document.scope == "enterprise" else "personal"
    store: VectorStore | None = None
    try:
        path = Path(settings.upload_dir) / document.stored_filename
        extracted = extract(path, document.file_ext, max_pages=settings.max_pdf_pages)

        context = DocumentContext(
            document_id=document.id,
            scope=scope,
            owner_id=document.owner_id,
            source_name=document.original_filename,
            department=document.department,
            visibility=_visibility(document),
        )
        chunker = SectionChunker(settings.chunk_max_tokens, settings.chunk_overlap_tokens)
        chunks = chunker.chunk(extracted, context)
        if not chunks:
            _fail(db, document, "no_indexable_content")
            return

        embedder = embedder_provider()
        vectors = embedder.embed([chunk.embedding_text for chunk in chunks])

        store = store_provider(scope, embedder.dimensions)
        store.upsert(chunks, vectors, embedder.model_name)
        store.delete_stale(document.id, [chunk.chunk_id for chunk in chunks])

        document.status = "ready"
        document.status_reason = None
        document.chunk_count = len(chunks)
        document.page_count = len(extracted.pages) if document.file_ext == ".pdf" else None
        document.doc_code = extracted.metadata.doc_code
        document.title = extracted.metadata.title
        document.doc_version = chunks[0].doc_version
        db.commit()
        logger.info(
            "ingestion finished",
            extra={
                "document_id": document.id,
                "chunk_count": len(chunks),
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
    except (ExtractionError, EmbeddingError, VectorStoreError) as exc:
        _cleanup(store, document.id)
        _fail(db, document, exc.reason)
    except Exception:
        logger.exception("unexpected ingestion error", extra={"document_id": document.id})
        _cleanup(store, document.id)
        _fail(db, document, "internal_error")


def _cleanup(store: VectorStore | None, document_id: str) -> None:
    if store is None:
        return
    try:
        store.delete_document(document_id)
    except Exception:
        logger.exception("cleanup of vectors failed", extra={"document_id": document_id})


def run_ingestion(
    document_id: str,
    settings: Settings,
    embedder_provider: EmbedderProvider,
    store_provider: StoreProvider,
) -> None:
    """Entry point for background tasks: runs one ingestion in its own database session."""
    with get_session_factory(settings.database_url)() as db:
        ingest_document(
            db,
            document_id,
            settings=settings,
            embedder_provider=embedder_provider,
            store_provider=store_provider,
        )
