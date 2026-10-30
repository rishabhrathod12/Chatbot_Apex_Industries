from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.departments import canonical_department
from app.core.dev_user import DEV_USER_ID
from app.repositories import documents as repo
from app.schemas.document import DocumentOut, UploadResponse
from app.services import documents as service

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=UploadResponse)
def upload_documents(
    response: Response,
    files: list[UploadFile],
    scope: Annotated[Literal["personal", "enterprise"], Form()],
    department: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
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
            db, f, scope=scope, owner_id=DEV_USER_ID, department=dept, settings=settings
        )
        for f in files
    ]

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


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    document = repo.get(db, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="not_found")
    service.delete_document(db, document, settings)
    return Response(status_code=204)
