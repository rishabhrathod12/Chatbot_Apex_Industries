"""Search endpoint for development and retrieval checks. Returns raw chunks, not answers.

What a user can find is decided by the server from who they are. The optional `department` only
narrows the search to one department's documents. It can never reveal more than the user may see.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal, get_embedder_provider, get_store_provider
from app.core.config import Settings, get_settings
from app.core.departments import canonical_department
from app.core.permissions import Principal, build_search_filter
from app.rag.ingestion.embedder import EmbeddingError, embedding_dimensions
from app.rag.ingestion.pipeline import EmbedderProvider, StoreProvider
from app.rag.retrieval import semantic_search
from app.rag.vectorstore import VectorStoreError

router = APIRouter(prefix="/search", tags=["search"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    scope: Literal["personal", "enterprise"] = "enterprise"
    department: str | None = None


class SearchHitOut(BaseModel):
    chunk_id: str
    score: float
    text: str
    document_id: str
    source_name: str
    doc_code: str
    doc_title: str
    section_title: str
    department: str
    page_start: int | None = None
    page_end: int | None = None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


@router.post("", response_model=list[SearchHitOut])
def search(
    body: SearchRequest,
    settings: Settings = Depends(get_settings),
    embedder_provider: EmbedderProvider = Depends(get_embedder_provider),
    store_provider: StoreProvider = Depends(get_store_provider),
    principal: Principal = Depends(get_current_principal),
) -> list[SearchHitOut]:
    narrow = None
    if body.scope == "enterprise" and body.department is not None:
        narrow = canonical_department(body.department)
        if narrow is None:
            raise HTTPException(status_code=422, detail="invalid_department")
    search_filter = build_search_filter(principal, body.scope, narrow)

    try:
        embedder = embedder_provider()
        store = store_provider(body.scope, embedding_dimensions(settings))
        hits = semantic_search(
            embedder, store, body.query, top_k=body.top_k, search_filter=search_filter
        )
    except EmbeddingError as exc:
        raise HTTPException(status_code=503, detail=exc.reason) from exc
    except VectorStoreError as exc:
        raise HTTPException(status_code=500, detail=exc.reason) from exc

    return [
        SearchHitOut(
            chunk_id=h.chunk_id,
            score=h.score,
            text=h.text,
            document_id=str(h.metadata.get("document_id", "")),
            source_name=str(h.metadata.get("source_name", "")),
            doc_code=str(h.metadata.get("doc_code", "")),
            doc_title=str(h.metadata.get("doc_title", "")),
            section_title=str(h.metadata.get("section_title", "")),
            department=str(h.metadata.get("department", "")),
            page_start=_as_int(h.metadata.get("page_start")),
            page_end=_as_int(h.metadata.get("page_end")),
        )
        for h in hits
    ]
