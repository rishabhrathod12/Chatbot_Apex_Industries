"""The staff workspace home page."""

from typing import Any

import streamlit as st

import ui
from api_client import ApiClient, ApiError


def _go(page: str) -> None:
    st.session_state["nav"] = page


def _open_history(conversation_id: str) -> None:
    st.session_state["history_open"] = conversation_id
    st.session_state["nav"] = ui.HISTORY


def dashboard_page(client: ApiClient, user: dict[str, Any]) -> None:
    first_name = user["full_name"].split()[0] if user["full_name"].split() else user["full_name"]
    st.title(f"Welcome, {first_name}")
    places = ", ".join(ui.department_label(d) for d in user["departments"]) or "all departments"
    st.caption(f"{ui.role_label(user['role'])} · {places}")

    try:
        documents = client.list_documents()
        conversations = client.list_conversations()
    except ApiError as error:
        st.error(ui.error_message(error))
        return
    mine = [d for d in documents if d["scope"] == "personal"]
    company = [d for d in documents if d["scope"] == "enterprise" and d["status"] == "ready"]
    ready = sum(d["status"] == "ready" for d in mine)
    working = sum(d["status"] in {"uploaded", "processing"} for d in mine)
    failed = [d for d in mine if d["status"] == "failed"]

    columns = st.columns(4)
    columns[0].metric("My documents", len(mine))
    columns[1].metric("Ready to ask about", ready)
    columns[2].metric("Company documents for me", len(company))
    columns[3].metric("Conversations", len(conversations))
    if working:
        st.info(f"{working} of your documents are still being processed.", icon="⏳")
    if failed:
        names = ", ".join(ui.escape_markdown(d["original_filename"]) for d in failed)
        st.warning(f"Could not process: {names}. Open *My Documents* for details.", icon="⚠️")

    st.subheader("What would you like to do?")
    first, second, third = st.columns(3)
    first.button(
        "Upload a PDF",
        on_click=_go,
        args=(ui.UPLOAD_PDF,),
        use_container_width=True,
        key="go-upload",
    )
    second.button(
        "Chat with my documents",
        on_click=_go,
        args=(ui.CHAT_MINE,),
        use_container_width=True,
        key="go-chat",
    )
    third.button(
        "Ask the company knowledge",
        on_click=_go,
        args=(ui.COMPANY_KNOWLEDGE,),
        use_container_width=True,
        key="go-company",
    )

    st.subheader("Recent conversations")
    if not conversations:
        st.caption("Nothing yet. Ask a question and it will be saved here.")
    for item in conversations[:5]:
        left, right = st.columns([5, 1])
        left.markdown(ui.escape_markdown(item["title"]))
        right.button(
            "Open",
            key=f"recent-{item['id']}",
            on_click=_open_history,
            args=(item["id"],),
            use_container_width=True,
        )
