"""Numbers and history for the Super Admin dashboard.

Only company documents are counted or named here. Personal documents belong to their owners:
the dashboard never lists them, counts them, or shows their file names, even to a Super Admin.
"""

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.departments import DEPARTMENTS
from app.models import AuditLog, Document, User, UserDepartment
from app.schemas.admin import (
    AccountCounts,
    AuditEntry,
    AuditPage,
    DepartmentSummary,
    DocumentCounts,
    RecentUpload,
    StatsOut,
)

# What counts as an "administrative activity" on the overview.
ADMIN_ACTIONS = (
    "setup_admin",
    "user_create",
    "user_update",
    "user_activate",
    "user_deactivate",
    "user_role_change",
    "user_departments_change",
    "password_reset",
    "document_upload",
    "document_delete",
    "document_reindex",
)
_DOCUMENT_ACTIONS = {"document_upload", "document_delete", "document_reindex", "document_access"}
_RECENT = 10


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _labels(db: Session, rows: Iterable[AuditLog]) -> tuple[dict[str, User], dict[str, str]]:
    """The people named in these rows, and a display name for each company document named."""
    rows = list(rows)
    user_ids = {r.user_id for r in rows if r.user_id}
    user_ids |= {r.target_id for r in rows if r.target_type == "user" and r.target_id}
    document_ids = {r.target_id for r in rows if r.target_type == "document" and r.target_id}
    users = (
        {u.id: u for u in db.scalars(select(User).where(User.id.in_(user_ids)))} if user_ids else {}
    )
    documents: dict[str, str] = {}
    if document_ids:
        for doc in db.scalars(select(Document).where(Document.id.in_(document_ids))):
            if doc.scope == "enterprise":  # a personal file's name is its owner's business
                documents[doc.id] = doc.title or doc.original_filename
    return users, documents


def _entries(db: Session, rows: list[AuditLog]) -> list[AuditEntry]:
    users, documents = _labels(db, rows)
    entries = []
    for row in rows:
        actor = users.get(row.user_id) if row.user_id else None
        label: str | None = None
        if row.target_type == "user" and row.target_id in users:
            label = users[row.target_id].full_name
        elif row.target_type == "document":
            label = documents.get(row.target_id or "", "a personal document")
        elif row.target_type in {"department", "portal"}:
            label = row.target_id
        entries.append(
            AuditEntry(
                id=row.id,
                created_at=_utc(row.created_at),
                action=row.action,
                outcome=row.outcome,
                actor_id=row.user_id,
                actor_name=actor.full_name if actor else None,
                actor_login_id=actor.login_id if actor else None,
                target_type=row.target_type,
                target_id=row.target_id,
                target_label=label,
                detail=row.detail,
            )
        )
    return entries


def audit_page(
    db: Session,
    *,
    page: int,
    page_size: int,
    action: str | None = None,
    outcome: str | None = None,
    user_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> AuditPage:
    conditions = []
    if action:
        conditions.append(AuditLog.action == action)
    if outcome:
        conditions.append(AuditLog.outcome == outcome)
    if user_id:
        conditions.append(AuditLog.user_id == user_id)
    if date_from:
        conditions.append(AuditLog.created_at >= _utc(date_from))
    if date_to:
        conditions.append(AuditLog.created_at <= _utc(date_to))

    total = int(db.scalar(select(func.count()).select_from(AuditLog).where(*conditions)) or 0)
    rows = list(
        db.scalars(
            select(AuditLog)
            .where(*conditions)
            .order_by(AuditLog.id.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    )
    actions = sorted(db.scalars(select(AuditLog.action).distinct()))
    return AuditPage(
        items=_entries(db, rows),
        total=total,
        page=page,
        page_size=page_size,
        available_actions=actions,
    )


def _recent_activity(db: Session) -> list[AuditEntry]:
    candidates = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.action.in_(ADMIN_ACTIONS), AuditLog.outcome == "ok")
            .order_by(AuditLog.id.desc())
            .limit(200)
        )
    )
    # Uploads, deletions and re-indexing of someone's personal document are not company business.
    _, company_documents = _labels(db, [r for r in candidates if r.action in _DOCUMENT_ACTIONS])
    kept = [
        r
        for r in candidates
        if r.action not in _DOCUMENT_ACTIONS or (r.target_id or "") in company_documents
    ]
    return _entries(db, kept[:_RECENT])


def stats(db: Session) -> StatsOut:
    by_role: dict[str, int] = {}
    active = inactive = 0
    for role, is_active, count in db.execute(
        select(User.role, User.is_active, func.count()).group_by(User.role, User.is_active)
    ):
        by_role[role] = by_role.get(role, 0) + count
        if is_active:
            active += count
        else:
            inactive += count

    members: dict[tuple[str, str], int] = {}
    for department, role, count in db.execute(
        select(UserDepartment.department, User.role, func.count())
        .join(User, User.id == UserDepartment.user_id)
        .where(User.is_active.is_(True))
        .group_by(UserDepartment.department, User.role)
    ):
        members[(department, role)] = count

    company = (Document.scope == "enterprise", Document.deleted_at.is_(None))
    count_by_department = (
        select(Document.department, func.count()).where(*company).group_by(Document.department)
    )
    count_by_status = (
        select(Document.status, func.count()).where(*company).group_by(Document.status)
    )
    per_department = {name: count for name, count in db.execute(count_by_department)}
    by_status = {name: count for name, count in db.execute(count_by_status)}

    uploads = list(
        db.scalars(
            select(Document).where(*company).order_by(Document.created_at.desc()).limit(_RECENT)
        )
    )
    uploaders = {
        u.id: u.full_name
        for u in db.scalars(select(User).where(User.id.in_({d.owner_id for d in uploads})))
    }
    return StatsOut(
        accounts=AccountCounts(
            total=active + inactive, active=active, inactive=inactive, by_role=by_role
        ),
        departments=[
            DepartmentSummary(
                code=code,
                employees=members.get((code, "employee"), 0),
                managers=members.get((code, "department_manager"), 0),
                documents=per_department.get(code, 0),
            )
            for code in DEPARTMENTS
        ],
        documents=DocumentCounts(total=sum(by_status.values()), by_status=by_status),
        recent_uploads=[
            RecentUpload(
                document_id=d.id,
                title=d.title or d.original_filename,
                filename=d.original_filename,
                department=d.department,
                status=d.status,
                uploaded_by=uploaders.get(d.owner_id, "unknown"),
                created_at=_utc(d.created_at),
            )
            for d in uploads
        ],
        recent_activity=_recent_activity(db),
    )
