import hashlib

from sqlalchemy.orm import Session

from app.models.audit import AuditLog


def fingerprint(value: str) -> str:
    """A short, one-way label for something we should not store as typed (like an email that was
    tried at login: people sometimes type their password into that box)."""
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:16]


def record(
    db: Session,
    action: str,
    outcome: str,
    *,
    user_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: str | None = None,
) -> None:
    """Write one audit row and commit it on its own, so it survives even if the request that
    caused it later fails. Pass only ids and codes, never text from documents or questions."""
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            outcome=outcome,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
    )
    db.commit()
