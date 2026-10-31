"""Display helpers: plain functions with no Streamlit calls, so they are easy to test."""

import re
from typing import Any, Literal

from api_client import ApiError

DEPARTMENTS = {
    "production": "Production",
    "maintenance": "Maintenance",
    "hr": "HR",
    "safety": "Safety",
    "quality": "Quality Control",
    "stores": "Stores",
}

# The six fictional sample documents: (department, visible to all employees).
SAMPLE_DOCUMENTS = {
    "01_Machine_Operation_Manual.pdf": ("production", False),
    "02_Maintenance_Manual.pdf": ("maintenance", False),
    "03_Factory_Safety_Manual.pdf": ("safety", True),
    "04_Production_SOP.pdf": ("production", False),
    "05_HR_Policy.pdf": ("hr", True),
    "06_Quality_Control_SOP.pdf": ("quality", False),
}

_NO_KEY = "No OpenAI key is set. Add OPENAI_API_KEY to the .env file and restart the API."

_ERROR_TEXT = {
    "embedding_not_configured": _NO_KEY,
    "llm_not_configured": _NO_KEY,
    "llm_request_failed": "The language model could not be reached. Please try again.",
    "embedding_request_failed": "The embedding service could not be reached. Please try again.",
    "conversation_not_found": "That conversation no longer exists.",
    "conversation_too_long": "This conversation is full. Please start a new chat.",
    "invalid_department": "That department is not recognised.",
    "too_many_files": "Too many files at once. Upload fewer files per batch.",
    "not_allowed": "You do not have permission to do that.",
    "invalid_credentials": "That email and password do not match an active account.",
    "too_many_attempts": "Too many failed attempts. Please wait a few minutes and try again.",
    "auth_not_configured": "Login is not set up. Set JWT_SECRET_KEY in .env and restart the API.",
    "password_too_short": "That password is too short.",
    "email_taken": "A user with that email already exists.",
    "manager_needs_department": "A department manager needs at least one department.",
    "cannot_demote_or_disable_self": "You cannot demote or disable your own account.",
    "user_not_found": "That user no longer exists.",
    "current_password_incorrect": "The current password is not correct.",
    "password_unchanged": "Choose a password different from the current one.",
    "last_super_admin": "There must always be at least one active Super Admin.",
    "password_required": "Please enter a password.",
    "login_id_taken": "That Login ID is already used.",
    "password_change_required": "Please choose a new password first.",
    "invalid_setup_token": "That setup token is not correct.",
    "setup_already_done": "An account already exists. Please sign in.",
    "setup_disabled": "Setup from this page is switched off. See the instructions above.",
}


def setup_problem(full_name: str, email: str, password: str, repeat: str) -> str | None:
    """Mistakes we can catch before sending anything (the server checks everything again)."""
    if not full_name.strip() or not email.strip() or not password:
        return "Please fill in your name, email and password."
    if "@" not in email:
        return "That does not look like an email address."
    if password != repeat:
        return "The two passwords do not match."
    return None


_REJECT_TEXT = {
    "unsupported_type": "not a PDF, DOCX or TXT file",
    "content_mismatch": "the contents do not match the file type",
    "empty_file": "the file is empty",
    "pdf_only": "only PDF files can be added to your own library",
    "file_too_large": "the file is too large",
}

_FAILURE_TEXT = {
    "embedding_not_configured": "no OpenAI key set",
    "embedding_request_failed": "embedding service unavailable",
    "corrupt_pdf": "could not read the PDF",
    "encrypted_pdf": "the PDF is password protected",
    "no_text_layer": "scanned PDF with no text",
    "empty_document": "no text found",
    "too_many_pages": "too many pages",
    "no_indexable_content": "nothing to index",
    "internal_error": "unexpected error",
}


def error_message(error: ApiError) -> str:
    return _ERROR_TEXT.get(error.detail, f"Something went wrong ({error.detail}).")


def reject_message(reason: str | None) -> str:
    return _REJECT_TEXT.get(reason or "", reason or "rejected")


def failure_message(reason: str | None) -> str:
    return _FAILURE_TEXT.get(reason or "", reason or "failed")


def page_label(start: int | None, end: int | None) -> str:
    if start is None:
        return ""
    if end is None or end == start:
        return f"page {start}"
    return f"pages {start}-{end}"


def source_title(source: dict[str, Any]) -> str:
    """One line for a source: 'Factory Health & Safety Manual · section · page 2'."""
    parts = [str(source.get("doc_title") or source.get("source_name") or "Document")]
    if source.get("section_title"):
        parts.append(str(source["section_title"]))
    page = page_label(source.get("page_start"), source.get("page_end"))
    if page:
        parts.append(page)
    return " · ".join(parts)


BadgeColor = Literal["green", "red", "orange", "gray"]


def status_badge(document: dict[str, Any]) -> tuple[str, BadgeColor]:
    """Label and colour for a document's status."""
    status = document.get("status", "")
    if status == "archived":
        return "Archived", "gray"
    if status == "ready":
        return "Ready", "green"
    if status == "failed":
        return f"Failed: {failure_message(document.get('status_reason'))}", "red"
    return "Processing", "orange"


def department_label(code: str) -> str:
    return DEPARTMENTS.get(code, code.title()) if code else "Personal"


def human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


EXAMPLE_QUESTIONS = [
    "When can I re-enter an evacuated area?",
    "What must be checked before operating a CNC machine?",
    "How do I submit a leave request?",
    "Who can release a product after final inspection?",
]

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)|!\[[^\]]*\]\[[^\]]*\]")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$])")


def safe_markdown(text: str) -> str:
    """Make model output safe to render as markdown.

    Images are removed because a browser fetches them automatically, which could leak text through
    the image address. Links keep their label but lose their address. Everything else (bullets,
    bold, the [1] citation markers) is left alone.
    """
    text = _IMAGE.sub("", text)
    return _LINK.sub(r"\1", text)


def escape_markdown(text: str) -> str:
    """Show text literally: for quotes copied from documents, which must not be interpreted."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


ROLES = {
    "super_admin": "Super Admin",
    "department_manager": "Department Manager",
    "employee": "Employee",
}


def role_label(role: str) -> str:
    return ROLES.get(role, role)


def visibility_label(document: dict[str, Any]) -> str:
    if document.get("scope") == "personal":
        return "Personal"
    return "Company-wide" if document.get("visibility") == "company" else "Department only"


def can_manage(user: dict[str, Any], document: dict[str, Any]) -> bool:
    """Whether to SHOW the re-index and delete buttons. Only a convenience: the server decides."""
    if document.get("scope") == "personal":
        return True  # the list only ever contains your own personal documents
    if user.get("role") == "super_admin":
        return True
    return user.get("role") == "department_manager" and document.get("department") in user.get(
        "departments", []
    )


def upload_targets(user: dict[str, Any]) -> list[str]:
    """Which places this user may add documents to, as department codes ('' = personal)."""
    targets = [""]
    if user.get("role") == "super_admin":
        return [*targets, *DEPARTMENTS]
    if user.get("role") == "department_manager":
        return [*targets, *user.get("departments", [])]
    return targets


def retry_message(error: ApiError) -> str:
    if error.status == 429 and error.retry_after:
        minutes = max(1, round(error.retry_after / 60))
        return f"Too many failed attempts. Try again in about {minutes} minute(s)."
    return error_message(error)


def company_upload_targets(user: dict[str, Any]) -> list[str]:
    """Departments this user may add COMPANY documents to (empty for ordinary employees)."""
    return [t for t in upload_targets(user) if t]


DOCUMENT_TYPES = {
    "sop": "SOP",
    "manual": "Manual",
    "policy": "Policy",
    "form": "Form",
    "checklist": "Checklist",
    "report": "Report",
    "other": "Other",
}

# Plain words for the audit log (the stored action names are for programs).
ACTION_LABELS = {
    "login": "Sign-in",
    "logout": "Sign-out",
    "refresh": "Session renewed",
    "refresh_token_reuse": "Old session token reused",
    "setup_admin": "First Super Admin created",
    "user_create": "Account created",
    "user_update": "Account details changed",
    "user_activate": "Account enabled",
    "user_deactivate": "Account disabled",
    "user_role_change": "Role changed",
    "user_departments_change": "Departments changed",
    "password_reset": "Password reset by an admin",
    "password_change": "Password changed",
    "admin_endpoint": "Admin page refused",
    "document_upload": "Document uploaded",
    "document_delete": "Document deleted",
    "document_reindex": "Document re-indexed",
    "document_archive": "Document archived",
    "document_restore": "Document restored",
    "document_access": "Document access refused",
    "document_version_upload": "New version uploaded",
    "document_version_replace": "Old version replaced",
}

OUTCOME_LABELS = {"ok": "OK", "denied": "Refused", "failed": "Failed"}


def action_label(action: str) -> str:
    return ACTION_LABELS.get(action, action.replace("_", " ").capitalize())


def audit_summary(entry: dict[str, Any]) -> str:
    """One line for an audit entry, e.g. 'Asha Admin: Account disabled (Esha Production)'."""
    who = entry.get("actor_name") or "Someone not signed in"
    line = f"{who}: {action_label(entry['action'])}"
    if entry.get("target_label"):
        line += f" ({entry['target_label']})"
    if entry.get("detail"):
        line += f" [{entry['detail']}]"
    return line


# ---- navigation: which pages each kind of account sees (a convenience; the server decides) ----

DASHBOARD = "\U0001f3e0 Dashboard"
UPLOAD_PDF = "\u2b06\ufe0f Upload PDF"
MY_DOCUMENTS = "\U0001f4c4 My Documents"
CHAT_MINE = "\U0001f4ac Chat with My Documents"
COMPANY_KNOWLEDGE = "\U0001f3e2 Company Knowledge"
HISTORY = "\U0001f558 Conversation History"
DEPARTMENT_DOCUMENTS = "\U0001f5c2\ufe0f Department Documents"

OVERVIEW = "\U0001f4ca Overview"
EMPLOYEES = "\U0001f465 Employee Management"
CREATE_EMPLOYEE = "\u2795 Create Employee"
COMPANY_DOCUMENTS = "\U0001f5c2\ufe0f Company Documents"
UPLOAD_COMPANY = "\u2b06\ufe0f Upload Company Documents"
DEPARTMENTS_PAGE = "\U0001f3ed Departments"
AUDIT_LOGS = "\U0001f9fe Audit Logs"
ASK_COMPANY = "\U0001f4ac Ask Company Documents"

ADMIN_PAGES = [
    OVERVIEW,
    EMPLOYEES,
    CREATE_EMPLOYEE,
    COMPANY_DOCUMENTS,
    UPLOAD_COMPANY,
    DEPARTMENTS_PAGE,
    AUDIT_LOGS,
    ASK_COMPANY,
]
EMPLOYEE_PAGES = [DASHBOARD, UPLOAD_PDF, MY_DOCUMENTS, CHAT_MINE, COMPANY_KNOWLEDGE, HISTORY]
CHAT_PAGES = {CHAT_MINE, COMPANY_KNOWLEDGE, ASK_COMPANY}

ALL_DEPARTMENTS = "All departments"


def legacy_page(user: dict[str, Any], name: str) -> str | None:
    """Old bookmarks (?page=documents, users, chat) still land somewhere sensible."""
    admin = user.get("role") == "super_admin"
    return {
        "documents": COMPANY_DOCUMENTS if admin else MY_DOCUMENTS,
        "users": EMPLOYEES if admin else None,
        "chat": ASK_COMPANY if admin else CHAT_MINE,
    }.get(name)


def pages_for(user: dict[str, Any]) -> list[str]:
    """The sidebar pages for this account. Super Admins get the admin workspace; everyone else
    the staff workspace (department managers also manage their departments' documents)."""
    if user.get("role") == "super_admin":
        return list(ADMIN_PAGES)
    pages = list(EMPLOYEE_PAGES)
    if user.get("role") == "department_manager":
        pages.insert(pages.index(HISTORY), DEPARTMENT_DOCUMENTS)
    return pages


def short_time(value: str | None) -> str:
    """'2026-10-31T09:05:12.123+00:00' -> '2026-10-31 09:05'."""
    return (value or "")[:16].replace("T", " ")
