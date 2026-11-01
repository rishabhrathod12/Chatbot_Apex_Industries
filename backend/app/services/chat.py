import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.permissions import Principal, can_read_document
from app.models.document import Document
from app.rag.generation import ChatModel, HistoryTurn, answer_question, condense_question
from app.rag.ingestion.embedder import Embedder
from app.rag.vectorstore import SearchFilter, VectorStore
from app.repositories import conversations as repo
from app.schemas.chat import ChatAnswer, Source

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
        keyword_rescue=settings.retrieval_keyword_rescue,
        overview=settings.overview_enabled,
        overview_max_documents=settings.overview_max_documents,
        overview_max_chars=settings.overview_max_chars,
        overview_max_answer_tokens=settings.overview_max_answer_tokens,
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


def redact_unreadable_sources(
    db: Session, principal: Principal, per_message: list[list[Source]]
) -> list[list[Source]]:
    """Old answers keep their citations, but a citation to a document the person can no longer
    read (archived, deleted, moved out of their department) is replaced by a blank placeholder:
    no title, no section, no quote. The [n] numbers in the answer text still line up.
    """
    ids = {s.chunk_id.split(":", 1)[0] for sources in per_message for s in sources}
    readable: set[str] = set()
    if ids:
        documents = db.scalars(
            select(Document).where(Document.id.in_(ids), Document.deleted_at.is_(None))
        )
        readable = {d.id for d in documents if can_read_document(principal, d)}
    return [
        [
            s
            if s.chunk_id.split(":", 1)[0] in readable
            else Source(
                number=s.number,
                chunk_id="",
                doc_code="",
                doc_title="",
                source_name="",
                section_title="",
                available=False,
            )
            for s in sources
        ]
        for sources in per_message
    ]
