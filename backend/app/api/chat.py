"""Question answering over the indexed documents, with saved conversations.

TEMPORARY until M11: there is no login yet, so the user is the dev user, and an optional
`department` stands in for the departments that will come from the user's token.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import (
    ChatModelProvider,
    get_chat_model_provider,
    get_current_user_id,
    get_embedder_provider,
    get_store_provider,
)
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.departments import canonical_department
from app.rag.generation import LlmError
from app.rag.ingestion.embedder import EmbeddingError, embedding_dimensions
from app.rag.ingestion.pipeline import EmbedderProvider, StoreProvider
from app.rag.vectorstore import SearchFilter, VectorStoreError
from app.schemas.chat import ChatAnswer
from app.services import chat as chat_service

router = APIRouter(prefix="/chat", tags=["chat"])

ConversationId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    scope: Literal["personal", "enterprise"] = "enterprise"
    department: str | None = None
    # Leave out to start a new conversation, or pass an earlier one to ask a follow-up.
    conversation_id: ConversationId | None = None


@router.post("", response_model=ChatAnswer)
def chat(
    body: ChatRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    embedder_provider: EmbedderProvider = Depends(get_embedder_provider),
    store_provider: StoreProvider = Depends(get_store_provider),
    chat_model_provider: ChatModelProvider = Depends(get_chat_model_provider),
    user_id: str = Depends(get_current_user_id),
) -> ChatAnswer:
    if body.scope == "personal":
        search_filter: SearchFilter | None = SearchFilter(owner_id=user_id)
    elif body.department is not None:
        dept = canonical_department(body.department)
        if dept is None:
            raise HTTPException(status_code=422, detail="invalid_department")
        search_filter = SearchFilter(departments=[dept])
    else:
        search_filter = None

    try:
        return chat_service.chat(
            db,
            user_id=user_id,
            question=body.question,
            conversation_id=body.conversation_id,
            embedder=embedder_provider(),
            store=store_provider(body.scope, embedding_dimensions(settings)),
            chat_model=chat_model_provider(),
            search_filter=search_filter,
            settings=settings,
        )
    except chat_service.ConversationNotFound as exc:
        # Same answer whether it does not exist or belongs to someone else.
        raise HTTPException(status_code=404, detail="conversation_not_found") from exc
    except chat_service.ConversationTooLong as exc:
        raise HTTPException(status_code=409, detail="conversation_too_long") from exc
    except (EmbeddingError, LlmError) as exc:
        status = 503 if exc.reason in {"embedding_not_configured", "llm_not_configured"} else 502
        raise HTTPException(status_code=status, detail=exc.reason) from exc
    except VectorStoreError as exc:
        raise HTTPException(status_code=500, detail=exc.reason) from exc
