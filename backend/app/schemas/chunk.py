from typing import Literal

from pydantic import BaseModel

from app.schemas.extraction import BlockKind


class DocumentContext(BaseModel):
    """What the chunker needs to know about a document that the file itself doesn't say."""

    document_id: str
    scope: Literal["personal", "enterprise"]
    owner_id: str
    source_name: str  # original filename, used in citations
    department: str = ""  # canonical code, empty for personal documents
    visibility: Literal["personal", "department", "company"] = "department"
    doc_version: str | None = None  # falls back to the version in the cover-page metadata
    doc_title: str | None = None  # falls back to the title in the cover-page metadata


class Chunk(BaseModel):
    chunk_id: str
    document_id: str
    chunk_index: int
    text: str
    kind: BlockKind
    section_title: str | None
    page_start: int | None  # real PDF pages, None for DOCX/TXT
    page_end: int | None
    doc_code: str | None
    doc_title: str | None
    doc_version: str
    scope: Literal["personal", "enterprise"]
    department: str
    visibility: Literal["personal", "department", "company"]
    owner_id: str
    source_name: str
    content_hash: str

    @property
    def embedding_text(self) -> str:
        """Text to embed: the chunk plus a short context line, so a bare bullet still says what
        document and section it belongs to."""
        heading = self.doc_title or self.source_name
        if self.section_title:
            heading = f"{heading} > {self.section_title}"
        return f"{heading}\n{self.text}"

    def to_metadata(self) -> dict[str, str | int]:
        """Flat metadata for the vector store. Chroma rejects None, so absent values are omitted
        (pages) or empty (strings)."""
        meta: dict[str, str | int] = {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "chunk_index": self.chunk_index,
            "kind": self.kind,
            "section_title": self.section_title or "",
            "doc_code": self.doc_code or "",
            "doc_title": self.doc_title or "",
            "doc_version": self.doc_version,
            "scope": self.scope,
            "department": self.department,
            "visibility": self.visibility,
            "owner_id": self.owner_id,
            "source_name": self.source_name,
            "content_hash": self.content_hash,
        }
        if self.page_start is not None:
            meta["page_start"] = self.page_start
        if self.page_end is not None:
            meta["page_end"] = self.page_end
        return meta
