from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.permissions import Principal
from app.models.document import Document


def add(db: Session, document: Document) -> Document:
    db.add(document)
    db.commit()
    return document


def get(db: Session, document_id: str) -> Document | None:
    return db.scalars(
        select(Document).where(Document.id == document_id, Document.deleted_at.is_(None))
    ).first()


def list_visible(db: Session, principal: Principal) -> list[Document]:
    """The documents this user may read: their own personal ones, plus company documents they can
    see (all of them for a Super Admin, otherwise their departments and company-wide ones)."""
    own_personal = and_(Document.scope == "personal", Document.owner_id == principal.user_id)
    if principal.is_admin:
        allowed = or_(Document.scope == "enterprise", own_personal)
    else:
        in_use = Document.status != "archived"
        if principal.is_manager:  # managers still see their own departments' archived documents
            in_use = or_(in_use, Document.department.in_(principal.departments))
        company = and_(
            Document.scope == "enterprise",
            or_(Document.visibility == "company", Document.department.in_(principal.departments)),
            in_use,
        )
        allowed = or_(company, own_personal)
    return list(
        db.scalars(
            select(Document)
            .where(Document.deleted_at.is_(None), allowed)
            .order_by(Document.created_at)
        )
    )


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


def archive(db: Session, document: Document) -> None:
    document.status = "archived"
    document.status_reason = None
    document.chunk_count = 0
    document.archived_at = datetime.now(UTC)
    db.commit()


def unarchive(db: Session, document: Document) -> None:
    """Back to the start of the pipeline: the caller queues the indexing."""
    document.status = "uploaded"
    document.archived_at = None
    db.commit()


def soft_delete(db: Session, document: Document) -> None:
    document.deleted_at = datetime.now(UTC)
    document.status = "deleted"
    db.commit()
