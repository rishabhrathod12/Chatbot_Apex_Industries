from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models._common import new_id, utcnow

_NOT_DELETED = text("deleted_at IS NULL")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_supersedes", "supersedes_id"),
        # One live copy of a file per dedupe_key. Soft-deleted rows don't block re-upload.
        Index(
            "uq_documents_dedupe",
            "dedupe_key",
            "file_hash",
            unique=True,
            sqlite_where=_NOT_DELETED,
            postgresql_where=_NOT_DELETED,
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(16))  # "personal" | "enterprise"
    owner_id: Mapped[str] = mapped_column(String(64))
    department: Mapped[str] = mapped_column(String(32), default="")  # "" for personal
    # "department": visible to that department. "company": visible to every logged-in user.
    # "personal": private to the owner.
    visibility: Mapped[str] = mapped_column(
        String(16), default="department", server_default="department"
    )
    dedupe_key: Mapped[str] = mapped_column(String(128))
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_filename: Mapped[str] = mapped_column(String(64))
    file_ext: Mapped[str] = mapped_column(String(8))
    mime_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    file_hash: Mapped[str] = mapped_column(String(64))
    # uploaded -> processing -> ready | failed, or archived (company documents taken out of use:
    # no vectors, not searchable). Deleted rows are soft-deleted, see deleted_at.
    status: Mapped[str] = mapped_column(String(16), default="uploaded")
    status_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doc_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    doc_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Typed by a Super Admin at upload. Kept apart from title / doc_version, which are read from
    # the file, so that re-indexing never overwrites them.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title_override: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version_override: Mapped[str | None] = mapped_column(String(32), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The earlier version this document replaces (company documents only).
    supersedes_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
