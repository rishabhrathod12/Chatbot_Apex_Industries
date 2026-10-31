"""Who may see and change what. Every access decision in the app goes through this module.

Rules
-----
Super Admin       sees and manages every company document, and can create users.
Department Manager sees their department(s) plus company-wide documents. Manages documents of
                  their own department(s) and may publish them company-wide.
Employee          sees their department(s) plus company-wide documents. Adds personal ones only.
Everyone          sees only their own personal documents. Not even a Super Admin reads someone
                  else's personal files or conversations.

A document is either "department" (visible to that department) or "company" (visible to every
logged-in user). Personal documents are always private to their owner.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from app.models.document import Document
from app.rag.vectorstore import SearchFilter

Visibility = Literal["personal", "department", "company"]
Scope = Literal["personal", "enterprise"]


class Role(StrEnum):
    SUPER_ADMIN = "super_admin"
    DEPARTMENT_MANAGER = "department_manager"
    EMPLOYEE = "employee"


@dataclass(frozen=True)
class Principal:
    """The logged-in user, as loaded from the database for this request."""

    user_id: str
    email: str
    full_name: str
    role: Role
    departments: frozenset[str]
    login_id: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role is Role.SUPER_ADMIN

    @property
    def is_manager(self) -> bool:
        return self.role is Role.DEPARTMENT_MANAGER


# ----------------------------------------------------------------------------- documents


def can_read_document(principal: Principal, document: Document) -> bool:
    if document.scope == "personal":
        return document.owner_id == principal.user_id
    return (
        principal.is_admin
        or document.visibility == "company"
        or document.department in principal.departments
    )


def can_manage_document(principal: Principal, document: Document) -> bool:
    """Re-index or delete."""
    if document.scope == "personal":
        return document.owner_id == principal.user_id
    if principal.is_admin:
        return True
    return principal.is_manager and document.department in principal.departments


def can_add_company_document(principal: Principal, department: str) -> bool:
    """Add a document to the company library under `department`."""
    if principal.is_admin:
        return True
    return principal.is_manager and department in principal.departments


# ----------------------------------------------------------------------------- search


def build_search_filter(
    principal: Principal, scope: Scope, narrow_to_department: str | None = None
) -> SearchFilter | None:
    """The one place that decides which chunks a user's question may be answered from.

    `narrow_to_department` can only shrink the result (search just one department's documents).
    It can never reveal anything the user could not already see.
    """
    if scope == "personal":
        return SearchFilter(owner_id=principal.user_id)
    if principal.is_admin:
        return (
            SearchFilter(narrow_department=narrow_to_department) if narrow_to_department else None
        )
    return SearchFilter(
        departments=sorted(principal.departments),
        include_company_wide=True,
        narrow_department=narrow_to_department,
    )
