from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.permissions import Principal, Role
from app.core.security import hash_password
from app.models.user import User
from app.repositories import users as repo
from app.schemas.auth import UserCreate, UserUpdate
from app.services import audit


class UserError(Exception):
    """A user-management request was refused. `reason` is a short code, `status` the HTTP status."""

    def __init__(self, reason: str, status: int = 422) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


def check_password(password: str, settings: Settings) -> None:
    if len(password) < settings.password_min_length:
        raise UserError("password_too_short")


def _check_departments(role: Role, departments: list[str]) -> None:
    if role is Role.DEPARTMENT_MANAGER and not departments:
        raise UserError("manager_needs_department")


def create_user(db: Session, settings: Settings, actor: Principal | None, data: UserCreate) -> User:
    check_password(data.password, settings)
    _check_departments(data.role, data.departments)
    if repo.get_by_email(db, data.email):
        raise UserError("email_taken", status=409)
    user = repo.create(
        db,
        email=data.email,
        full_name=data.full_name,
        password_hash=hash_password(data.password, settings.password_hash_cost_log2),
        role=data.role.value,
        departments=data.departments,
    )
    db.commit()
    audit.record(
        db,
        "user_create",
        "ok",
        user_id=actor.user_id if actor else None,
        target_type="user",
        target_id=user.id,
    )
    return user


def update_user(db: Session, actor: Principal, user: User, data: UserUpdate) -> User:
    """Change a user. Admins cannot demote or disable themselves, so the company can never be
    left without a working Super Admin by accident."""
    if user.id == actor.user_id and (
        data.is_active is False or (data.role is not None and data.role is not Role.SUPER_ADMIN)
    ):
        raise UserError("cannot_demote_or_disable_self", status=409)

    new_role = data.role if data.role is not None else Role(user.role)
    new_departments = (
        data.departments if data.departments is not None else sorted(user.department_codes)
    )
    _check_departments(new_role, new_departments)

    if data.full_name is not None:
        user.full_name = data.full_name
    user.role = new_role.value
    if data.departments is not None:
        repo.set_departments(user, data.departments)
    if data.is_active is not None:
        user.is_active = data.is_active
        if not data.is_active:
            repo.revoke_all_refresh_tokens(db, user.id)
    db.commit()
    audit.record(
        db, "user_update", "ok", user_id=actor.user_id, target_type="user", target_id=user.id
    )
    return user


def reset_password(
    db: Session, settings: Settings, actor: Principal, user: User, password: str
) -> None:
    check_password(password, settings)
    user.password_hash = hash_password(password, settings.password_hash_cost_log2)
    repo.revoke_all_refresh_tokens(db, user.id)  # old sessions end when the password changes
    db.commit()
    audit.record(
        db, "password_reset", "ok", user_id=actor.user_id, target_type="user", target_id=user.id
    )
