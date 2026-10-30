import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import get_chat_model_provider, get_current_user_id
from app.core.config import Settings, get_settings
from app.core.database import get_session_factory
from app.models import Message
from app.rag.generation import LlmError
from app.rag.generation.prompts import NOT_FOUND_MESSAGE
from app.services import chat as chat_service
from tests.conftest import SAMPLE_DOCS, ScriptedChat

CHAT = "/api/v1/chat"
CONVERSATIONS = "/api/v1/conversations"
DOCS = "/api/v1/documents"
SAFETY = (SAMPLE_DOCS / "03_Factory_Safety_Manual.pdf").read_bytes()
Q1 = "When can I re-enter an evacuated area?"
Q2 = "And who gives that permission?"


@pytest.fixture
def chat_client(client: TestClient, settings: Settings) -> TestClient:
    # Hashing scores are low, so open the relevance gate for these API-level tests.
    relaxed = settings.model_copy(update={"retrieval_min_score": 0.0})
    client.app.dependency_overrides[get_settings] = lambda: relaxed  # type: ignore[attr-defined]
    response = client.post(
        DOCS,
        data={"scope": "enterprise", "department": "safety"},
        files=[("files", ("safety.pdf", SAFETY))],
    )
    assert response.status_code == 201
    return client


def _use(client: TestClient, model: ScriptedChat) -> None:
    client.app.dependency_overrides[get_chat_model_provider] = lambda: lambda: model  # type: ignore[attr-defined]


def _act_as(client: TestClient, user: str) -> None:
    client.app.dependency_overrides[get_current_user_id] = lambda: user  # type: ignore[attr-defined]


def _ask(client: TestClient, question: str = Q1, **body: Any) -> Any:
    return client.post(CHAT, json={"question": question, **body})


# ---------- saving ----------


def test_first_question_starts_a_saved_conversation(chat_client: TestClient) -> None:
    _use(chat_client, ScriptedChat("Wait for the incident controller [1]."))
    body = _ask(chat_client).json()
    assert re.fullmatch(r"[0-9a-f]{32}", body["conversation_id"])
    assert body["message_id"]
    assert body["rewritten_question"] is None

    listing = chat_client.get(CONVERSATIONS).json()
    assert [(c["id"], c["title"]) for c in listing] == [(body["conversation_id"], Q1)]

    detail = chat_client.get(f"{CONVERSATIONS}/{body['conversation_id']}").json()
    user_msg, assistant_msg = detail["messages"]
    assert (user_msg["role"], user_msg["content"]) == ("user", Q1)
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["status"] == "answered"
    assert assistant_msg["id"] == body["message_id"]
    source = assistant_msg["sources"][0]
    assert source["doc_code"] == "APM-HSE-MAN-003"
    assert source["quote"]


def test_refusals_are_saved_without_sources(chat_client: TestClient) -> None:
    _use(chat_client, ScriptedChat("NOT_FOUND"))
    body = _ask(chat_client).json()
    detail = chat_client.get(f"{CONVERSATIONS}/{body['conversation_id']}").json()
    reply = detail["messages"][1]
    assert (reply["status"], reply["reason"]) == ("not_found", "model_declined")
    assert reply["content"] == NOT_FOUND_MESSAGE
    assert reply["sources"] == []


def test_a_model_failure_leaves_no_conversation_behind(chat_client: TestClient) -> None:
    _use(chat_client, ScriptedChat(error=LlmError("llm_request_failed")))
    assert _ask(chat_client).status_code == 502
    assert chat_client.get(CONVERSATIONS).json() == []


def test_conversations_are_listed_newest_first(chat_client: TestClient) -> None:
    _use(chat_client, ScriptedChat("ok [1]"))
    first = _ask(chat_client, "first question about guards").json()["conversation_id"]
    second = _ask(chat_client, "second question about chemicals").json()["conversation_id"]
    assert [c["id"] for c in chat_client.get(CONVERSATIONS).json()] == [second, first]

    _ask(chat_client, "another one", conversation_id=first)  # touching the first moves it up
    assert [c["id"] for c in chat_client.get(CONVERSATIONS).json()] == [first, second]


def test_long_titles_are_cut(chat_client: TestClient) -> None:
    _use(chat_client, ScriptedChat("ok [1]"))
    _ask(chat_client, "word " * 100)
    assert len(chat_client.get(CONVERSATIONS).json()[0]["title"]) <= 60


# ---------- follow-ups ----------


def test_follow_up_is_rewritten_and_only_the_rewrite_reaches_the_answer_prompt(
    chat_client: TestClient,
) -> None:
    rewrite = "Who authorizes re-entry to an evacuated area?"
    model = ScriptedChat(
        ["Wait for the incident controller [1].", rewrite, "The incident controller [1]."]
    )
    _use(chat_client, model)

    first = _ask(chat_client).json()
    second = _ask(chat_client, Q2, conversation_id=first["conversation_id"]).json()

    assert second["conversation_id"] == first["conversation_id"]
    assert second["rewritten_question"] == rewrite
    assert second["status"] == "answered"

    assert len(model.calls) == 3  # answer, rewrite, answer
    rewrite_prompt = model.calls[1][1]["content"]
    assert Q1 in rewrite_prompt  # the rewrite step sees the history
    assert f"Latest message: {Q2}" in rewrite_prompt
    answer_prompt = model.calls[2][1]["content"]
    assert f"Question: {rewrite}" in answer_prompt
    assert Q1 not in answer_prompt  # the answering step never sees the history
    assert Q2 not in answer_prompt

    detail = chat_client.get(f"{CONVERSATIONS}/{first['conversation_id']}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    assert detail["messages"][2]["content"] == Q2  # the user's own words are what is stored
    assert detail["messages"][3]["rewritten_question"] == rewrite


def test_first_message_needs_no_rewrite_call(chat_client: TestClient) -> None:
    model = ScriptedChat("ok [1]")
    _use(chat_client, model)
    _ask(chat_client)
    assert len(model.calls) == 1


def test_unchanged_rewrite_is_not_reported_as_a_rewrite(chat_client: TestClient) -> None:
    model = ScriptedChat(["ok [1]", Q2, "ok [1]"])
    _use(chat_client, model)
    cid = _ask(chat_client).json()["conversation_id"]
    assert _ask(chat_client, Q2, conversation_id=cid).json()["rewritten_question"] is None


def test_follow_up_still_works_when_the_rewrite_step_fails(chat_client: TestClient) -> None:
    model = ScriptedChat(["ok [1]", "unused", "Answer [1]."], fail_on_calls={1})
    _use(chat_client, model)
    cid = _ask(chat_client).json()["conversation_id"]
    second = _ask(chat_client, Q2, conversation_id=cid).json()
    assert second["status"] == "answered"
    assert second["rewritten_question"] is None
    assert f"Question: {Q2}" in model.calls[2][1]["content"]  # searched as typed


def test_only_the_configured_number_of_turns_reaches_the_rewrite_step(
    chat_client: TestClient, settings: Settings
) -> None:
    one_turn = settings.model_copy(update={"retrieval_min_score": 0.0, "chat_history_turns": 1})
    chat_client.app.dependency_overrides[get_settings] = lambda: one_turn  # type: ignore[attr-defined]
    # Call order: answer 1 | rewrite 2, answer 2 | rewrite 3, answer 3
    model = ScriptedChat(["a [1]", "rewrite two", "b [1]", "rewrite three", "c [1]"])
    _use(chat_client, model)
    cid = _ask(chat_client, "question number one").json()["conversation_id"]
    _ask(chat_client, "question number two", conversation_id=cid)
    _ask(chat_client, "question number three", conversation_id=cid)
    last_rewrite_prompt = model.calls[3][1]["content"]
    assert "question number two" in last_rewrite_prompt
    assert "question number one" not in last_rewrite_prompt


def test_permission_filter_still_applies_on_follow_ups(chat_client: TestClient) -> None:
    model = ScriptedChat(["ok [1]", "Who gives permission to re-enter?", "unused"])
    _use(chat_client, model)
    cid = _ask(chat_client, department="safety").json()["conversation_id"]
    second = _ask(chat_client, Q2, conversation_id=cid, department="hr").json()
    assert second["status"] == "not_found"
    assert len(model.calls) == 2  # the rewrite ran, the answer step was skipped


# ---------- isolation ----------


def test_other_users_cannot_see_continue_or_delete_a_conversation(chat_client: TestClient) -> None:
    _use(chat_client, ScriptedChat("ok [1]"))
    cid = _ask(chat_client).json()["conversation_id"]

    _act_as(chat_client, "mallory")
    assert chat_client.get(CONVERSATIONS).json() == []
    assert chat_client.get(f"{CONVERSATIONS}/{cid}").status_code == 404
    assert chat_client.delete(f"{CONVERSATIONS}/{cid}").status_code == 404
    stolen = _ask(chat_client, Q2, conversation_id=cid)
    assert stolen.status_code == 404
    assert stolen.json() == {"detail": "conversation_not_found"}

    missing = chat_client.get(f"{CONVERSATIONS}/{'0' * 32}")
    assert missing.status_code == 404
    assert missing.json() == stolen.json()  # indistinguishable from "does not exist"

    _act_as(chat_client, "dev-user")  # the owner is unaffected
    detail = chat_client.get(f"{CONVERSATIONS}/{cid}").json()
    assert len(detail["messages"]) == 2


def test_each_user_gets_a_separate_list(chat_client: TestClient) -> None:
    _use(chat_client, ScriptedChat("ok [1]"))
    mine = _ask(chat_client, "alice question").json()["conversation_id"]
    _act_as(chat_client, "bob")
    theirs = _ask(chat_client, "bob question").json()["conversation_id"]
    assert [c["id"] for c in chat_client.get(CONVERSATIONS).json()] == [theirs]
    _act_as(chat_client, "dev-user")
    assert [c["id"] for c in chat_client.get(CONVERSATIONS).json()] == [mine]


# ---------- housekeeping ----------


def test_delete_removes_the_conversation_and_its_messages(
    chat_client: TestClient, settings: Settings
) -> None:
    _use(chat_client, ScriptedChat("ok [1]"))
    cid = _ask(chat_client).json()["conversation_id"]
    assert chat_client.delete(f"{CONVERSATIONS}/{cid}").status_code == 204
    assert chat_client.get(f"{CONVERSATIONS}/{cid}").status_code == 404
    with get_session_factory(settings.database_url)() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 0


def test_bad_conversation_ids_are_rejected(chat_client: TestClient) -> None:
    assert _ask(chat_client, conversation_id="not-a-valid-id").status_code == 422
    assert _ask(chat_client, conversation_id="../../etc/passwd").status_code == 422


def test_conversation_length_is_capped(
    chat_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat_service, "MAX_MESSAGES_PER_CONVERSATION", 4)
    _use(chat_client, ScriptedChat("ok [1]"))
    cid = _ask(chat_client).json()["conversation_id"]
    assert _ask(chat_client, Q2, conversation_id=cid).status_code == 200  # now 4 messages
    full = _ask(chat_client, Q2, conversation_id=cid)
    assert full.status_code == 409
    assert full.json() == {"detail": "conversation_too_long"}
