"""Apex Knowledge Assistant: the web interface.

Run from the repository root:   streamlit run frontend/streamlit_app.py
It talks to the API (default http://127.0.0.1:8000/api/v1, override with API_BASE_URL).
The server decides what each user may see and do; this page only shows the result.
"""

import re
from pathlib import Path
from typing import Any

import streamlit as st

import ui
from api_client import ApiClient, ApiError, ApiUnreachable, SessionExpired, base_url

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "sample_docs"
ALL_DEPARTMENTS = "All departments"
COMPANY_SCOPE = "Company documents"
PERSONAL_SCOPE = "My own documents"
CHAT_PAGE = "💬 Chat"
DOCUMENTS_PAGE = "📄 Documents"
USERS_PAGE = "👥 Users"
_CONVERSATION_ID = re.compile(r"^[0-9a-f]{32}$")


def api() -> ApiClient:
    if "api" not in st.session_state:
        st.session_state["api"] = ApiClient()
    client: ApiClient = st.session_state["api"]
    return client


def current_user() -> dict[str, Any]:
    user = api().user
    assert user is not None
    return user


def session_ended() -> None:
    """The login is no longer valid: go back to the sign-in page with an explanation."""
    st.session_state.pop("conversation_id", None)
    st.session_state["notice"] = "Your session ended. Please sign in again."
    st.rerun()


# ----------------------------------------------------------------------------- sign in


def setup_status() -> dict[str, Any]:
    """Whether the system still needs its first account. If we can't tell, show the normal form."""
    try:
        return api().setup_status()
    except (ApiError, ApiUnreachable):
        return {"setup_required": False, "token_required": False, "available": False}


def setup_form(status: dict[str, Any]) -> None:
    """First run only: no account exists, so the first person creates the Super Admin here."""
    st.subheader("First-time setup")
    if not status["available"]:
        st.warning(
            "No account exists yet, and setting one up from this page is switched off. "
            "Either set SETUP_TOKEN in .env and restart the API, or create the Super Admin "
            "on the command line:"
        )
        st.code(
            'python -m app.cli create-user --email you@company.com --name "Your Name" '
            "--role super_admin"
        )
        return
    st.write(
        "No accounts exist yet. Create the Super Admin account for this system. "
        "This page disappears as soon as it exists; other accounts are added later "
        "from the Users page."
    )
    with st.form("setup"):
        full_name = st.text_input("Full name")
        email = st.text_input("Email")
        password = st.text_input("Password", type="password", help="At least 12 characters.")
        repeat = st.text_input("Repeat password", type="password")
        token = (
            st.text_input("Setup token", type="password", help="The SETUP_TOKEN value in .env.")
            if status["token_required"]
            else ""
        )
        submitted = st.form_submit_button(
            "Create Super Admin account", type="primary", use_container_width=True
        )
    if not submitted:
        return
    if problem := ui.setup_problem(full_name, email, password, repeat):
        st.error(problem)
        return
    try:
        api().setup(email, full_name, password, token)
        api().login(email, password)
    except ApiError as error:
        st.error(ui.retry_message(error), icon="\U0001f512")
        return
    except ApiUnreachable:
        st.error("Cannot reach the API. Is it running?", icon="\U0001f50c")
        return
    st.rerun()


def login_page() -> None:
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        st.markdown("## 🏭 Apex Knowledge Assistant")
        st.caption("Apex Precision Manufacturing Pvt. Ltd. · fictional sample data")
        if notice := st.session_state.pop("notice", None):
            st.info(notice)
        status = setup_status()
        if status["setup_required"]:
            setup_form(status)
            return
        with st.form("login"):
            email = st.text_input("Login ID or email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        if submitted:
            try:
                api().login(email, password)
            except ApiError as error:
                st.error(ui.retry_message(error), icon="🔒")
                return
            except ApiUnreachable:
                st.error("Cannot reach the API. Is it running?", icon="🔌")
                return
            st.rerun()
        st.caption("Accounts are created by a Super Admin. There is no self sign-up.")


def password_change_page() -> None:
    """Shown instead of the app while a temporary password is still in force."""
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        st.markdown("## Choose a new password")
        st.write(
            "You signed in with a temporary password. Choose your own to continue. "
            "It must be at least 12 characters."
        )
        with st.form("change-password"):
            current = st.text_input("Current (temporary) password", type="password")
            new = st.text_input("New password", type="password")
            repeat = st.text_input("Repeat new password", type="password")
            submitted = st.form_submit_button(
                "Change password", type="primary", use_container_width=True
            )
        if st.button("Sign out", use_container_width=True):
            api().logout()
            st.rerun()
        if not submitted:
            return
        if not current or not new:
            st.error("Please fill in all three boxes.")
            return
        if new != repeat:
            st.error("The two new passwords do not match.")
            return
        try:
            api().change_password(current, new)
        except ApiError as error:
            st.error(ui.retry_message(error), icon="\U0001f512")
            return
        except ApiUnreachable:
            st.error("Cannot reach the API. Is it running?", icon="\U0001f50c")
            return
        st.rerun()


# ----------------------------------------------------------------------------- sidebar


def sidebar() -> tuple[str, str, str | None]:
    """Draw the sidebar. Returns (page, scope, department)."""
    user = current_user()
    with st.sidebar:
        st.markdown("### 🏭 Apex Precision Manufacturing")
        st.caption("Knowledge Assistant · fictional sample data")

        with st.container(border=True):
            st.markdown(f"**{user['full_name']}**")
            places = ", ".join(ui.department_label(d) for d in user["departments"]) or "All"
            st.caption(f"{ui.role_label(user['role'])} · {places}")
            if st.button("Sign out", use_container_width=True):
                api().logout()
                st.session_state.pop("conversation_id", None)
                st.rerun()

        pages = [CHAT_PAGE, DOCUMENTS_PAGE]
        if user["role"] == "super_admin":
            pages.append(USERS_PAGE)
        wanted = {"documents": DOCUMENTS_PAGE, "users": USERS_PAGE}.get(
            str(st.query_params.get("page", "")), CHAT_PAGE
        )
        page = st.radio(
            "Go to",
            pages,
            index=pages.index(wanted) if wanted in pages else 0,
            label_visibility="collapsed",
        )

        st.divider()
        st.markdown("**Search in**")
        scope_label = st.radio(
            "Which documents", [COMPANY_SCOPE, PERSONAL_SCOPE], label_visibility="collapsed"
        )
        scope = "enterprise" if scope_label == COMPANY_SCOPE else "personal"
        department: str | None = None
        if scope == "enterprise":
            choice = st.selectbox("Only in department", [ALL_DEPARTMENTS, *ui.DEPARTMENTS.values()])
            if choice != ALL_DEPARTMENTS:
                department = next(code for code, name in ui.DEPARTMENTS.items() if name == choice)
            st.caption(
                "Narrows the search. You only ever get answers from documents you can access."
            )

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
        st.session_state.pop("conversation_id", None)  # deleted, or not yours
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


def target_label(code: str) -> str:
    if not code:
        return "My own documents (private)"
    return f"Company library · {ui.department_label(code)}"


def upload_section() -> None:
    user = current_user()
    targets = ui.upload_targets(user)
    st.subheader("Add documents")

    left, right = st.columns([3, 2])
    with left:
        files = st.file_uploader(
            "PDF, DOCX or TXT files", type=["pdf", "docx", "txt"], accept_multiple_files=True
        )
    with right:
        if len(targets) == 1:
            st.info("You can add documents to your own private library.", icon="🔒")
            target = ""
        else:
            target = st.selectbox("Add to", targets, format_func=target_label, key="upload-target")
        company_wide = False
        if target:
            company_wide = st.checkbox(
                "Visible to all employees (company-wide)",
                help="Otherwise only that department can see it.",
            )

    scope = "enterprise" if target else "personal"
    if st.button("Upload", type="primary", disabled=not files):
        send_files([(f.name, f.getvalue()) for f in files], scope, target or None, company_wide)

    if user["role"] == "super_admin" and SAMPLE_DIR.exists():
        if st.button("Load the 6 sample documents"):
            for name, (dept, wide) in ui.SAMPLE_DOCUMENTS.items():
                if (SAMPLE_DIR / name).exists():
                    send_files([(name, (SAMPLE_DIR / name).read_bytes())], "enterprise", dept, wide)


def send_files(
    files: list[tuple[str, bytes]], scope: str, department: str | None, company_wide: bool
) -> None:
    try:
        results = api().upload(files, scope, department, company_wide)
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
    except SessionExpired:
        session_ended()
        return
    st.subheader(f"Library ({len(documents)})")
    if not documents:
        st.caption("No documents you can access yet.")
        return
    user = current_user()
    for document in documents:
        label, color = ui.status_badge(document)
        with st.container(border=True):
            info, status, actions = st.columns([5, 3, 2])
            with info:
                name = ui.escape_markdown(document.get("title") or document["original_filename"])
                st.markdown(f"**{name}**")
                detail = [
                    ui.department_label(document["department"]),
                    ui.visibility_label(document),
                ]
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
                if ui.can_manage(user, document):
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


def documents_page() -> None:
    st.title("Documents")
    upload_section()
    st.divider()
    document_list()


# ----------------------------------------------------------------------------- users


def show_temporary_password(password: str) -> None:
    st.warning(
        "Give the person this temporary password now. It is shown only once and cannot be "
        "looked up later (you can reset it again if it is lost).",
        icon="\U0001f511",
    )
    st.code(password, language=None)


def show_new_account(created: dict[str, Any]) -> None:
    st.success(
        f"Created {created['email']}. Login ID: {created.get('login_id', '')}. "
        "They must choose their own password at first sign-in."
    )
    if created.get("temporary_password"):
        show_temporary_password(created["temporary_password"])


def create_user_form() -> None:
    with st.expander("➕ Add a user"), st.form("create-user", clear_on_submit=True):
        email = st.text_input("Email")
        full_name = st.text_input("Full name")
        role_label = st.selectbox("Role", list(ui.ROLES.values()))
        names = st.multiselect("Departments", list(ui.DEPARTMENTS.values()))
        login_id = st.text_input(
            "Login ID (optional)", help="Leave empty to make one from the name."
        )
        password = st.text_input(
            "Temporary password (optional)",
            type="password",
            help="Leave empty to generate a secure one. They must change it at first sign-in.",
        )
        if st.form_submit_button("Create user", type="primary"):
            role = next(code for code, label in ui.ROLES.items() if label == role_label)
            departments = [c for c, n in ui.DEPARTMENTS.items() if n in names]
            try:
                created = api().create_user(
                    email,
                    full_name,
                    password or None,
                    role,
                    departments,
                    login_id.strip() or None,
                )
            except ApiError as error:
                st.error(ui.error_message(error))
            else:
                show_new_account(created)


def user_row(person: dict[str, Any], me: str) -> None:
    with st.container(border=True):
        info, status, actions = st.columns([5, 2, 4])
        with info:
            st.markdown(f"**{ui.escape_markdown(person['full_name'])}**")
            places = ", ".join(ui.department_label(d) for d in person["departments"]) or "All"
            st.caption(
                f"Login ID: {person['login_id']} · {person['email']} · "
                f"{ui.role_label(person['role'])} · {places}"
            )
        with status:
            active = person["is_active"]
            st.badge("Active" if active else "Disabled", color="green" if active else "gray")
        with actions:
            if person["id"] != me:
                action = "Disable" if person["is_active"] else "Enable"
                if st.button(action, key=f"toggle-{person['id']}"):
                    try:
                        api().update_user(person["id"], is_active=not person["is_active"])
                    except ApiError as error:
                        st.error(ui.error_message(error))
                    st.rerun()
            with st.popover("Reset password"):
                new_password = st.text_input(
                    "New temporary password (optional)",
                    type="password",
                    help="Leave empty to generate one.",
                    key=f"pw-{person['id']}",
                )
                if st.button("Reset password", key=f"setpw-{person['id']}"):
                    try:
                        temporary = api().reset_password(person["id"], new_password or None)
                    except ApiError as error:
                        st.error(ui.error_message(error))
                    else:
                        st.success(
                            "Password reset. Their old sessions have ended and they must "
                            "choose a new password at next sign-in."
                        )
                        if temporary:
                            show_temporary_password(temporary)


def users_page() -> None:
    st.title("Users")
    st.caption("Super Admin only. Changes apply immediately, even to people who are signed in.")
    create_user_form()
    try:
        people = api().list_users()
    except ApiError as error:
        st.error(ui.error_message(error))
        return
    me = current_user()["user_id"]
    for person in people:
        user_row(person, me)


# ----------------------------------------------------------------------------- entry


def main() -> None:
    st.set_page_config(page_title="Apex Knowledge Assistant", page_icon="🏭", layout="wide")
    if not api().healthy():
        st.title("Apex Knowledge Assistant")
        st.error("The API is not running, so there is nothing to show yet.", icon="🔌")
        st.caption(f"Expected at `{base_url()}`")
        st.markdown("Start it from the repository root, then reload this page:")
        st.code("scripts\\dev.ps1", language="powershell")
        st.stop()

    if not api().is_logged_in:
        login_page()
        st.stop()

    if current_user().get("must_change_password"):
        password_change_page()
        st.stop()

    try:
        page, scope, department = sidebar()
        if page == CHAT_PAGE:
            chat_page(scope, department)
        elif page == DOCUMENTS_PAGE:
            documents_page()
        else:
            users_page()
    except SessionExpired:
        session_ended()


main()
