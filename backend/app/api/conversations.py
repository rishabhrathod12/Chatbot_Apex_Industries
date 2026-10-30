from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user_id
from app.core.database import get_db
from app.models.conversation import Conversation
from app.repositories import conversations as repo
from app.schemas.chat import ConversationDetail, ConversationOut, MessageOut

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _owned_or_404(db: Session, conversation_id: str, user_id: str) -> Conversation:
    conversation = repo.get_owned(db, conversation_id, user_id)
    if conversation is None:
        # Same answer whether it does not exist or belongs to someone else.
        raise HTTPException(status_code=404, detail="conversation_not_found")
    return conversation


@router.get("", response_model=list[ConversationOut])
def list_conversations(
    db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
) -> list[ConversationOut]:
    return [
        ConversationOut(id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at)
        for c in repo.list_owned(db, user_id)
    ]


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> ConversationDetail:
    conversation = _owned_or_404(db, conversation_id, user_id)
    messages = [
        MessageOut(
            id=m.id,
            role="assistant" if m.role == "assistant" else "user",
            content=m.content,
            status=m.status,
            reason=m.reason,
            sources=repo.sources_of(m),
            rewritten_question=m.rewritten_question,
            created_at=m.created_at,
        )
        for m in conversation.messages
    ]
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=messages,
    )


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> Response:
    repo.delete(db, _owned_or_404(db, conversation_id, user_id))
    return Response(status_code=204)
