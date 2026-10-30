import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conversation import Conversation, Message
from app.schemas.chat import Source

TITLE_CHARS = 60


def create(db: Session, user_id: str, first_question: str) -> Conversation:
    title = " ".join(first_question.split())[:TITLE_CHARS] or "New conversation"
    conversation = Conversation(user_id=user_id, title=title)
    db.add(conversation)
    db.flush()
    return conversation


def get_owned(db: Session, conversation_id: str, user_id: str) -> Conversation | None:
    """Only the owner can see a conversation. Callers report 'not found' either way."""
    return db.scalars(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    ).first()


def list_owned(db: Session, user_id: str, limit: int = 50) -> list[Conversation]:
    return list(
        db.scalars(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
    )


def message_count(db: Session, conversation: Conversation) -> int:
    return len(conversation.messages)


def recent_messages(conversation: Conversation, limit: int) -> list[Message]:
    return conversation.messages[-limit:] if limit > 0 else []


def add_message(
    conversation: Conversation,
    role: str,
    content: str,
    *,
    status: str | None = None,
    reason: str | None = None,
    sources: list[Source] | None = None,
    rewritten_question: str | None = None,
) -> Message:
    message = Message(
        position=len(conversation.messages),
        role=role,
        content=content,
        status=status,
        reason=reason,
        sources_json=json.dumps([s.model_dump() for s in sources]) if sources else None,
        rewritten_question=rewritten_question,
    )
    conversation.messages.append(message)
    conversation.updated_at = datetime.now(UTC)
    return message


def sources_of(message: Message) -> list[Source]:
    if not message.sources_json:
        return []
    return [Source.model_validate(item) for item in json.loads(message.sources_json)]


def delete(db: Session, conversation: Conversation) -> None:
    db.delete(conversation)
    db.commit()
