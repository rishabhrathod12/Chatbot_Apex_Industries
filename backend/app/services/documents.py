import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import BinaryIO

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.document import Document
from app.repositories import documents as repo
from app.schemas.document import DocumentOut, UploadResult
from app.services.file_validation import (
    MIME_TYPES,
    UploadRejected,
    get_extension,
    sanitize_filename,
    validate_content,
)

logger = logging.getLogger("app.documents")
_CHUNK = 1024 * 1024


def dedupe_key(scope: str, owner_id: str, department: str) -> str:
    return f"enterprise:{department}" if scope == "enterprise" else f"personal:{owner_id}"


def _stream_to_temp(src: BinaryIO, tmp: Path, max_bytes: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with tmp.open("wb") as out:
        while chunk := src.read(_CHUNK):
            size += len(chunk)
            if size > max_bytes:
                raise UploadRejected("file_too_large")
            digest.update(chunk)
            out.write(chunk)
    if size == 0:
        raise UploadRejected("empty_file")
    return digest.hexdigest(), size


def store_upload(
    db: Session,
    upload: UploadFile,
    *,
    scope: str,
    owner_id: str,
    department: str,
    visibility: str,
    settings: Settings,
    title: str | None = None,
    description: str | None = None,
    document_type: str | None = None,
    version: str | None = None,
) -> UploadResult:
    filename = sanitize_filename(upload.filename)
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    tmp = upload_dir / f".tmp-{uuid.uuid4().hex}"
    try:
        ext = get_extension(filename)
        file_hash, size = _stream_to_temp(upload.file, tmp, settings.max_upload_mb * 1024 * 1024)
        key = dedupe_key(scope, owner_id, department)

        existing = repo.find_duplicate(db, key, file_hash)
        if existing:
            logger.info("duplicate upload", extra={"document_id": existing.id})
            return UploadResult(
                filename=filename,
                status="duplicate",
                document=DocumentOut.model_validate(existing),
            )

        validate_content(tmp, ext)

        stored_filename = f"{uuid.uuid4().hex}{ext}"
        final_path = upload_dir / stored_filename
        os.replace(tmp, final_path)
        document = Document(
            scope=scope,
            owner_id=owner_id,
            department=department,
            visibility=visibility,
            dedupe_key=key,
            original_filename=filename,
            stored_filename=stored_filename,
            file_ext=ext,
            mime_type=MIME_TYPES[ext],
            size_bytes=size,
            file_hash=file_hash,
            title_override=title,
            description=description,
            document_type=document_type,
            version_override=version,
        )
        try:
            repo.add(db, document)
        except Exception:
            db.rollback()
            final_path.unlink(missing_ok=True)
            raise
        logger.info("document stored", extra={"document_id": document.id, "size_bytes": size})
        return UploadResult(
            filename=filename, status="accepted", document=DocumentOut.model_validate(document)
        )
    except UploadRejected as exc:
        logger.info("upload rejected", extra={"reason": exc.reason})
        return UploadResult(filename=filename, status="rejected", reason=exc.reason)
    finally:
        tmp.unlink(missing_ok=True)


def delete_document(db: Session, document: Document, settings: Settings) -> None:
    (Path(settings.upload_dir) / document.stored_filename).unlink(missing_ok=True)
    repo.soft_delete(db, document)
    logger.info("document deleted", extra={"document_id": document.id})
