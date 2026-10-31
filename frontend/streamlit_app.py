"""Apex Knowledge Assistant: the web interface.

Run from the repository root:   streamlit run frontend/streamlit_app.py
It talks to the API (default http://127.0.0.1:8000/api/v1, override with API_BASE_URL).
The server decides what each user may see and do; this page only shows the result. Which pages
appear in the sidebar follows the signed-in account's role as the server reports it.
"""

from typing import Any

import streamlit as st

import admin_views
import auth_views
import chat_views
import document_views
import employee_views
import ui
from api_client import REQUIRED_API_LEVEL, ApiClient, SessionExpired, base_url

KNOWLEDGE_VIEWS = ["Ask a question", "Browse documents"]


def api() -> ApiClient:
    """This browser session's API client.

    Streamlit keeps `st.session_state` when the code is edited and reloaded, so a page left open
    can still hold a client made by an OLDER copy of `ApiClient` (missing newer methods such as
    `api_level`). A client that was not made by the current class is dropped and replaced; the
    person simply signs in again. Nothing else about sessions changes.
    """
    if st.session_state.get("api_class") is not ApiClient:
        st.session_state["api"] = ApiClient()
        st.session_state["api_class"] = ApiClient
    client: ApiClient = st.session_state["api"]
    return client


def current_user() -> dict[str, Any]:
    user = api().user
    assert user is not None
    return user


# ----------------------------------------------------------------------------- sidebar


def sidebar(user: dict[str, Any]) -> str:
    """Draw the sidebar and return the page to show."""
    pages = ui.pages_for(user)
    with st.sidebar:
        st.markdown("### \U0001f3ed Apex Precision Manufacturing")
        st.caption("Knowledge Assistant \u00b7 fictional sample data")

        with st.container(border=True):
            st.markdown(f"**{user['full_name']}**")
            places = ", ".join(ui.department_label(d) for d in user["departments"]) or "All"
            st.caption(f"{ui.role_label(user['role'])} \u00b7 {places}")
            if st.button("Sign out", use_container_width=True):
                api().logout()
                st.session_state.pop("conversation_id", None)
                auth_views.forget_portal()
                st.rerun()

        if st.session_state.get("nav") not in pages:
            legacy = ui.legacy_page(user, str(st.query_params.get("page", "")))
            st.session_state["nav"] = legacy if legacy in pages else pages[0]
        st.radio("Go to", pages, key="nav", label_visibility="collapsed")
        page: str = st.session_state["nav"]

        # A conversation belongs to the page it was started on, unless the person chose to
        # continue it somewhere else (Conversation History does that on purpose).
        if st.session_state.get("last_page") != page:
            if not st.session_state.pop("keep_conversation", False):
                st.session_state.pop("conversation_id", None)
            st.session_state["last_page"] = page

        if page in ui.CHAT_PAGES:
            st.divider()
            chat_views.sidebar_conversations(api())
    return page


# ----------------------------------------------------------------------------- pages


def department_filter() -> str | None:
    choice = st.selectbox(
        "Only in department", [ui.ALL_DEPARTMENTS, *ui.DEPARTMENTS.values()], key="ask-department"
    )
    st.caption("Narrows the search. You only ever get answers from documents you can access.")
    if choice == ui.ALL_DEPARTMENTS:
        return None
    return next(code for code, name in ui.DEPARTMENTS.items() if name == choice)


def company_knowledge_page(client: ApiClient) -> None:
    st.title("Company knowledge")
    view = st.radio(
        "View",
        KNOWLEDGE_VIEWS,
        horizontal=True,
        label_visibility="collapsed",
        key="knowledge-view",
    )
    if view == KNOWLEDGE_VIEWS[1]:
        document_views.company_library(client)
        return
    department = department_filter()
    chat_views.chat_page(
        client,
        "enterprise",
        department,
        title=None,
        intro=(
            "Ask about a procedure, policy or manual. You get answers from the company "
            "documents you are allowed to see, each with its document, section and page."
        ),
        examples=True,
    )


def department_documents_page(client: ApiClient, user: dict[str, Any]) -> None:
    st.title("Department documents")
    with st.expander("\u2795 Add documents to your departments"):
        document_views.company_upload(client, user, show_title=False)
    document_views.company_manager(client, user)


def render(page: str, user: dict[str, Any]) -> None:
    client = api()
    if page == ui.DASHBOARD:
        employee_views.dashboard_page(client, user)
    elif page == ui.UPLOAD_PDF:
        document_views.personal_upload(client)
    elif page == ui.MY_DOCUMENTS:
        st.title("My documents")
        document_views.my_documents(client)
    elif page == ui.CHAT_MINE:
        chat_views.chat_page(
            client,
            "personal",
            None,
            title="Chat with my documents",
            intro=(
                "Ask about the PDFs you uploaded. Every answer shows the document, section "
                "and page it came from. Only you can see these documents."
            ),
        )
    elif page == ui.COMPANY_KNOWLEDGE:
        company_knowledge_page(client)
    elif page == ui.HISTORY:
        chat_views.history_page(client, ui.CHAT_MINE, ui.COMPANY_KNOWLEDGE)
    elif page == ui.DEPARTMENT_DOCUMENTS:
        department_documents_page(client, user)
    elif page == ui.OVERVIEW:
        admin_views.overview_page(client)
    elif page == ui.EMPLOYEES:
        admin_views.employees_page(client, user["user_id"])
    elif page == ui.CREATE_EMPLOYEE:
        admin_views.create_employee_page(client)
    elif page == ui.COMPANY_DOCUMENTS:
        document_views.company_documents_page(client, user, "Company documents")
    elif page == ui.UPLOAD_COMPANY:
        document_views.company_upload(client, user)
    elif page == ui.DEPARTMENTS_PAGE:
        admin_views.departments_page(client)
    elif page == ui.AUDIT_LOGS:
        admin_views.audit_page(client)
    elif page == ui.ASK_COMPANY:
        st.title("Ask the company documents")
        department = department_filter()
        chat_views.chat_page(
            client,
            "enterprise",
            department,
            title=None,
            intro=(
                "Ask about a procedure, policy or manual. Every answer shows the document, "
                "section and page it came from."
            ),
            examples=True,
        )


# ----------------------------------------------------------------------------- entry


def main() -> None:
    st.set_page_config(page_title="Apex Knowledge Assistant", page_icon="\U0001f3ed", layout="wide")
    if not api().healthy():
        st.title("Apex Knowledge Assistant")
        st.error("The API is not running, so there is nothing to show yet.", icon="\U0001f50c")
        st.caption(f"Expected at `{base_url()}`")
        st.markdown("Start it from the repository root, then reload this page:")
        st.code("scripts\\dev.ps1", language="powershell")
        st.stop()

    level = api().api_level()
    if level is None or level < REQUIRED_API_LEVEL:
        st.title("Apex Knowledge Assistant")
        st.error(
            "The API running on this computer is an older copy than this page, so signing in "
            "would fail. Close its window and start both again.",
            icon="\u26a0\ufe0f",
        )
        st.code("scripts\\dev.ps1 -Restart", language="powershell")
        reported = "unknown" if level is None else level
        st.caption(
            f"API at `{base_url()}` reports level {reported}; this page needs {REQUIRED_API_LEVEL}."
        )
        st.stop()

    if not api().is_logged_in:
        auth_views.signed_out_page(api())
        st.stop()

    if current_user().get("must_change_password"):
        auth_views.password_change_page(api())
        st.stop()

    try:
        user = current_user()
        render(sidebar(user), user)
    except SessionExpired:
        auth_views.session_ended()


main()
