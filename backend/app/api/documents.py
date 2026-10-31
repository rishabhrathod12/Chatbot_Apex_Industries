import logging
import re
from pathlib import Path
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

from app.api.deps import get_current_principal, get_embedder_provider, get_store_provider
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.departments import canonical_department
from app.core.document_types import DOCUMENT_TYPES
from app.core.permissions import (
    Principal,
    can_add_company_document,
    can_manage_document,
    can_read_document,
)
from app.models.document import Document
from app.rag.ingestion.embedder import embedding_dimensions
from app.rag.ingestion.pipeline import (
    EmbedderProvider,
    Scope,
    StoreProvider,
    replace_document,
    run_ingestion,
)
from app.repositories import documents as repo
from app.schemas.document import DocumentOut, UploadResponse, UploadResult
from app.services import audit
from app.services import documents as service
from app.services.file_validation import sanitize_filename

logger = logging.getLogger("app.documents")
router = APIRouter(prefix="/documents", tags=["documents"])


def _visible_or_404(db: Session, principal: Principal, document_id: str) -> Document:
    """A document the user may read, or 404. A document they may not read looks exactly like one
    that does not exist, so ids cannot be probed."""
    document = repo.get(db, document_id)
    if document is None or not can_read_document(principal, document):
        if document is not None:
            audit.record(
                db,
                "document_access",
                "denied",
                user_id=principal.user_id,
                target_type="document",
                target_id=document_id,
            )
        raise HTTPException(status_code=404, detail="not_found")
    return document


def _manageable_or_403(db: Session, principal: Principal, document: Document, action: str) -> None:
    if not can_manage_document(principal, document):
        audit.record(
            db,
            action,
            "denied",
            user_id=principal.user_id,
            target_type="document",
            target_id=document.id,
        )
        raise HTTPException(status_code=403, detail="not_allowed")


def _allowed_for_personal(filename: str | None, settings: Settings) -> bool:
    return Path(sanitize_filename(filename)).suffix.lower() in settings.personal_upload_extensions


_VERSION = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


def _clean_metadata(
    title: str | None, description: str | None, document_type: str | None, version: str | None
) -> dict[str, str]:
    """The details a Super Admin types for a company document. Blank means "not given"."""
    given = {
        "title": (title or "").strip(),
        "description": (description or "").strip(),
        "document_type": (document_type or "").strip().lower(),
        "version": (version or "").strip(),
    }
    given = {k: v for k, v in given.items() if v}
    if len(given.get("title", "")) > 255 or len(given.get("description", "")) > 2000:
        raise HTTPException(status_code=422, detail="metadata_too_long")
    if "document_type" in given and given["document_type"] not in DOCUMENT_TYPES:
        raise HTTPException(status_code=422, detail="invalid_document_type")
    if "version" in given and not _VERSION.match(given["version"]):
        raise HTTPException(status_code=422, detail="invalid_version")
    return given


@router.post("", response_model=UploadResponse)
def upload_documents(
    response: Response,
    background: BackgroundTasks,
    files: list[UploadFile],
    scope: Annotated[Literal["personal", "enterprise"], Form()],
    department: Annotated[str | None, Form()] = None,
    visibility: Annotated[Literal["department", "company"], Form()] = "department",
    title: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
    document_type: Annotated[str | None, Form()] = None,
    version: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    embedder_provider: EmbedderProvider = Depends(get_embedder_provider),
    store_provider: StoreProvider = Depends(get_store_provider),
    principal: Principal = Depends(get_current_principal),
) -> UploadResponse:
    if not files:
        raise HTTPException(status_code=422, detail="no_files")
    if len(files) > settings.max_files_per_request:
        raise HTTPException(status_code=400, detail="too_many_files")

    metadata = _clean_metadata(title, description, document_type, version)
    if metadata and scope != "enterprise":
        raise HTTPException(status_code=422, detail="metadata_for_company_documents_only")
    if (metadata.get("title") or metadata.get("version")) and len(files) > 1:
        raise HTTPException(status_code=422, detail="title_and_version_need_a_single_file")

    if scope == "enterprise":
        dept = canonical_department(department)
        if dept is None:
            raise HTTPException(status_code=422, detail="invalid_department")
        if not can_add_company_document(principal, dept):
            audit.record(
                db,
                "document_upload",
                "denied",
                user_id=principal.user_id,
                target_type="department",
                target_id=dept,
            )
            raise HTTPException(status_code=403, detail="not_allowed")
        stored_visibility: str = visibility
    else:
        dept = ""
        stored_visibility = "personal"

    def handle(f: UploadFile) -> UploadResult:
        if scope == "personal" and not _allowed_for_personal(f.filename, settings):
            return UploadResult(
                filename=sanitize_filename(f.filename), status="rejected", reason="pdf_only"
            )
        return service.store_upload(
            db,
            f,
            scope=scope,
            owner_id=principal.user_id,
            department=dept,
            visibility=stored_visibility,
            settings=settings,
            **metadata,
        )

    results = [handle(f) for f in files]

    for result in results:
        if result.status == "accepted" and result.document is not None:
            audit.record(
                db,
                "document_upload",
                "ok",
                user_id=principal.user_id,
                target_type="document",
                target_id=result.document.id,
            )
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
def list_documents(
    db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal)
) -> list[DocumentOut]:
    return [DocumentOut.model_validate(d) for d in repo.list_visible(db, principal)]


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> DocumentOut:
    return DocumentOut.model_validate(_visible_or_404(db, principal, document_id))


@router.post("/{document_id}/reindex", response_model=DocumentOut, status_code=202)
def reindex_document(
    document_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    embedder_provider: EmbedderProvider = Depends(get_embedder_provider),
    store_provider: StoreProvider = Depends(get_store_provider),
    principal: Principal = Depends(get_current_principal),
) -> DocumentOut:
    document = _visible_or_404(db, principal, document_id)
    _manageable_or_403(db, principal, document, "document_reindex")
    if document.status == "archived":
        raise HTTPException(status_code=409, detail="archived_restore_it_first")
    if document.status == "processing":
        raise HTTPException(status_code=409, detail="already_processing")
    background.add_task(run_ingestion, document.id, settings, embedder_provider, store_provider)
    audit.record(
        db,
        "document_reindex",
        "ok",
        user_id=principal.user_id,
        target_type="document",
        target_id=document.id,
    )
    return DocumentOut.model_validate(document)


@router.post("/{document_id}/versions", response_model=DocumentOut, status_code=202)
def upload_new_version(
    document_id: str,
    file: UploadFile,
    background: BackgroundTasks,
    title: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
    document_type: Annotated[str | None, Form()] = None,
    version: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    embedder_provider: EmbedderProvider = Depends(get_embedder_provider),
    store_provider: StoreProvider = Depends(get_store_provider),
    principal: Principal = Depends(get_current_principal),
) -> DocumentOut:
    """Replace a company document with a new file. The new version is indexed in the background;
    the old one stays searchable until the new one is ready, then is archived (never a moment
    with neither). If the new file cannot be indexed the old one stays as it was."""
    old = _visible_or_404(db, principal, document_id)
    _manageable_or_403(db, principal, old, "document_version_upload")
    if old.scope != "enterprise":
        raise HTTPException(status_code=409, detail="only_company_documents_have_versions")
    if old.status == "archived":
        raise HTTPException(status_code=409, detail="archived_restore_it_first")
    if old.status in {"uploaded", "processing"} or repo.replacement_in_progress(db, old):
        raise HTTPException(status_code=409, detail="still_processing")

    given = _clean_metadata(title, description, document_type, version)
    if given.get("version") and given["version"] == (old.version_override or old.doc_version):
        raise HTTPException(status_code=422, detail="same_version")
    details = {
        "title": given.get("title") or old.title_override,
        "description": given.get("description") or old.description,
        "document_type": given.get("document_type") or old.document_type,
        "version": given.get("version"),  # the old version's label is not inherited
    }

    result = service.store_upload(
        db,
        file,
        scope="enterprise",
        owner_id=principal.user_id,
        department=old.department,
        visibility=old.visibility,
        settings=settings,
        **details,
    )
    if result.status == "duplicate":
        same = result.document is not None and result.document.id == old.id
        raise HTTPException(
            status_code=409, detail="same_file" if same else "duplicate_of_another_document"
        )
    if result.status == "rejected" or result.document is None:
        status = 413 if result.reason == "file_too_large" else 422
        raise HTTPException(status_code=status, detail=result.reason or "rejected")

    new = repo.get(db, result.document.id)
    assert new is not None
    new.supersedes_id = old.id
    db.commit()
    audit.record(
        db,
        "document_version_upload",
        "ok",
        user_id=principal.user_id,
        target_type="document",
        target_id=new.id,
        detail=f"replaces:{old.id}",
    )
    background.add_task(
        replace_document,
        new.id,
        old.id,
        principal.user_id,
        settings,
        embedder_provider,
        store_provider,
    )
    return DocumentOut.model_validate(new)


@router.get("/{document_id}/versions", response_model=list[DocumentOut])
def list_versions(
    document_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> list[DocumentOut]:
    """This document and the versions before and after it, newest first. Versions the user is
    not allowed to see (such as archived ones, for an employee) are left out."""
    document = _visible_or_404(db, principal, document_id)
    return [
        DocumentOut.model_validate(d)
        for d in repo.version_chain(db, document)
        if can_read_document(principal, d)
    ]


@router.post("/{document_id}/archive", response_model=DocumentOut)
def archive_document(
    document_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    store_provider: StoreProvider = Depends(get_store_provider),
    principal: Principal = Depends(get_current_principal),
) -> DocumentOut:
    """Take a company document out of use. Its vectors are removed first, so it can no longer be
    searched or cited; the file and its details are kept so it can be restored."""
    document = _visible_or_404(db, principal, document_id)
    _manageable_or_403(db, principal, document, "document_archive")
    if document.scope != "enterprise":
        raise HTTPException(status_code=409, detail="only_company_documents_can_be_archived")
    if document.status == "archived":
        return DocumentOut.model_validate(document)
    if document.status in {"uploaded", "processing"}:
        raise HTTPException(status_code=409, detail="still_processing")

    try:
        store_provider("enterprise", embedding_dimensions(settings)).delete_document(document.id)
    except Exception as exc:
        logger.exception("vector delete failed", extra={"document_id": document.id})
        raise HTTPException(status_code=500, detail="vector_delete_failed") from exc

    repo.archive(db, document)
    audit.record(
        db,
        "document_archive",
        "ok",
        user_id=principal.user_id,
        target_type="document",
        target_id=document.id,
    )
    return DocumentOut.model_validate(document)


@router.post("/{document_id}/restore", response_model=DocumentOut, status_code=202)
def restore_document(
    document_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    embedder_provider: EmbedderProvider = Depends(get_embedder_provider),
    store_provider: StoreProvider = Depends(get_store_provider),
    principal: Principal = Depends(get_current_principal),
) -> DocumentOut:
    """Bring an archived company document back: it is indexed again and becomes searchable
    once it shows as ready."""
    document = _visible_or_404(db, principal, document_id)
    _manageable_or_403(db, principal, document, "document_restore")
    if document.status != "archived":
        raise HTTPException(status_code=409, detail="not_archived")

    repo.unarchive(db, document)
    background.add_task(run_ingestion, document.id, settings, embedder_provider, store_provider)
    audit.record(
        db,
        "document_restore",
        "ok",
        user_id=principal.user_id,
        target_type="document",
        target_id=document.id,
    )
    return DocumentOut.model_validate(document)


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    store_provider: StoreProvider = Depends(get_store_provider),
    principal: Principal = Depends(get_current_principal),
) -> Response:
    document = _visible_or_404(db, principal, document_id)
    _manageable_or_403(db, principal, document, "document_delete")

    # Vectors go first. If that fails the document stays fully intact, so nothing can end up
    # searchable after the user has deleted it.
    scope: Scope = "enterprise" if document.scope == "enterprise" else "personal"
    try:
        store_provider(scope, embedding_dimensions(settings)).delete_document(document.id)
    except Exception as exc:
        logger.exception("vector delete failed", extra={"document_id": document.id})
        raise HTTPException(status_code=500, detail="vector_delete_failed") from exc

    service.delete_document(db, document, settings)
    audit.record(
        db,
        "document_delete",
        "ok",
        user_id=principal.user_id,
        target_type="document",
        target_id=document_id,
    )
    return Response(status_code=204)
