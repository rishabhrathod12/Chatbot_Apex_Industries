"""Asking questions and reading saved conversations."""

import re
from typing import Any

import streamlit as st

import ui
from api_client import ApiClient, ApiError, ApiUnreachable

_CONVERSATION_ID = re.compile(r"^[0-9a-f]{32}$")


def sidebar_conversations(client: ApiClient) -> None:
    if st.button("➕ New chat", use_container_width=True):
        st.session_state.pop("conversation_id", None)
        st.rerun()
    try:
        conversations = client.list_conversations()
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
    if current and st.button("\U0001f5d1 Delete this chat", use_container_width=True):
        try:
            client.delete_conversation(current)
        except ApiError:
            pass
        st.session_state.pop("conversation_id", None)
        st.rerun()


def render_sources(sources: list[dict[str, Any]]) -> None:
    with st.expander(f"Sources ({len(sources)})", expanded=False):
        for source in sources:
            if source.get("available") is False:
                st.markdown(
                    f"**[{source['number']}]** _This source is no longer available to you._"
                )
                continue
            st.markdown(f"**[{source['number']}]** {ui.escape_markdown(ui.source_title(source))}")
            if source.get("doc_code"):
                st.caption(f"{source['doc_code']} · {source.get('source_name', '')}")
            if source.get("quote"):
                st.markdown("> " + ui.escape_markdown(source["quote"]))


def render_message(message: dict[str, Any]) -> None:
    if message["role"] == "user":
        with st.chat_message("user", avatar="\U0001f464"):
            st.markdown(ui.escape_markdown(message["content"]))
        return
    with st.chat_message("assistant", avatar="\U0001f3ed"):
        if message.get("status") == "not_found":
            st.info(message["content"], icon="\U0001f50e")
            st.caption("Nothing in the documents you can access answers this.")
        else:
            st.markdown(ui.safe_markdown(message["content"]))
        if message.get("rewritten_question"):
            st.caption(f"Searched as: *{ui.escape_markdown(message['rewritten_question'])}*")
        if message.get("sources"):
            render_sources(message["sources"])


def load_messages(client: ApiClient) -> list[dict[str, Any]]:
    # A link like ?c=<conversation id> opens that conversation (bookmarkable and shareable).
    linked = st.query_params.get("c", "")
    if "conversation_id" not in st.session_state and _CONVERSATION_ID.match(linked):
        st.session_state["conversation_id"] = linked
    conversation_id = st.session_state.get("conversation_id")
    if not conversation_id:
        return []
    try:
        return list(client.get_conversation(conversation_id)["messages"])
    except ApiError:
        st.session_state.pop("conversation_id", None)  # deleted, or not yours
        return []


def chat_page(
    client: ApiClient,
    scope: str,
    department: str | None,
    *,
    title: str | None,
    intro: str,
    examples: bool = False,
) -> None:
    if title:
        st.title(title)
    messages = load_messages(client)

    if not messages:
        st.markdown(intro)
        if examples:
            columns = st.columns(2)
            for index, example in enumerate(ui.EXAMPLE_QUESTIONS):
                if columns[index % 2].button(
                    example, key=f"example-{index}", use_container_width=True
                ):
                    st.session_state["pending_question"] = example
                    st.rerun()

    for message in messages:
        render_message(message)

    typed = st.chat_input("Ask about a procedure, policy or manual…")
    question: str | None = typed or st.session_state.pop("pending_question", None)
    if not question:
        return

    with st.chat_message("user", avatar="\U0001f464"):
        st.markdown(ui.escape_markdown(question))
    with (
        st.chat_message("assistant", avatar="\U0001f3ed"),
        st.spinner("Searching the documents…"),
    ):
        try:
            reply = client.chat(
                question, scope, department, st.session_state.get("conversation_id")
            )
        except ApiError as error:
            if error.detail == "conversation_not_found":
                st.session_state.pop("conversation_id", None)
            st.error(ui.error_message(error), icon="⚠️")
            return
        except ApiUnreachable:
            st.error("Cannot reach the API. Is it running?", icon="\U0001f50c")
            return
    st.session_state["conversation_id"] = reply["conversation_id"]
    st.rerun()


# ----------------------------------------------------------------------------- history


def _continue_in(conversation_id: str, page: str) -> None:
    st.session_state["conversation_id"] = conversation_id
    st.session_state["keep_conversation"] = True  # the page change below is on purpose
    st.session_state["nav"] = page


def history_page(client: ApiClient, personal_page: str, company_page: str) -> None:
    """Every saved conversation, newest first. Open one to read it; continue it where you like."""
    st.title("Conversation history")
    st.caption("Only you can see your conversations.")
    conversations = client.list_conversations()
    if not conversations:
        st.info("No conversations yet. Ask a question and it will be saved here.")
        return
    opened = st.session_state.get("history_open")
    for item in conversations:
        with st.container(border=True):
            left, right = st.columns([5, 2])
            with left:
                st.markdown(f"**{ui.escape_markdown(item['title'])}**")
                st.caption(f"Last message {ui.short_time(item.get('updated_at'))}")
            with right:
                is_open = opened == item["id"]
                if st.button(
                    "Close" if is_open else "Read",
                    key=f"read-{item['id']}",
                    use_container_width=True,
                ):
                    st.session_state["history_open"] = None if is_open else item["id"]
                    st.rerun()
            if opened == item["id"]:
                try:
                    messages = client.get_conversation(item["id"])["messages"]
                except ApiError as error:
                    st.error(ui.error_message(error))
                    continue
                for message in messages:
                    render_message(message)
                first, second, third = st.columns(3)
                first.button(
                    "Continue in My Documents",
                    key=f"c-mine-{item['id']}",
                    on_click=_continue_in,
                    args=(item["id"], personal_page),
                    use_container_width=True,
                )
                second.button(
                    "Continue in Company Knowledge",
                    key=f"c-company-{item['id']}",
                    on_click=_continue_in,
                    args=(item["id"], company_page),
                    use_container_width=True,
                )
                if third.button("Delete", key=f"del-chat-{item['id']}", use_container_width=True):
                    try:
                        client.delete_conversation(item["id"])
                    except ApiError:
                        pass
                    st.session_state["history_open"] = None
                    st.rerun()
