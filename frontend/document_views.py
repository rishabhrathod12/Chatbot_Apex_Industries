"""Uploading, listing and managing documents: personal PDFs, the company library, and the
Super Admin / department manager tools for company documents."""

from pathlib import Path
from typing import Any

import streamlit as st

import auth_views
import ui
from api_client import ApiClient, ApiError, ApiUnreachable, SessionExpired

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "sample_docs"


# ----------------------------------------------------------------------------- shared


def show_results(results: list[dict[str, Any]]) -> None:
    for result in results:
        name = ui.escape_markdown(result["filename"])
        if result["status"] == "accepted":
            st.success(f"{name}: uploaded, now being indexed")
        elif result["status"] == "duplicate":
            st.info(f"{name}: already uploaded")
        else:
            st.error(f"{name}: {ui.reject_message(result.get('reason'))}")


def send_files(
    client: ApiClient,
    files: list[tuple[str, bytes]],
    scope: str,
    department: str | None,
    company_wide: bool = False,
    details: dict[str, str] | None = None,
) -> None:
    try:
        results = client.upload(files, scope, department, company_wide, details)
    except ApiError as error:
        st.error(ui.error_message(error))
        return
    except ApiUnreachable:
        st.error("Cannot reach the API. Is it running?")
        return
    show_results(results)


def _fetch(client: ApiClient) -> list[dict[str, Any]] | None:
    try:
        return client.list_documents()
    except ApiUnreachable:
        st.error("Cannot reach the API. Is it running?")
    except SessionExpired:
        auth_views.session_ended()
    return None


def _document_line(document: dict[str, Any]) -> None:
    name = ui.escape_markdown(document.get("title") or document["original_filename"])
    st.markdown(f"**{name}**")
    detail = [ui.department_label(document["department"]), ui.visibility_label(document)]
    if document.get("document_type"):
        detail.append(ui.DOCUMENT_TYPES.get(document["document_type"], document["document_type"]))
    if document.get("doc_version"):
        detail.append(f"version {document['doc_version']}")
    if document.get("doc_code"):
        detail.append(document["doc_code"])
    detail.append(ui.human_size(document["size_bytes"]))
    st.caption(" · ".join(detail))
    if document.get("description"):
        st.caption(ui.escape_markdown(document["description"]))


def _status_cell(document: dict[str, Any]) -> None:
    label, color = ui.status_badge(document)
    st.badge(label, color=color)
    if document["status"] == "ready":
        pages = document.get("page_count") or "-"
        st.caption(f"{document['chunk_count']} passages · {pages} pages")


# ----------------------------------------------------------------------------- my own PDFs


def personal_upload(client: ApiClient) -> None:
    st.title("Upload PDF")
    st.info("Only you can see the documents you add here.", icon="\U0001f512")
    files = st.file_uploader(
        "Choose PDF files from your computer",
        type=["pdf"],
        accept_multiple_files=True,
        key="personal-upload",
    )
    if st.button("Upload", type="primary", disabled=not files, key="personal-upload-go"):
        send_files(client, [(f.name, f.getvalue()) for f in files or []], "personal", None)
    st.caption("When a document shows as Ready, ask about it under *Chat with My Documents*.")


def _my_row(client: ApiClient, document: dict[str, Any]) -> None:
    key = document["id"]
    if document["status"] != "processing" and st.button("Re-index", key=f"re-{key}"):
        try:
            client.reindex(key)
        except ApiError as error:
            st.error(ui.error_message(error))
        st.rerun()
    with st.popover("Delete"):
        st.write("Remove this document and everything searchable from it?")
        if st.button("Yes, delete", key=f"del-{key}", type="primary"):
            try:
                client.delete_document(key)
            except ApiError as error:
                st.error(ui.error_message(error))
            st.rerun()


@st.fragment(run_every=3)
def my_documents(client: ApiClient) -> None:
    documents = _fetch(client)
    if documents is None:
        return
    mine = [d for d in documents if d["scope"] == "personal"]
    st.subheader(f"My documents ({len(mine)})")
    if not mine:
        st.caption("You have not added any documents yet. Use *Upload PDF*.")
        return
    for document in mine:
        with st.container(border=True):
            info, status, actions = st.columns([5, 3, 2])
            with info:
                _document_line(document)
            with status:
                _status_cell(document)
            with actions:
                _my_row(client, document)


# ----------------------------------------------------------------------------- company library


def company_library(client: ApiClient) -> None:
    """The company documents this person may read. Nothing to change here."""
    documents = _fetch(client)
    if documents is None:
        return
    company = [d for d in documents if d["scope"] == "enterprise" and d["status"] != "archived"]
    st.subheader(f"Documents you can ask about ({len(company)})")
    if not company:
        st.caption("No company documents are available to you yet.")
        return
    departments = sorted({d["department"] for d in company})
    choice = st.selectbox(
        "Department",
        [ui.ALL_DEPARTMENTS, *(ui.department_label(d) for d in departments)],
        key="library-department",
    )
    for document in company:
        if choice != ui.ALL_DEPARTMENTS and ui.department_label(document["department"]) != choice:
            continue
        with st.container(border=True):
            info, status = st.columns([6, 3])
            with info:
                _document_line(document)
            with status:
                _status_cell(document)


# ----------------------------------------------------------------------------- company tools


def company_upload(client: ApiClient, user: dict[str, Any], *, show_title: bool = True) -> None:
    if show_title:
        st.title("Upload company documents")
    targets = ui.company_upload_targets(user)
    if not targets:
        st.error("Your account cannot add company documents.")
        return
    st.caption(
        "Official documents go into the company library and become searchable for the people "
        "in the department you choose."
    )
    files = st.file_uploader(
        "PDF, DOCX or TXT files",
        type=["pdf", "docx", "txt"],
        accept_multiple_files=True,
        key="company-upload",
    )
    left, right = st.columns(2)
    with left:
        department = st.selectbox(
            "Department", targets, format_func=ui.department_label, key="company-department"
        )
        company_wide = st.checkbox(
            "Visible to all employees (company-wide)",
            help="Otherwise only that department can see it.",
            key="company-wide",
        )
    with right:
        doc_type = st.selectbox(
            "Document type",
            ["", *ui.DOCUMENT_TYPES],
            format_func=lambda code: ui.DOCUMENT_TYPES.get(code, "(not set)"),
            key="company-type",
        )
        title = st.text_input("Title", key="company-title", help="Leave empty to use the file's.")
        version = st.text_input("Version", key="company-version", placeholder="e.g. 1.0")
    description = st.text_area("Description", key="company-description", max_chars=2000)
    many = len(files or []) > 1
    if many:
        st.caption("Title and version apply to a single file, so they are skipped for this batch.")

    if st.button("Upload", type="primary", disabled=not files, key="company-upload-go"):
        details = {
            "description": description,
            "document_type": doc_type,
            "title": "" if many else title,
            "version": "" if many else version,
        }
        send_files(
            client,
            [(f.name, f.getvalue()) for f in files or []],
            "enterprise",
            department,
            company_wide,
            details,
        )

    if user["role"] == "super_admin" and SAMPLE_DIR.exists():
        st.divider()
        if st.button("Load the 6 sample documents", key="load-samples"):
            for name, (dept, wide) in ui.SAMPLE_DOCUMENTS.items():
                if (SAMPLE_DIR / name).exists():
                    send_files(
                        client, [(name, (SAMPLE_DIR / name).read_bytes())], "enterprise", dept, wide
                    )


def _version_popover(client: ApiClient, document: dict[str, Any]) -> None:
    key = document["id"]
    with st.popover("New version"):
        st.caption(
            "The current version stays searchable until the new one is ready, then it is archived."
        )
        upload = st.file_uploader(
            "Replacement file", type=["pdf", "docx", "txt"], key=f"ver-file-{key}"
        )
        label = st.text_input("Version label (optional)", key=f"ver-label-{key}")
        if st.button("Upload new version", key=f"ver-go-{key}", disabled=upload is None):
            assert upload is not None
            try:
                client.upload_version(
                    key, upload.name, upload.getvalue(), {"version": label.strip()}
                )
            except ApiError as error:
                st.error(ui.error_message(error))
            else:
                st.success("Uploaded. It will replace the current version once it is indexed.")


def _history_popover(client: ApiClient, document: dict[str, Any]) -> None:
    key = document["id"]
    with st.popover("History"):
        if st.button("Show versions", key=f"hist-{key}"):
            try:
                versions = client.list_versions(key)
            except ApiError as error:
                st.error(ui.error_message(error))
                return
            for version in versions:
                label, _ = ui.status_badge(version)
                number = version.get("doc_version") or "-"
                name = ui.escape_markdown(version.get("title") or version["original_filename"])
                st.markdown(
                    f"**{name}** · version {number} · {label} · "
                    f"{ui.short_time(version['created_at'])}"
                )


def _manage_actions(client: ApiClient, document: dict[str, Any]) -> None:
    key = document["id"]
    archived = document["status"] == "archived"
    busy = document["status"] in {"uploaded", "processing"}
    if not archived and not busy and st.button("Re-index", key=f"re-{key}"):
        _act(lambda: client.reindex(key))
    if archived:
        if st.button("Restore", key=f"restore-{key}"):
            _act(lambda: client.restore_document(key))
    elif not busy and st.button("Archive", key=f"archive-{key}"):
        _act(lambda: client.archive_document(key))
    if not archived and not busy:
        _version_popover(client, document)
    _history_popover(client, document)
    with st.popover("Delete"):
        st.write("Delete this document for good? (To keep it but stop it being used, archive it.)")
        if st.button("Yes, delete", key=f"del-{key}", type="primary"):
            _act(lambda: client.delete_document(key))


def _act(action: Any) -> None:
    try:
        action()
    except ApiError as error:
        st.error(ui.error_message(error))
        return
    st.rerun()


@st.fragment(run_every=4)
def company_manager(client: ApiClient, user: dict[str, Any]) -> None:
    documents = _fetch(client)
    if documents is None:
        return
    managed = [d for d in documents if d["scope"] == "enterprise" and ui.can_manage(user, d)]

    first, second, third = st.columns(3)
    departments = sorted({d["department"] for d in managed})
    department = first.selectbox(
        "Department",
        [ui.ALL_DEPARTMENTS, *(ui.department_label(d) for d in departments)],
        key="manage-department",
    )
    status = second.selectbox(
        "Status", ["All", "Ready", "Processing", "Failed", "Archived"], key="manage-status"
    )
    text = third.text_input("Search by title or file name", key="manage-search").strip().lower()

    shown = []
    for document in managed:
        if (
            department != ui.ALL_DEPARTMENTS
            and ui.department_label(document["department"]) != department
        ):
            continue
        label = ui.status_badge(document)[0]
        if status != "All" and not label.startswith(status):
            continue
        haystack = f"{document.get('title') or ''} {document['original_filename']}".lower()
        if text and text not in haystack:
            continue
        shown.append(document)

    st.subheader(f"Company documents ({len(shown)} of {len(managed)})")
    if not shown:
        st.caption("No documents match.")
        return
    for document in shown:
        with st.container(border=True):
            info, state, actions = st.columns([5, 3, 3])
            with info:
                _document_line(document)
            with state:
                _status_cell(document)
            with actions:
                _manage_actions(client, document)


def company_documents_page(client: ApiClient, user: dict[str, Any], title: str) -> None:
    st.title(title)
    company_manager(client, user)
