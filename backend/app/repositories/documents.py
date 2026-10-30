from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document


def add(db: Session, document: Document) -> Document:
    db.add(document)
    db.commit()
    return document


def get(db: Session, document_id: str) -> Document | None:
    return db.scalars(
        select(Document).where(Document.id == document_id, Document.deleted_at.is_(None))
    ).first()


def list_all(db: Session) -> list[Document]:
    return list(
        db.scalars(
            select(Document).where(Document.deleted_at.is_(None)).order_by(Document.created_at)
        )
    )


def find_duplicate(db: Session, dedupe_key: str, file_hash: str) -> Document | None:
    return db.scalars(
        select(Document).where(
            Document.dedupe_key == dedupe_key,
            Document.file_hash == file_hash,
            Document.deleted_at.is_(None),
        )
    ).first()


def soft_delete(db: Session, document: Document) -> None:
    document.deleted_at = datetime.now(UTC)
    document.status = "deleted"
    db.commit()
