"""Index the sample PDFs straight into a vector store, without the database (for eval and tests)."""

from pathlib import Path

from app.rag.ingestion.chunkers import SectionChunker
from app.rag.ingestion.embedder import Embedder
from app.rag.ingestion.extractors import extract
from app.rag.vectorstore import QdrantVectorStore, collection_name
from app.schemas.chunk import DocumentContext


def index_sample_documents(
    store_path: str, embedder: Embedder, docs_dir: Path
) -> QdrantVectorStore:
    store = QdrantVectorStore(store_path, collection_name("enterprise"), embedder.dimensions)
    chunker = SectionChunker()
    for pdf in sorted(docs_dir.glob("*.pdf")):
        document = extract(pdf, ".pdf")
        context = DocumentContext(
            document_id=pdf.stem,
            scope="enterprise",
            owner_id="eval",
            source_name=pdf.name,
            department=document.metadata.department or "",
        )
        chunks = chunker.chunk(document, context)
        vectors = embedder.embed([c.embedding_text for c in chunks])
        store.upsert(chunks, vectors, embedder.model_name)
    return store
