"""Create the very first Super Admin from the web page.

This is the only way to get an account without already having one, so it is locked down:
it works only while no user exists, in production it needs a SETUP_TOKEN from `.env`, wrong
tokens are rate limited, and every attempt is audited. After the first account exists the
endpoint is permanently closed (more accounts are made by a Super Admin on the Users page).
"""

import hmac
import threading

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.permissions import Role
from app.core.rate_limit import LoginLimiter
from app.models.user import User
from app.repositories import users as repo
from app.schemas.auth import SetupRequest, SetupStatus, UserCreate
from app.services import audit
from app.services import users as user_service
from app.services.auth import AuthError

_lock = threading.Lock()  # one API process, so two simultaneous setups can't both pass the check


def _expected_token(settings: Settings) -> str:
    return settings.setup_token.get_secret_value() if settings.setup_token else ""


def status(db: Session, settings: Settings) -> SetupStatus:
    required = not repo.any_user(db)
    has_token = bool(_expected_token(settings))
    return SetupStatus(
        setup_required=required,
        token_required=required and has_token,
        available=required and (has_token or settings.app_env != "production"),
    )


def create_first_admin(
    db: Session,
    settings: Settings,
    limiter: LoginLimiter,
    client_address: str,
    data: SetupRequest,
) -> User:
    key = f"setup:{client_address}"
    if wait := limiter.retry_after(key):
        raise AuthError("too_many_attempts", status=429, retry_after=wait)
    with _lock:
        state = status(db, settings)
        if not state.setup_required:
            raise AuthError("setup_already_done", status=409)
        if not state.available:
            raise AuthError("setup_disabled", status=403)
        if state.token_required and not hmac.compare_digest(
            data.setup_token.encode(), _expected_token(settings).encode()
        ):
            limiter.record_failure(key)
            audit.record(db, "setup_admin", "denied")
            raise AuthError("invalid_setup_token", status=403)
        try:
            user = user_service.create_user(
                db,
                settings,
                None,
                UserCreate(
                    email=data.email,
                    full_name=data.full_name,
                    password=data.password,
                    role=Role.SUPER_ADMIN,
                    departments=[],
                ),
            )
        except user_service.UserError as exc:
            raise AuthError(exc.reason, status=exc.status) from exc
    limiter.reset(key)
    audit.record(db, "setup_admin", "ok", user_id=user.id, target_type="user", target_id=user.id)
    return user
