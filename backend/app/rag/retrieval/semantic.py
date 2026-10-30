from app.rag.ingestion.embedder import Embedder
from app.rag.vectorstore import SearchFilter, SearchHit, VectorStore


def semantic_search(
    embedder: Embedder,
    store: VectorStore,
    query: str,
    *,
    top_k: int = 5,
    search_filter: SearchFilter | None = None,
) -> list[SearchHit]:
    """Embed the query and return the closest chunks that the filter allows."""
    vector = embedder.embed([query])[0]
    return store.query(vector, top_k=top_k, search_filter=search_filter)
