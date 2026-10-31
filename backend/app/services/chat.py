import logging

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.rag.generation import ChatModel, HistoryTurn, answer_question, condense_question
from app.rag.ingestion.embedder import Embedder
from app.rag.vectorstore import SearchFilter, VectorStore
from app.repositories import conversations as repo
from app.schemas.chat import ChatAnswer

logger = logging.getLogger("app.chat")

MAX_MESSAGES_PER_CONVERSATION = 200


class ConversationNotFound(Exception):
    pass


class ConversationTooLong(Exception):
    pass


def chat(
    db: Session,
    *,
    user_id: str,
    question: str,
    conversation_id: str | None,
    embedder: Embedder,
    store: VectorStore,
    chat_model: ChatModel,
    search_filter: SearchFilter | None,
    settings: Settings,
) -> ChatAnswer:
    """Answer a question, remembering the conversation.

    Nothing is saved unless the answer step succeeds, so a model failure never leaves a
    half-written conversation behind.
    """
    conversation = None
    history: list[HistoryTurn] = []
    if conversation_id is not None:
        conversation = repo.get_owned(db, conversation_id, user_id)
        if conversation is None:
            raise ConversationNotFound
        if repo.message_count(db, conversation) >= MAX_MESSAGES_PER_CONVERSATION:
            raise ConversationTooLong
        recent = repo.recent_messages(conversation, settings.chat_history_turns * 2)
        history = [HistoryTurn(m.role, m.content) for m in recent]  # type: ignore[arg-type]

    rewritten = condense_question(history, question, chat_model)
    if rewritten is not None and rewritten.casefold() == question.strip().casefold():
        rewritten = None

    result = answer_question(
        rewritten or question,
        embedder=embedder,
        store=store,
        chat_model=chat_model,
        search_filter=search_filter,
        top_k=settings.retrieval_top_k,
        min_score=settings.retrieval_min_score,
        retrieval_mode=settings.retrieval_mode,
        candidates=settings.retrieval_candidates,
        max_scan=settings.lexical_max_chunks,
        use_rerank=settings.rerank_enabled,
        rerank_top_n=settings.rerank_top_n,
        rerank_min_relevance=settings.rerank_min_relevance,
    )

    if conversation is None:
        conversation = repo.create(db, user_id, question)
    repo.add_message(conversation, "user", question)
    reply = repo.add_message(
        conversation,
        "assistant",
        result.answer,
        status=result.status,
        reason=result.reason,
        sources=result.sources,
        rewritten_question=rewritten,
    )
    db.commit()
    logger.info(
        "conversation updated",
        extra={"conversation_id": conversation.id, "rewritten": rewritten is not None},
    )
    return result.model_copy(
        update={
            "conversation_id": conversation.id,
            "message_id": reply.id,
            "rewritten_question": rewritten,
        }
    )
