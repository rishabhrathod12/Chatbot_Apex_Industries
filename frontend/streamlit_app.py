"""Apex Knowledge Assistant: the web interface.

Run from the repository root:   streamlit run frontend/streamlit_app.py
It talks to the API (default http://127.0.0.1:8000/api/v1, override with API_BASE_URL).
"""

import re
from pathlib import Path
from typing import Any

import streamlit as st

import ui
from api_client import ApiClient, ApiError, ApiUnreachable, base_url

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "sample_docs"
ALL_DEPARTMENTS = "All departments"
COMPANY_SCOPE = "Company documents"
PERSONAL_SCOPE = "My own documents"
CHAT_PAGE = "💬 Chat"
DOCUMENTS_PAGE = "📄 Documents"
_CONVERSATION_ID = re.compile(r"^[0-9a-f]{32}$")


def api() -> ApiClient:
    if "api" not in st.session_state:
        st.session_state["api"] = ApiClient()
    client: ApiClient = st.session_state["api"]
    return client


# ----------------------------------------------------------------------------- sidebar


def sidebar() -> tuple[str, str, str | None]:
    """Draw the sidebar. Returns (page, scope, department)."""
    with st.sidebar:
        st.markdown("### 🏭 Apex Precision Manufacturing")
        st.caption("Knowledge Assistant · fictional sample data")

        if api().healthy():
            st.success("API connected", icon="✅")
        else:
            st.error("API not reachable", icon="🔌")
            st.caption(f"Expected at `{base_url()}`")

        start = 1 if st.query_params.get("page") == "documents" else 0
        page = st.radio(
            "Go to", [CHAT_PAGE, DOCUMENTS_PAGE], index=start, label_visibility="collapsed"
        )

        st.divider()
        st.markdown("**Search in**")
        scope_label = st.radio(
            "Which documents", [COMPANY_SCOPE, PERSONAL_SCOPE], label_visibility="collapsed"
        )
        scope = "enterprise" if scope_label == COMPANY_SCOPE else "personal"
        department: str | None = None
        if scope == "enterprise":
            choice = st.selectbox("Department", [ALL_DEPARTMENTS, *ui.DEPARTMENTS.values()])
            if choice != ALL_DEPARTMENTS:
                department = next(code for code, name in ui.DEPARTMENTS.items() if name == choice)
            st.caption("Demo control. Once login arrives, your departments come from your account.")

        if page == CHAT_PAGE:
            st.divider()
            conversation_list()
    return page, scope, department


def conversation_list() -> None:
    if st.button("➕ New chat", use_container_width=True):
        st.session_state.pop("conversation_id", None)
        st.rerun()
    try:
        conversations = api().list_conversations()
    except (ApiUnreachable, ApiError):
        return
    current = st.session_state.get("conversation_id")
    if conversations:
        st.markdown("**Recent chats**")
    for item in conversations[:12]:
        label = item["title"] if len(item["title"]) <= 34 else item["title"][:31] + "…"
        if st.button(
            label,
            key=f"conv-{item['id']}",
            use_container_width=True,
            type="primary" if item["id"] == current else "secondary",
        ):
            st.session_state["conversation_id"] = item["id"]
            st.rerun()
    if current and st.button("🗑 Delete this chat", use_container_width=True):
        try:
            api().delete_conversation(current)
        except ApiError:
            pass
        st.session_state.pop("conversation_id", None)
        st.rerun()


# ----------------------------------------------------------------------------- chat


def render_sources(sources: list[dict[str, Any]]) -> None:
    with st.expander(f"Sources ({len(sources)})", expanded=False):
        for source in sources:
            st.markdown(f"**[{source['number']}]** {ui.escape_markdown(ui.source_title(source))}")
            if source.get("doc_code"):
                st.caption(f"{source['doc_code']} · {source.get('source_name', '')}")
            if source.get("quote"):
                st.markdown("> " + ui.escape_markdown(source["quote"]))


def render_message(message: dict[str, Any]) -> None:
    if message["role"] == "user":
        with st.chat_message("user", avatar="👤"):
            st.markdown(ui.escape_markdown(message["content"]))
        return
    with st.chat_message("assistant", avatar="🏭"):
        if message.get("status") == "not_found":
            st.info(message["content"], icon="🔎")
            st.caption("Nothing in the documents you can access answers this.")
        else:
            st.markdown(ui.safe_markdown(message["content"]))
        if message.get("rewritten_question"):
            st.caption(f"Searched as: *{ui.escape_markdown(message['rewritten_question'])}*")
        if message.get("sources"):
            render_sources(message["sources"])


def load_messages() -> list[dict[str, Any]]:
    # A link like ?c=<conversation id> opens that conversation (bookmarkable and shareable).
    linked = st.query_params.get("c", "")
    if "conversation_id" not in st.session_state and _CONVERSATION_ID.match(linked):
        st.session_state["conversation_id"] = linked
    conversation_id = st.session_state.get("conversation_id")
    if not conversation_id:
        return []
    try:
        return list(api().get_conversation(conversation_id)["messages"])
    except ApiError:
        st.session_state.pop("conversation_id", None)  # it was deleted elsewhere
        return []


def chat_page(scope: str, department: str | None) -> None:
    st.title("Ask the documents")
    messages = load_messages()

    if not messages:
        st.markdown(
            "Ask about a procedure, policy or manual. Every answer shows the document, "
            "section and page it came from."
        )
        columns = st.columns(2)
        for index, example in enumerate(ui.EXAMPLE_QUESTIONS):
            if columns[index % 2].button(example, key=f"example-{index}", use_container_width=True):
                st.session_state["pending_question"] = example
                st.rerun()

    for message in messages:
        render_message(message)

    typed = st.chat_input("Ask about a procedure, policy or manual…")
    question: str | None = typed or st.session_state.pop("pending_question", None)
    if not question:
        return

    with st.chat_message("user", avatar="👤"):
        st.markdown(ui.escape_markdown(question))
    with st.chat_message("assistant", avatar="🏭"), st.spinner("Searching the documents…"):
        try:
            reply = api().chat(question, scope, department, st.session_state.get("conversation_id"))
        except ApiError as error:
            if error.detail == "conversation_not_found":
                st.session_state.pop("conversation_id", None)
            st.error(ui.error_message(error), icon="⚠️")
            return
        except ApiUnreachable:
            st.error("Cannot reach the API. Is it running?", icon="🔌")
            return
    st.session_state["conversation_id"] = reply["conversation_id"]
    st.rerun()


# ----------------------------------------------------------------------------- documents


def upload_section(scope: str) -> None:
    st.subheader("Add documents")
    left, right = st.columns([3, 2])
    with left:
        files = st.file_uploader(
            "PDF, DOCX or TXT files", type=["pdf", "docx", "txt"], accept_multiple_files=True
        )
    with right:
        target = st.radio(
            "Add to", [COMPANY_SCOPE, PERSONAL_SCOPE], index=0 if scope == "enterprise" else 1
        )
        department = None
        if target == COMPANY_SCOPE:
            name = st.selectbox("Department", list(ui.DEPARTMENTS.values()), key="upload-dept")
            department = next(code for code, label in ui.DEPARTMENTS.items() if label == name)
    scope_value = "enterprise" if target == COMPANY_SCOPE else "personal"

    if st.button("Upload", type="primary", disabled=not files):
        send_files([(f.name, f.getvalue()) for f in files], scope_value, department)

    if SAMPLE_DIR.exists() and st.button("Load the 6 sample documents"):
        samples = [
            (name, (SAMPLE_DIR / name).read_bytes(), dept)
            for name, dept in ui.SAMPLE_DOCUMENTS.items()
            if (SAMPLE_DIR / name).exists()
        ]
        for name, content, dept in samples:
            send_files([(name, content)], "enterprise", dept)


def send_files(files: list[tuple[str, bytes]], scope: str, department: str | None) -> None:
    try:
        results = api().upload(files, scope, department)
    except ApiError as error:
        st.error(ui.error_message(error))
        return
    except ApiUnreachable:
        st.error("Cannot reach the API. Is it running?")
        return
    for result in results:
        name = ui.escape_markdown(result["filename"])
        if result["status"] == "accepted":
            st.success(f"{name}: uploaded, now being indexed")
        elif result["status"] == "duplicate":
            st.info(f"{name}: already uploaded")
        else:
            st.error(f"{name}: {ui.reject_message(result.get('reason'))}")


@st.fragment(run_every=3)
def document_list() -> None:
    try:
        documents = api().list_documents()
    except ApiUnreachable:
        st.error("Cannot reach the API. Is it running?")
        return
    st.subheader(f"Library ({len(documents)})")
    if not documents:
        st.caption("No documents yet. Upload some, or load the sample set.")
        return
    for document in documents:
        label, color = ui.status_badge(document)
        with st.container(border=True):
            info, status, actions = st.columns([5, 3, 2])
            with info:
                name = ui.escape_markdown(document.get("title") or document["original_filename"])
                st.markdown(f"**{name}**")
                detail = [ui.department_label(document["department"])]
                if document.get("doc_code"):
                    detail.append(document["doc_code"])
                detail.append(ui.human_size(document["size_bytes"]))
                st.caption(" · ".join(detail))
            with status:
                st.badge(label, color=color)
                if document["status"] == "ready":
                    pages = document.get("page_count") or "-"
                    st.caption(f"{document['chunk_count']} passages · {pages} pages")
            with actions:
                row_actions(document)


def row_actions(document: dict[str, Any]) -> None:
    key = document["id"]
    if document["status"] != "processing" and st.button("Re-index", key=f"re-{key}"):
        try:
            api().reindex(key)
        except ApiError as error:
            st.error(ui.error_message(error))
        st.rerun()
    with st.popover("Delete"):
        st.write("Remove this document and everything searchable from it?")
        if st.button("Yes, delete", key=f"del-{key}", type="primary"):
            try:
                api().delete_document(key)
            except ApiError as error:
                st.error(ui.error_message(error))
            st.rerun()


def documents_page(scope: str) -> None:
    st.title("Documents")
    upload_section(scope)
    st.divider()
    document_list()


# ----------------------------------------------------------------------------- entry


def main() -> None:
    st.set_page_config(page_title="Apex Knowledge Assistant", page_icon="🏭", layout="wide")
    page, scope, department = sidebar()
    if not api().healthy():
        st.title("Apex Knowledge Assistant")
        st.error("The API is not running, so there is nothing to show yet.", icon="🔌")
        st.markdown("Start it from the repository root, then reload this page:")
        st.code("scripts\\dev.ps1", language="powershell")
        st.stop()
    if page == CHAT_PAGE:
        chat_page(scope, department)
    else:
        documents_page(scope)


main()
