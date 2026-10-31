import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.permissions import Principal, Role
from app.core.rate_limit import LoginLimiter
from app.core.security import (
    burn_password_check,
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.models.user import User
from app.repositories import users as repo
from app.schemas.auth import PrincipalOut, TokenPair
from app.services import audit
from app.services import users as user_service

logger = logging.getLogger("app.auth")


class AuthError(Exception):
    """Authentication failed. `reason` is a short code, `status` the HTTP status to use."""

    def __init__(self, reason: str, status: int = 401, retry_after: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.retry_after = retry_after


def principal_of(user: User) -> Principal:
    return Principal(
        user_id=user.id,
        login_id=user.login_id,
        email=user.email,
        full_name=user.full_name,
        role=Role(user.role),
        departments=user.department_codes,
        must_change_password=user.must_change_password,
    )


def principal_out(principal: Principal) -> PrincipalOut:
    return PrincipalOut(
        user_id=principal.user_id,
        login_id=principal.login_id,
        email=principal.email,
        full_name=principal.full_name,
        role=principal.role,
        departments=sorted(principal.departments),
        must_change_password=principal.must_change_password,
    )


def _issue(db: Session, user: User, settings: Settings) -> TokenPair:
    access = create_access_token(user.id, settings)
    raw, hashed = new_refresh_token()
    expires = datetime.now(UTC) + timedelta(hours=settings.refresh_token_hours)
    repo.add_refresh_token(db, user.id, hashed, expires)
    db.commit()
    principal = principal_of(user)
    return TokenPair(
        access_token=access.token,
        refresh_token=raw,
        expires_in=access.expires_in,
        user=principal_out(principal),
    )


def login(
    db: Session,
    settings: Settings,
    limiter: LoginLimiter,
    *,
    identifier: str,
    password: str,
    client_address: str,
) -> TokenPair:
    """Check a password and start a session.

    Wrong email, wrong password and a disabled account all give the same answer, and the same
    amount of work, so a caller cannot tell which emails exist.
    """
    user = repo.get_by_identifier(db, identifier)
    # One lockout counter per person, whether they type their Login ID or their email.
    key = f"{client_address}|{user.id if user else identifier}"
    wait = limiter.retry_after(key)
    if wait:
        audit.record(
            db, "login", "denied", target_type="email", target_id=audit.fingerprint(identifier)
        )
        raise AuthError("too_many_attempts", status=429, retry_after=wait)

    if user is None:
        burn_password_check(password, settings.password_hash_cost_log2)
    valid = user is not None and verify_password(password, user.password_hash)
    if user is None or not valid or not user.is_active:
        limiter.record_failure(key)
        audit.record(
            db,
            "login",
            "failed",
            user_id=user.id if user else None,
            target_type="email",
            target_id=audit.fingerprint(identifier),
        )
        raise AuthError("invalid_credentials")

    limiter.reset(key)
    audit.record(db, "login", "ok", user_id=user.id)
    return _issue(db, user, settings)


def refresh(db: Session, settings: Settings, raw_token: str) -> TokenPair:
    """Swap a refresh token for a new pair. Each token works once. If an already-used token is
    presented again, someone may have copied it, so every session of that user is ended."""
    stored = repo.get_refresh_token(db, hash_refresh_token(raw_token))
    if stored is None:
        raise AuthError("invalid_refresh_token")

    if stored.revoked_at is not None:
        repo.revoke_all_refresh_tokens(db, stored.user_id)
        db.commit()
        audit.record(db, "refresh_token_reuse", "denied", user_id=stored.user_id)
        raise AuthError("invalid_refresh_token")

    if repo.aware(stored.expires_at) <= datetime.now(UTC):
        raise AuthError("token_expired")

    user = repo.get(db, stored.user_id)
    if user is None or not user.is_active:
        raise AuthError("invalid_refresh_token")

    stored.revoked_at = datetime.now(UTC)
    pair = _issue(db, user, settings)
    audit.record(db, "refresh", "ok", user_id=user.id)
    return pair


def logout(db: Session, raw_token: str) -> None:
    """End one session. Quietly does nothing for an unknown token."""
    stored = repo.get_refresh_token(db, hash_refresh_token(raw_token))
    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = datetime.now(UTC)
        db.commit()
        audit.record(db, "logout", "ok", user_id=stored.user_id)


def change_password(
    db: Session,
    settings: Settings,
    limiter: LoginLimiter,
    principal: Principal,
    *,
    current_password: str,
    new_password: str,
) -> TokenPair:
    """A signed-in person chooses a new password. This is also how a temporary password is
    replaced. All their other sessions end and a fresh one is returned."""
    user = repo.get(db, principal.user_id)
    if user is None or not user.is_active:
        raise AuthError("invalid_token")

    # A stolen session must not be able to guess the current password without limit.
    key = f"password-change|{user.id}"
    if wait := limiter.retry_after(key):
        audit.record(db, "password_change", "denied", user_id=user.id)
        raise AuthError("too_many_attempts", status=429, retry_after=wait)
    if not verify_password(current_password, user.password_hash):
        limiter.record_failure(key)
        audit.record(db, "password_change", "failed", user_id=user.id)
        # 422, not 401: the app treats a 401 as "your session expired".
        raise AuthError("current_password_incorrect", status=422)
    limiter.reset(key)

    try:
        user_service.check_password(new_password, settings)
    except user_service.UserError as exc:
        raise AuthError(exc.reason, status=422) from exc
    if verify_password(new_password, user.password_hash):
        raise AuthError("password_unchanged", status=422)

    user.password_hash = hash_password(new_password, settings.password_hash_cost_log2)
    user.must_change_password = False
    user.password_changed_at = datetime.now(UTC)
    repo.revoke_all_refresh_tokens(db, user.id)
    db.commit()
    audit.record(db, "password_change", "ok", user_id=user.id)
    return _issue(db, user, settings)
