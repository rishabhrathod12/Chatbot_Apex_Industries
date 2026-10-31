import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.database import get_engine

logger = logging.getLogger("app.health")

router = APIRouter(tags=["health"])


# Bumped when the API changes in a way the web pages depend on. The page compares it with the
# level it needs and says so plainly if the API running on the machine is an older copy.
API_LEVEL = 2


@router.get("/health")
def health() -> dict[str, str | int]:
    """Liveness: the process is up and answering. Does not touch anything else."""
    return {"status": "ok", "api_level": API_LEVEL}


@router.get("/health/ready", response_model=None)
def ready(settings: Settings = Depends(get_settings)) -> JSONResponse:
    """Readiness: the things a request needs are reachable. Reports short codes, never details."""
    checks = {"database": _database_ok(settings), "storage": _storage_ok(settings)}
    healthy = all(checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "unavailable",
            "checks": {name: "ok" if ok else "failed" for name, ok in checks.items()},
        },
    )


def _database_ok(settings: Settings) -> bool:
    try:
        with get_engine(settings.database_url).connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # any failure means "not ready"; log the type only
        logger.error("database check failed", extra={"error_type": type(exc).__name__})
        return False
    return True


def _storage_ok(settings: Settings) -> bool:
    """Uploads and the vector index live on disk; both folders must be there and writable.

    A fresh deployment has neither folder yet (they are normally created on first use), so they
    are created here rather than reporting a new, healthy install as broken.
    """
    try:
        for path in (settings.upload_dir, settings.vector_store_path):
            Path(path).mkdir(parents=True, exist_ok=True)
            if not os.access(path, os.W_OK):
                return False
    except OSError as exc:
        logger.error("storage check failed", extra={"error_type": type(exc).__name__})
        return False
    return True
