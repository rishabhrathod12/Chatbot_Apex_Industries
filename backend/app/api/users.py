"""User management. Super Admin only."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.permissions import Principal, Role
from app.models.user import User
from app.repositories import users as repo
from app.schemas.auth import PasswordReset, UserCreate, UserOut, UserUpdate
from app.services import users as user_service

router = APIRouter(prefix="/users", tags=["users"])


def _out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        login_id=user.login_id,
        email=user.email,
        full_name=user.full_name,
        role=Role(user.role),
        departments=sorted(user.department_codes),
        is_active=user.is_active,
        created_at=user.created_at,
    )


def _get_or_404(db: Session, user_id: str) -> User:
    user = repo.get(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return user


def _refuse(exc: user_service.UserError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.reason)


@router.get("", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db), _admin: Principal = Depends(require_admin)
) -> list[UserOut]:
    return [_out(u) for u in repo.list_all(db)]


@router.post("", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    admin: Principal = Depends(require_admin),
) -> UserOut:
    try:
        return _out(user_service.create_user(db, settings, admin, body))
    except user_service.UserError as exc:
        raise _refuse(exc) from exc


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: str,
    body: UserUpdate,
    db: Session = Depends(get_db),
    admin: Principal = Depends(require_admin),
) -> UserOut:
    user = _get_or_404(db, user_id)
    try:
        return _out(user_service.update_user(db, admin, user, body))
    except user_service.UserError as exc:
        raise _refuse(exc) from exc


@router.post("/{user_id}/password", status_code=204)
def reset_password(
    user_id: str,
    body: PasswordReset,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    admin: Principal = Depends(require_admin),
) -> Response:
    user = _get_or_404(db, user_id)
    try:
        user_service.reset_password(db, settings, admin, user, body.password)
    except user_service.UserError as exc:
        raise _refuse(exc) from exc
    return Response(status_code=204)
