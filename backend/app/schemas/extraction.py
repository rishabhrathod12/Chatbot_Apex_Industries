from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

# title_block:      cover-page title and metadata table
# body:             real content
# boilerplate:      repeated disclaimer / document-control text (kept for display, not for indexing)
# revision_history: version table (kept for display, not for indexing)
BlockKind = Literal["title_block", "body", "boilerplate", "revision_history"]


class Block(BaseModel):
    kind: BlockKind
    section_title: str | None = None
    text: str


class Page(BaseModel):
    """One page of cleaned text. `number` is the real 1-based PDF page, or None for DOCX/TXT."""

    number: int | None
    blocks: list[Block] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


class DocumentMetadata(BaseModel):
    doc_code: str | None = None
    title: str | None = None
    department: str | None = None  # canonical code, see app.core.departments
    version: str | None = None
    effective_date: date | None = None
    owner: str | None = None
    classification: str | None = None


class ExtractedDocument(BaseModel):
    pages: list[Page]
    metadata: DocumentMetadata
    warnings: list[str] = Field(default_factory=list)
