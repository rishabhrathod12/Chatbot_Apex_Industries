from datetime import datetime

from pydantic import BaseModel


class AccountCounts(BaseModel):
    total: int
    active: int
    inactive: int
    by_role: dict[str, int]


class DepartmentSummary(BaseModel):
    code: str
    employees: int  # active employees in the department
    managers: int  # active department managers
    documents: int  # company documents filed under it


class DocumentCounts(BaseModel):
    total: int  # company documents only; personal documents are never counted here
    by_status: dict[str, int]


class RecentUpload(BaseModel):
    document_id: str
    title: str
    filename: str
    department: str
    status: str
    uploaded_by: str
    created_at: datetime


class AuditEntry(BaseModel):
    id: int
    created_at: datetime
    action: str
    outcome: str
    actor_id: str | None
    actor_name: str | None
    actor_login_id: str | None
    target_type: str | None
    target_id: str | None
    target_label: str | None  # a person's or company document's name, when it can be shown
    detail: str | None


class StatsOut(BaseModel):
    accounts: AccountCounts
    departments: list[DepartmentSummary]
    documents: DocumentCounts
    recent_uploads: list[RecentUpload]
    recent_activity: list[AuditEntry]


class AuditPage(BaseModel):
    items: list[AuditEntry]
    total: int
    page: int
    page_size: int
    available_actions: list[str]
