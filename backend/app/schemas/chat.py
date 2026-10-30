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


class ChatAnswer(BaseModel):
    status: Literal["answered", "not_found"]
    answer: str
    sources: list[Source]
    # Why nothing was answered: no_relevant_context, model_declined, uncited_answer, empty_answer.
    reason: str | None = None
