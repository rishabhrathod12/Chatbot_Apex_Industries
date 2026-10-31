"""Dashboard numbers and the audit log. Super Admin only."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.core.permissions import Principal
from app.schemas.admin import AuditPage, StatsOut
from app.services import admin as admin_service

router = APIRouter(prefix="/admin", tags=["admin"])

ActionName = Annotated[str, Query(pattern=r"^[a-z_]{1,48}$")]
UserId = Annotated[str, Query(pattern=r"^[0-9a-f]{32}$")]


@router.get("/stats", response_model=StatsOut)
def stats(db: Session = Depends(get_db), _admin: Principal = Depends(require_admin)) -> StatsOut:
    return admin_service.stats(db)


@router.get("/audit", response_model=AuditPage)
def audit(
    db: Session = Depends(get_db),
    _admin: Principal = Depends(require_admin),
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    action: ActionName | None = None,
    outcome: Literal["ok", "denied", "failed"] | None = None,
    user_id: UserId | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> AuditPage:
    return admin_service.audit_page(
        db,
        page=page,
        page_size=page_size,
        action=action,
        outcome=outcome,
        user_id=user_id,
        date_from=date_from,
        date_to=date_to,
    )
