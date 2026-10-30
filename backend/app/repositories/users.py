from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.user import RefreshToken, User, UserDepartment


def aware(moment: datetime) -> datetime:
    """SQLite hands datetimes back without a time zone. Everything we store is UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def get(db: Session, user_id: str) -> User | None:
    return db.get(User, user_id)


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalars(select(User).where(User.email == email.strip().lower())).first()


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
) -> User:
    user = User(
        email=email.strip().lower(), full_name=full_name, password_hash=password_hash, role=role
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
