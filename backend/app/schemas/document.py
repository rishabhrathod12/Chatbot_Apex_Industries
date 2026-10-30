from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scope: str
    department: str
    original_filename: str
    file_ext: str
    size_bytes: int
    status: str
    created_at: datetime


class UploadResult(BaseModel):
    filename: str
    status: Literal["accepted", "duplicate", "rejected"]
    reason: str | None = None
    document: DocumentOut | None = None


class UploadResponse(BaseModel):
    results: list[UploadResult]
