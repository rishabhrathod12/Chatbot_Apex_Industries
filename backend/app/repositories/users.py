import re
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.user import RefreshToken, User, UserDepartment


def aware(moment: datetime) -> datetime:
    """SQLite hands datetimes back without a time zone. Everything we store is UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def get(db: Session, user_id: str) -> User | None:
    return db.get(User, user_id)


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalars(select(User).where(User.email == email.strip().lower())).first()


def get_by_login_id(db: Session, login_id: str) -> User | None:
    return db.scalars(select(User).where(User.login_id == login_id.strip().lower())).first()


def get_by_identifier(db: Session, identifier: str) -> User | None:
    """Find a user by what they typed at sign-in: an email (has an "@") or a Login ID."""
    identifier = identifier.strip().lower()
    if "@" in identifier:
        return get_by_email(db, identifier)
    return get_by_login_id(db, identifier)


_LOGIN_ID_MAX = 32
_LOGIN_ID_BAD = re.compile(r"[^a-z0-9._-]")


def unique_login_id(db: Session, base: str) -> str:
    """A free Login ID close to `base`: lower-case, only letters, digits and . _ -, adding a
    number if someone already has it."""
    stem = _LOGIN_ID_BAD.sub("", base.strip().lower())[: _LOGIN_ID_MAX - 4] or "user"
    candidate, number = stem, 1
    while get_by_login_id(db, candidate) is not None:
        number += 1
        candidate = f"{stem}{number}"
    return candidate


def count_active_super_admins(db: Session, exclude_user_id: str | None = None) -> int:
    query = (
        select(func.count())
        .select_from(User)
        .where(User.role == "super_admin", User.is_active.is_(True))
    )
    if exclude_user_id:
        query = query.where(User.id != exclude_user_id)
    return int(db.scalar(query) or 0)


def any_user(db: Session) -> bool:
    return db.scalars(select(User.id).limit(1)).first() is not None


def list_all(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at, User.email)))


def create(
    db: Session,
    *,
    email: str,
    full_name: str,
    password_hash: str,
    role: str,
    departments: Iterable[str],
    login_id: str | None = None,
    must_change_password: bool = False,
    created_by: str | None = None,
) -> User:
    email = email.strip().lower()
    user = User(
        email=email,
        login_id=unique_login_id(db, login_id or email.split("@")[0]),
        full_name=full_name,
        password_hash=password_hash,
        role=role,
        must_change_password=must_change_password,
        created_by=created_by,
    )
    set_departments(user, departments)
    db.add(user)
    db.flush()
    return user


def set_departments(user: User, departments: Iterable[str]) -> None:
    """Make the user's departments exactly `departments`. Only the difference is written: clearing
    and re-adding would insert a department before its old row is deleted and break the unique
    constraint."""
    wanted = set(departments)
    for row in list(user.departments):
        if row.department not in wanted:
            user.departments.remove(row)
    present = {row.department for row in user.departments}
    for code in sorted(wanted - present):
        user.departments.append(UserDepartment(department=code))


# ----------------------------------------------------------------------------- refresh tokens


def add_refresh_token(
    db: Session, user_id: str, token_hash: str, expires_at: datetime
) -> RefreshToken:
    token = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
    db.add(token)
    db.flush()
    return token


def get_refresh_token(db: Session, token_hash: str) -> RefreshToken | None:
    return db.scalars(select(RefreshToken).where(RefreshToken.token_hash == token_hash)).first()


def revoke_all_refresh_tokens(db: Session, user_id: str) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
