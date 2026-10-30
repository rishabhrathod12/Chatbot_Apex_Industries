from fastapi import Depends

from app.core.config import Settings, get_settings
from app.rag.ingestion.embedder import Embedder, get_embedder
from app.rag.ingestion.pipeline import EmbedderProvider, Scope, StoreProvider
from app.rag.vectorstore import QdrantVectorStore, VectorStore, collection_name


def get_embedder_provider(settings: Settings = Depends(get_settings)) -> EmbedderProvider:
    """A callable rather than an embedder, so a missing API key fails the *ingestion* (recorded on
    the document) and does not break the upload request itself."""

    def provider() -> Embedder:
        return get_embedder(settings)

    return provider


def get_store_provider(settings: Settings = Depends(get_settings)) -> StoreProvider:
    def provider(scope: Scope, dimensions: int) -> VectorStore:
        return QdrantVectorStore(settings.vector_store_path, collection_name(scope), dimensions)

    return provider
