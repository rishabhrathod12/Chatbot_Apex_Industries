from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.permissions import Principal
from app.core.rate_limit import LoginLimiter
from app.core.security import TokenError, decode_access_token
from app.rag.generation import ChatModel, get_chat_model
from app.rag.ingestion.embedder import Embedder, get_embedder
from app.rag.ingestion.pipeline import EmbedderProvider, Scope, StoreProvider
from app.rag.vectorstore import QdrantVectorStore, VectorStore, collection_name
from app.repositories import users as users_repo
from app.services import audit
from app.services.auth import principal_of

# ----------------------------------------------------------------------------- who is asking

_bearer = HTTPBearer(auto_error=False)


def _unauthorized(reason: str) -> HTTPException:
    return HTTPException(status_code=401, detail=reason, headers={"WWW-Authenticate": "Bearer"})


def get_principal_allow_pending(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Principal:
    """The logged-in user, even one who still has to choose a new password. Only the few
    endpoints that let them do that (and sign out) use this; everything else uses
    `get_current_principal`.

    The token only says who the user is. Role and departments are read from the database on every
    request, so removing someone's access, or disabling their account, takes effect immediately.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("not_authenticated")
    try:
        user_id = decode_access_token(credentials.credentials, settings)
    except TokenError as exc:
        if exc.reason == "auth_not_configured":
            raise HTTPException(status_code=503, detail="auth_not_configured") from exc
        raise _unauthorized(exc.reason) from exc
    user = users_repo.get(db, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("invalid_token")
    return principal_of(user)


def get_current_principal(
    principal: Principal = Depends(get_principal_allow_pending),
) -> Principal:
    """The logged-in user, or a 401. Every endpoint that is not public depends on this.

    Someone with a temporary password (just created, or just reset by an admin) gets a 403 on
    everything until they have chosen their own.
    """
    if principal.must_change_password:
        raise HTTPException(status_code=403, detail="password_change_required")
    return principal


def get_current_user_id(principal: Principal = Depends(get_current_principal)) -> str:
    return principal.user_id


def require_admin(
    principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
) -> Principal:
    if not principal.is_admin:
        audit.record(db, "admin_endpoint", "denied", user_id=principal.user_id)
        raise HTTPException(status_code=403, detail="not_allowed")
    return principal


def get_login_limiter(request: Request) -> LoginLimiter:
    limiter: LoginLimiter = request.app.state.login_limiter
    return limiter


# ----------------------------------------------------------------------------- services


def get_embedder_provider(settings: Settings = Depends(get_settings)) -> EmbedderProvider:
    """A callable rather than an embedder, so a missing API key fails the *ingestion* (recorded on
    the document) and does not break the upload request itself."""

    def provider() -> Embedder:
        return get_embedder(settings)

    return provider


def get_store_provider(settings: Settings = Depends(get_settings)) -> StoreProvider:
    def provider(scope: Scope, dimensions: int) -> VectorStore:
        return QdrantVectorStore(settings.vector_store_path, collection_name(scope), dimensions)

    return provider


ChatModelProvider = Callable[[], ChatModel]


def get_chat_model_provider(settings: Settings = Depends(get_settings)) -> ChatModelProvider:
    """Callable for the same reason as the embedder: a missing key becomes a clean 503."""

    def provider() -> ChatModel:
        return get_chat_model(settings)

    return provider
