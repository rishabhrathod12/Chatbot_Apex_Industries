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

# The six fictional sample documents, and the department each belongs to.
SAMPLE_DOCUMENTS = {
    "01_Machine_Operation_Manual.pdf": "production",
    "02_Maintenance_Manual.pdf": "maintenance",
    "03_Factory_Safety_Manual.pdf": "safety",
    "04_Production_SOP.pdf": "production",
    "05_HR_Policy.pdf": "hr",
    "06_Quality_Control_SOP.pdf": "quality",
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
}

_REJECT_TEXT = {
    "unsupported_type": "not a PDF, DOCX or TXT file",
    "content_mismatch": "the contents do not match the file type",
    "empty_file": "the file is empty",
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


BadgeColor = Literal["green", "red", "orange"]


def status_badge(document: dict[str, Any]) -> tuple[str, BadgeColor]:
    """Label and colour for a document's status."""
    status = document.get("status", "")
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
