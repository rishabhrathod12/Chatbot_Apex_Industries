import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.api import auth, chat, conversations, documents, health, search, users
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_ctx
from app.core.rate_limit import LoginLimiter

logger = logging.getLogger("app.request")

# This is a JSON API, never a web page, so it can forbid framing, sniffing and caching outright.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
DOC_PAGES = {"/docs", "/redoc"}


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    # The interactive API pages describe every endpoint, so they are not served in production.
    show_docs = settings.app_env != "production"
    app = FastAPI(
        title="Apex Knowledge Assistant",
        version="0.1.0",
        docs_url="/docs" if show_docs else None,
        redoc_url="/redoc" if show_docs else None,
        openapi_url="/openapi.json" if show_docs else None,
    )
    app.state.login_limiter = LoginLimiter(
        settings.login_max_failures, settings.login_window_minutes * 60
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled error", extra={"path": request.url.path})
            return JSONResponse(
                status_code=500,
                content={"error": "internal_server_error", "request_id": request_id},
                headers={"X-Request-ID": request_id},
            )
        else:
            response.headers["X-Request-ID"] = request_id
            for name, value in SECURITY_HEADERS.items():
                if name == "Content-Security-Policy" and request.url.path in DOC_PAGES:
                    continue  # development-only API pages load their viewer from a CDN
                response.headers.setdefault(name, value)
            logger.info(
                "request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
            return response
        finally:
            request_id_ctx.reset(token)

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(search.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(conversations.router, prefix="/api/v1")
    return app


app = create_app()
