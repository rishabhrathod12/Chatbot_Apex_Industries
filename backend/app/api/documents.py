import logging
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Response,
    UploadFile,
)
from sqlalchemy.orm import Session

from app.api.deps import get_current_user_id, get_embedder_provider, get_store_provider
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.departments import canonical_department
from app.rag.ingestion.embedder import embedding_dimensions
from app.rag.ingestion.pipeline import EmbedderProvider, Scope, StoreProvider, run_ingestion
from app.repositories import documents as repo
from app.schemas.document import DocumentOut, UploadResponse
from app.services import documents as service

logger = logging.getLogger("app.documents")
router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=UploadResponse)
def upload_documents(
    response: Response,
    background: BackgroundTasks,
    files: list[UploadFile],
    scope: Annotated[Literal["personal", "enterprise"], Form()],
    department: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    embedder_provider: EmbedderProvider = Depends(get_embedder_provider),
    store_provider: StoreProvider = Depends(get_store_provider),
    user_id: str = Depends(get_current_user_id),
) -> UploadResponse:
    if not files:
        raise HTTPException(status_code=422, detail="no_files")
    if len(files) > settings.max_files_per_request:
        raise HTTPException(status_code=400, detail="too_many_files")

    if scope == "enterprise":
        dept = canonical_department(department)
        if dept is None:
            raise HTTPException(status_code=422, detail="invalid_department")
    else:
        dept = ""

    results = [
        service.store_upload(
            db, f, scope=scope, owner_id=user_id, department=dept, settings=settings
        )
        for f in files
    ]

    for result in results:
        if result.status == "accepted" and result.document is not None:
            background.add_task(
                run_ingestion, result.document.id, settings, embedder_provider, store_provider
            )

    statuses = {r.status for r in results}
    if "accepted" in statuses:
        response.status_code = 201
    elif statuses == {"rejected"}:
        reasons = {r.reason for r in results}
        response.status_code = 413 if reasons == {"file_too_large"} else 422
    return UploadResponse(results=results)


@router.get("", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db)) -> list[DocumentOut]:
    return [DocumentOut.model_validate(d) for d in repo.list_all(db)]


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: str, db: Session = Depends(get_db)) -> DocumentOut:
    document = repo.get(db, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="not_found")
    return DocumentOut.model_validate(document)


@router.post("/{document_id}/reindex", response_model=DocumentOut, status_code=202)
def reindex_document(
    document_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    embedder_provider: EmbedderProvider = Depends(get_embedder_provider),
    store_provider: StoreProvider = Depends(get_store_provider),
) -> DocumentOut:
    document = repo.get(db, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="not_found")
    if document.status == "processing":
        raise HTTPException(status_code=409, detail="already_processing")
    background.add_task(run_ingestion, document.id, settings, embedder_provider, store_provider)
    return DocumentOut.model_validate(document)


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    store_provider: StoreProvider = Depends(get_store_provider),
) -> Response:
    document = repo.get(db, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="not_found")

    # Vectors go first. If that fails the document stays fully intact, so nothing can end up
    # searchable after the user has deleted it.
    scope: Scope = "enterprise" if document.scope == "enterprise" else "personal"
    try:
        store_provider(scope, embedding_dimensions(settings)).delete_document(document.id)
    except Exception as exc:
        logger.exception("vector delete failed", extra={"document_id": document.id})
        raise HTTPException(status_code=500, detail="vector_delete_failed") from exc

    service.delete_document(db, document, settings)
    return Response(status_code=204)
