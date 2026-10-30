from collections.abc import Callable

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.core.dev_user import DEV_USER_ID
from app.rag.generation import ChatModel, get_chat_model
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


ChatModelProvider = Callable[[], ChatModel]


def get_chat_model_provider(settings: Settings = Depends(get_settings)) -> ChatModelProvider:
    """Callable for the same reason as the embedder: a missing key becomes a clean 503."""

    def provider() -> ChatModel:
        return get_chat_model(settings)

    return provider


def get_current_user_id() -> str:
    """Who is making the request. TEMPORARY: always the dev user until M11 adds login (JWT).

    Every endpoint that needs an owner depends on this, so M11 changes only this function.
    """
    return DEV_USER_ID
