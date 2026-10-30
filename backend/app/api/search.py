"""TEMPORARY search endpoint for development and retrieval checks.

Until M11 there is no login, so it acts as the single dev user. The optional `department` field is
a stand-in: from M11 the allowed departments come from the user's token and this field goes away.
It returns raw chunks, not generated answers (those arrive in M7).
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user_id, get_embedder_provider, get_store_provider
from app.core.config import Settings, get_settings
from app.core.departments import canonical_department
from app.rag.ingestion.embedder import EmbeddingError, embedding_dimensions
from app.rag.ingestion.pipeline import EmbedderProvider, StoreProvider
from app.rag.retrieval import semantic_search
from app.rag.vectorstore import SearchFilter, VectorStoreError

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
    user_id: str = Depends(get_current_user_id),
) -> list[SearchHitOut]:
    if body.scope == "personal":
        search_filter = SearchFilter(owner_id=user_id)
    elif body.department is not None:
        dept = canonical_department(body.department)
        if dept is None:
            raise HTTPException(status_code=422, detail="invalid_department")
        search_filter = SearchFilter(departments=[dept])
    else:
        search_filter = None

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
