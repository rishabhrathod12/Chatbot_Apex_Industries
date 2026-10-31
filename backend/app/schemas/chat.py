from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Source(BaseModel):
    """A passage the answer relied on. Built from stored chunk metadata, never from model output."""

    number: int  # the [n] marker used in the answer text
    chunk_id: str
    doc_code: str
    doc_title: str
    source_name: str
    section_title: str
    page_start: int | None = None
    page_end: int | None = None
    # A sentence copied word for word from the cited chunk, so it can be checked against the page.
    quote: str = ""


class ChatAnswer(BaseModel):
    status: Literal["answered", "not_found"]
    answer: str
    sources: list[Source]
    # Why nothing was answered: no_relevant_context, low_relevance, model_declined, uncited_answer,
    # empty_answer.
    reason: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    # The standalone question used for retrieval, when a follow-up was rewritten.
    rewritten_question: str | None = None


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    status: str | None = None
    reason: str | None = None
    sources: list[Source] = []
    rewritten_question: str | None = None
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]
