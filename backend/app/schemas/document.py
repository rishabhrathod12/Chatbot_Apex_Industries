from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scope: str
    department: str
    visibility: str
    original_filename: str
    file_ext: str
    size_bytes: int
    status: str
    status_reason: str | None = None
    chunk_count: int = 0
    page_count: int | None = None
    doc_code: str | None = None
    title: str | None = None
    doc_version: str | None = None
    description: str | None = None
    document_type: str | None = None
    archived_at: datetime | None = None
    created_at: datetime


class UploadResult(BaseModel):
    filename: str
    status: Literal["accepted", "duplicate", "rejected"]
    reason: str | None = None
    document: DocumentOut | None = None


class UploadResponse(BaseModel):
    results: list[UploadResult]
