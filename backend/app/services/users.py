import re
import secrets
import threading

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


def generate_temporary_password(settings: Settings) -> str:
    """A random password for an admin to hand over once. Always meets the length rule."""
    return secrets.token_urlsafe(max(16, settings.password_min_length))


def _name_to_login_base(full_name: str) -> str:
    """ "Esha Production" -> "esha.production"; a lone name stays as it is."""
    words = re.sub(r"[^a-z0-9 ._-]", "", full_name.lower()).split()
    return ".".join([words[0], words[-1]] if len(words) > 1 else words) or "user"


def _create(
    db: Session,
    settings: Settings,
    actor: Principal | None,
    data: UserCreate,
    *,
    password: str,
    must_change_password: bool,
) -> User:
    check_password(password, settings)
    _check_departments(data.role, data.departments)
    if repo.get_by_email(db, data.email):
        raise UserError("email_taken", status=409)
    if data.login_id and repo.get_by_login_id(db, data.login_id):
        raise UserError("login_id_taken", status=409)
    user = repo.create(
        db,
        email=data.email,
        full_name=data.full_name,
        password_hash=hash_password(password, settings.password_hash_cost_log2),
        role=data.role.value,
        departments=data.departments,
        login_id=data.login_id or _name_to_login_base(data.full_name),
        must_change_password=must_change_password,
        created_by=actor.user_id if actor else None,
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


def create_user(db: Session, settings: Settings, actor: Principal | None, data: UserCreate) -> User:
    """Create an account whose owner already knows the password (the command line, first-time
    setup, tests). Nobody is forced to change it."""
    if data.password is None:
        raise UserError("password_required")
    return _create(db, settings, actor, data, password=data.password, must_change_password=False)


def create_account_for_employee(
    db: Session, settings: Settings, actor: Principal, data: UserCreate
) -> tuple[User, str | None]:
    """An admin creates an account. The person must choose their own password at first sign-in.

    Returns the user and, if the server generated the password, that password. It exists only
    in this return value: it is hashed before anything is saved and appears in no log or audit row.
    """
    generated = data.password is None
    password = data.password or generate_temporary_password(settings)
    user = _create(db, settings, actor, data, password=password, must_change_password=True)
    return user, (password if generated else None)


_admin_lock = threading.Lock()  # one API process: two admins cannot remove each other at once


def update_user(db: Session, actor: Principal, user: User, data: UserUpdate) -> User:
    """Change a user. The company can never be left without a working Super Admin: admins cannot
    demote or disable themselves, and nobody can remove the last active Super Admin (this also
    covers two admins removing each other at the same moment)."""
    with _admin_lock:
        return _update_user(db, actor, user, data)


def _update_user(db: Session, actor: Principal, user: User, data: UserUpdate) -> User:
    if user.id == actor.user_id and (
        data.is_active is False or (data.role is not None and data.role is not Role.SUPER_ADMIN)
    ):
        raise UserError("cannot_demote_or_disable_self", status=409)

    new_role = data.role if data.role is not None else Role(user.role)
    new_departments = (
        data.departments if data.departments is not None else sorted(user.department_codes)
    )
    _check_departments(new_role, new_departments)

    removes_an_admin = (
        Role(user.role) is Role.SUPER_ADMIN
        and user.is_active
        and (data.is_active is False or new_role is not Role.SUPER_ADMIN)
    )
    if removes_an_admin and repo.count_active_super_admins(db, exclude_user_id=user.id) == 0:
        raise UserError("last_super_admin", status=409)

    # What really changes, so the audit log can say "disabled" or "made Super Admin" instead of
    # a vague "updated".
    changes: list[tuple[str, str | None]] = []
    if data.is_active is not None and data.is_active != user.is_active:
        changes.append(("user_activate" if data.is_active else "user_deactivate", None))
    if new_role.value != user.role:
        changes.append(("user_role_change", f"role:{new_role.value}"))
    new_codes = sorted(new_departments)
    if new_codes != sorted(user.department_codes):
        changes.append(("user_departments_change", ",".join(new_codes)[:64] or "none"))
    if data.full_name is not None and data.full_name != user.full_name:
        changes.append(("user_update", None))

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
    for action, detail in changes:
        audit.record(
            db,
            action,
            "ok",
            user_id=actor.user_id,
            target_type="user",
            target_id=user.id,
            detail=detail,
        )
    return user


def reset_password(
    db: Session, settings: Settings, actor: Principal | None, user: User, password: str | None
) -> str | None:
    """An admin (or the command line, with `actor` None) sets a new password. The person must
    replace it at their next sign-in.

    Returns the password only if the server generated it (shown to the admin once).
    """
    generated = password is None
    new_password = password or generate_temporary_password(settings)
    check_password(new_password, settings)
    user.password_hash = hash_password(new_password, settings.password_hash_cost_log2)
    user.must_change_password = True
    repo.revoke_all_refresh_tokens(db, user.id)  # old sessions end when the password changes
    db.commit()
    audit.record(
        db,
        "password_reset",
        "ok",
        user_id=actor.user_id if actor else None,
        target_type="user",
        target_id=user.id,
    )
    return new_password if generated else None
