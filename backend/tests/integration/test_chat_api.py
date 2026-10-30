from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_chat_model_provider
from app.core.config import Settings, get_settings
from app.rag.generation import LlmError
from app.rag.generation.prompts import NOT_FOUND_MESSAGE
from tests.conftest import SAMPLE_DOCS, ScriptedChat

CHAT = "/api/v1/chat"
DOCS = "/api/v1/documents"
SAFETY = (SAMPLE_DOCS / "03_Factory_Safety_Manual.pdf").read_bytes()
QUESTION = "When can I re-enter an evacuated area?"


@pytest.fixture
def chat_client(client: TestClient, settings: Settings) -> TestClient:
    # Hashing scores are low, so open the relevance gate for these API-level tests.
    relaxed = settings.model_copy(update={"retrieval_min_score": 0.0})
    client.app.dependency_overrides[get_settings] = lambda: relaxed  # type: ignore[attr-defined]
    return client


def _upload(
    client: TestClient, content: bytes, scope: str = "enterprise", dept: str = "safety"
) -> None:
    data = {"scope": scope}
    if scope == "enterprise":
        data["department"] = dept
    response = client.post(DOCS, data=data, files=[("files", ("doc.pdf", content))])
    assert response.status_code == 201, response.text


def _use_model(client: TestClient, model: ScriptedChat) -> None:
    client.app.dependency_overrides[get_chat_model_provider] = lambda: lambda: model  # type: ignore[attr-defined]


def _ask(client: TestClient, **body: Any) -> Any:
    return client.post(CHAT, json={"question": QUESTION, **body})


def test_answer_with_sources(chat_client: TestClient) -> None:
    _upload(chat_client, SAFETY)
    model = ScriptedChat("Wait until the incident controller authorises it [1].")
    _use_model(chat_client, model)

    response = _ask(chat_client)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered"
    assert body["answer"] == "Wait until the incident controller authorises it [1]."
    assert body["reason"] is None
    source = body["sources"][0]
    assert source["doc_code"] == "APM-HSE-MAN-003"
    assert source["section_title"] == "6. Emergency response"
    assert (source["page_start"], source["page_end"]) == (2, 2)
    assert "Do not re-enter an evacuated area" in model.calls[0][1]["content"]


def test_nothing_indexed_means_not_found_without_calling_the_model(chat_client: TestClient) -> None:
    model = ScriptedChat("unused")
    _use_model(chat_client, model)
    body = _ask(chat_client).json()
    assert (body["status"], body["answer"], body["sources"], body["reason"]) == (
        "not_found",
        NOT_FOUND_MESSAGE,
        [],
        "no_relevant_context",
    )
    assert body["rewritten_question"] is None
    assert model.calls == []


def test_department_filter_applies_to_answers(chat_client: TestClient) -> None:
    _upload(chat_client, SAFETY, dept="safety")
    model = ScriptedChat("unused")
    _use_model(chat_client, model)
    body = _ask(chat_client, department="hr").json()
    assert body["status"] == "not_found"
    assert model.calls == []


def test_personal_documents_are_not_used_for_enterprise_questions(chat_client: TestClient) -> None:
    _upload(chat_client, SAFETY, scope="personal")
    model = ScriptedChat("unused")
    _use_model(chat_client, model)
    assert _ask(chat_client, scope="enterprise").json()["status"] == "not_found"
    assert model.calls == []

    personal = ScriptedChat("From my own file [1].")
    _use_model(chat_client, personal)
    assert _ask(chat_client, scope="personal").json()["status"] == "answered"


def test_missing_openai_key_gives_503(chat_client: TestClient) -> None:
    _upload(chat_client, SAFETY)  # indexing works offline, only the chat model needs a key
    response = _ask(chat_client)
    assert response.status_code == 503
    assert response.json() == {"detail": "llm_not_configured"}


def test_model_failure_gives_502(chat_client: TestClient) -> None:
    _upload(chat_client, SAFETY)
    _use_model(chat_client, ScriptedChat(error=LlmError("llm_request_failed")))
    response = _ask(chat_client)
    assert response.status_code == 502
    assert response.json() == {"detail": "llm_request_failed"}


def test_input_is_validated(chat_client: TestClient) -> None:
    assert chat_client.post(CHAT, json={"question": ""}).status_code == 422
    assert chat_client.post(CHAT, json={"question": "x" * 2001}).status_code == 422
    assert chat_client.post(CHAT, json={"question": "x", "scope": "other"}).status_code == 422
    assert _ask(chat_client, department="finance").status_code == 422
