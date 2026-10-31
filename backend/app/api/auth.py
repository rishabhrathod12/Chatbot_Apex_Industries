from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import (
    get_login_limiter,
    get_principal_allow_pending,
)
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.permissions import Principal
from app.core.rate_limit import LoginLimiter
from app.core.security import TokenError
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    PrincipalOut,
    RefreshRequest,
    SetupRequest,
    SetupStatus,
    TokenPair,
)
from app.services import auth as auth_service
from app.services import setup as setup_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _as_http(exc: auth_service.AuthError) -> HTTPException:
    headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else {}
    if exc.retry_after:
        headers["Retry-After"] = str(exc.retry_after)
    return HTTPException(status_code=exc.status, detail=exc.reason, headers=headers)


@router.post("/login", response_model=TokenPair)
def login(
    body: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    limiter: LoginLimiter = Depends(get_login_limiter),
) -> TokenPair:
    try:
        return auth_service.login(
            db,
            settings,
            limiter,
            identifier=body.identifier,
            password=body.password,
            client_address=request.client.host if request.client else "unknown",
        )
    except auth_service.AuthError as exc:
        raise _as_http(exc) from exc
    except TokenError as exc:
        raise HTTPException(status_code=503, detail=exc.reason) from exc


@router.get("/setup", response_model=SetupStatus)
def setup_status(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> SetupStatus:
    """Does the system still need its first Super Admin? Reveals nothing else."""
    return setup_service.status(db, settings)


@router.post("/setup", response_model=PrincipalOut, status_code=201)
def setup(
    body: SetupRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    limiter: LoginLimiter = Depends(get_login_limiter),
) -> PrincipalOut:
    try:
        user = setup_service.create_first_admin(
            db,
            settings,
            limiter,
            request.client.host if request.client else "unknown",
            body,
        )
    except auth_service.AuthError as exc:
        raise _as_http(exc) from exc
    principal = auth_service.principal_of(user)
    return auth_service.principal_out(principal)


@router.post("/change-password", response_model=TokenPair)
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    limiter: LoginLimiter = Depends(get_login_limiter),
    principal: Principal = Depends(get_principal_allow_pending),
) -> TokenPair:
    """Choose a new password. Allowed while a temporary password is still in force."""
    try:
        return auth_service.change_password(
            db,
            settings,
            limiter,
            principal,
            current_password=body.current_password,
            new_password=body.new_password,
        )
    except auth_service.AuthError as exc:
        raise _as_http(exc) from exc
    except TokenError as exc:
        raise HTTPException(status_code=503, detail=exc.reason) from exc


@router.post("/refresh", response_model=TokenPair)
def refresh(
    body: RefreshRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenPair:
    try:
        return auth_service.refresh(db, settings, body.refresh_token)
    except auth_service.AuthError as exc:
        raise _as_http(exc) from exc
    except TokenError as exc:
        raise HTTPException(status_code=503, detail=exc.reason) from exc


@router.post("/logout", status_code=204)
def logout(body: RefreshRequest, db: Session = Depends(get_db)) -> Response:
    auth_service.logout(db, body.refresh_token)
    return Response(status_code=204)


@router.get("/me", response_model=PrincipalOut)
def me(principal: Principal = Depends(get_principal_allow_pending)) -> PrincipalOut:
    return auth_service.principal_out(principal)
