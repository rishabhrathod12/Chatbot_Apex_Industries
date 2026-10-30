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
        company = and_(
            Document.scope == "enterprise",
            or_(Document.visibility == "company", Document.department.in_(principal.departments)),
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


def soft_delete(db: Session, document: Document) -> None:
    document.deleted_at = datetime.now(UTC)
    document.status = "deleted"
    db.commit()
