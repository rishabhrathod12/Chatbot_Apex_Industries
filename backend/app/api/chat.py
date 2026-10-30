"""Question answering over the indexed documents.

TEMPORARY until M11: there is no login yet, so this acts as the single dev user and accepts an
optional `department` to stand in for the departments that will come from the user's token.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import (
    ChatModelProvider,
    get_chat_model_provider,
    get_embedder_provider,
    get_store_provider,
)
from app.core.config import Settings, get_settings
from app.core.departments import canonical_department
from app.core.dev_user import DEV_USER_ID
from app.rag.generation import LlmError, answer_question
from app.rag.ingestion.embedder import EmbeddingError, embedding_dimensions
from app.rag.ingestion.pipeline import EmbedderProvider, StoreProvider
from app.rag.vectorstore import SearchFilter, VectorStoreError
from app.schemas.chat import ChatAnswer

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    scope: Literal["personal", "enterprise"] = "enterprise"
    department: str | None = None


@router.post("", response_model=ChatAnswer)
def chat(
    body: ChatRequest,
    settings: Settings = Depends(get_settings),
    embedder_provider: EmbedderProvider = Depends(get_embedder_provider),
    store_provider: StoreProvider = Depends(get_store_provider),
    chat_model_provider: ChatModelProvider = Depends(get_chat_model_provider),
) -> ChatAnswer:
    if body.scope == "personal":
        search_filter: SearchFilter | None = SearchFilter(owner_id=DEV_USER_ID)
    elif body.department is not None:
        dept = canonical_department(body.department)
        if dept is None:
            raise HTTPException(status_code=422, detail="invalid_department")
        search_filter = SearchFilter(departments=[dept])
    else:
        search_filter = None

    try:
        return answer_question(
            body.question,
            embedder=embedder_provider(),
            store=store_provider(body.scope, embedding_dimensions(settings)),
            chat_model=chat_model_provider(),
            search_filter=search_filter,
            top_k=settings.retrieval_top_k,
            min_score=settings.retrieval_min_score,
        )
    except (EmbeddingError, LlmError) as exc:
        status = 503 if exc.reason in {"embedding_not_configured", "llm_not_configured"} else 502
        raise HTTPException(status_code=status, detail=exc.reason) from exc
    except VectorStoreError as exc:
        raise HTTPException(status_code=500, detail=exc.reason) from exc
