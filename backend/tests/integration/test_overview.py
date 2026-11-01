"""Requests about a whole document ("summarise this", "key points of the manual").

They used to come back as "not found", because no single passage answers them. They are now
answered from excerpts spread across the document, through the same permission filter as search.
"""

import re
from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_chat_model_provider, get_current_principal
from app.core.config import Settings, get_settings
from app.core.permissions import Role
from app.rag.generation import answer_question
from app.rag.generation.llm import ChatMessage
from app.rag.generation.overview import (
    OVERVIEW_PROMPT,
    _named_documents,
    _sample,
    is_overview_question,
)
from app.rag.generation.prompts import NOT_FOUND_MESSAGE, SYSTEM_PROMPT
from app.rag.vectorstore import SearchFilter, StoredChunk
from tests.conftest import SAMPLE_DOCS, IndexedSamples, act_as_principal

DOC_CODES = {
    "APM-HSE-MAN-003",
    "APM-PROD-SOP-004",
    "APM-PROD-MAN-001",
    "APM-HR-POL-005",
    "APM-MNT-MAN-002",
    "APM-QLT-SOP-006",
}


class Chat:
    """A stand-in model that records what it was sent."""

    def __init__(self, reply: str = "It covers safety rules [1].") -> None:
        self.reply = reply
        self.prompts: list[list[ChatMessage]] = []

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        self.prompts.append(list(messages))
        return self.reply

    @property
    def sent(self) -> str:
        return self.prompts[-1][1]["content"] if self.prompts else ""

    def codes_sent(self) -> set[str]:
        return {code for code in DOC_CODES if code in self.sent}


def ask(samples: IndexedSamples, chat: Chat, question: str, **kw: object):  # noqa: ANN202
    options: dict[str, object] = {"overview": True}
    options.update(kw)
    return answer_question(
        question,
        embedder=samples.embedder,
        store=samples.store,
        chat_model=chat,
        **options,  # type: ignore[arg-type]
    )


# ---------- what counts as a request about a whole document ----------


@pytest.mark.parametrize(
    "question",
    [
        "key points of document",
        "summary of documents",
        "Summarise this",
        "give me a summary",
        "key points please",
        "overview of the documents",
        "What is this document about?",
        "what does the safety manual cover?",
        "Summary of the maintenance manual",
        "Can you summarize the HR policy?",
        "what are the main points of the SOP",
        "tl;dr",
    ],
)
def test_requests_about_a_whole_document_are_recognised(question: str) -> None:
    assert is_overview_question(question)


@pytest.mark.parametrize(
    "question",
    [
        "Key points to check before operating a CNC machine",
        "Summarize the steps for lockout",
        "What does the safety manual say about gloves?",
        "What are the rules in the manual about gloves?",
        "How do I report an injury?",
        "When can I re-enter an evacuated area?",
        "What PPE do I need?",
        "",
    ],
)
def test_questions_about_content_are_not_mistaken_for_them(question: str) -> None:
    assert not is_overview_question(question)


# ---------- choosing and sampling ----------


def _chunk(
    doc: str, index: int, text: str, page: int = 1, title: str = "", code: str = ""
) -> StoredChunk:
    meta: dict[str, str | int] = {
        "document_id": doc,
        "chunk_index": index,
        "page_start": page,
        "doc_title": title,
        "doc_code": code,
        "source_name": f"{doc}.pdf",
    }
    return StoredChunk(f"{doc}:{index}", text, meta)


def test_everything_is_used_when_it_fits_and_in_reading_order() -> None:
    chunks = [_chunk("d", 2, "c", page=2), _chunk("d", 0, "a"), _chunk("d", 1, "b")]
    picked, left_out = _sample(chunks, budget=100)
    assert [c.text for c in picked] == ["a", "b", "c"] and not left_out


def test_a_long_document_is_sampled_across_its_length_within_budget() -> None:
    chunks = [
        _chunk("d", i, f"{i:03d}" + "x" * 97, page=1 + i // 5) for i in range(40)
    ]  # 4000 chars
    picked, left_out = _sample(chunks, budget=1000)
    texts = [c.text[:3] for c in picked]
    assert left_out
    assert sum(len(c.text) for c in picked) <= 1000
    assert texts[0] == "000"  # always starts with the first chunk
    assert texts == sorted(texts) and int(texts[-1]) >= 30  # in order, and reaches the later pages


def test_a_document_is_named_by_its_code_or_its_distinctive_title_words() -> None:
    docs = {
        "a": [_chunk("a", 0, "t", title="Factory Health & Safety Manual", code="APM-HSE-MAN-003")],
        "b": [_chunk("b", 0, "t", title="CNC Machine Operation Manual", code="APM-PROD-MAN-001")],
        "c": [
            _chunk("c", 0, "t", title="Employee Attendance & Leave Policy", code="APM-HR-POL-005")
        ],
    }
    assert _named_documents("summary of APM-PROD-MAN-001", docs) == ["b"]
    assert _named_documents("summarise the machine operation manual", docs) == ["b"]
    assert _named_documents("key points of the leave policy", docs) == ["c"]
    assert _named_documents("key points of document", docs) == []  # names none
    assert _named_documents("summary of the manual", docs) == []  # "manual" is too generic


# ---------- answering ----------


def test_a_summary_of_documents_is_answered_instead_of_not_found(
    indexed_samples: IndexedSamples,
) -> None:
    chat = Chat()
    result = ask(indexed_samples, chat, "summary of documents")
    assert result.status == "answered" and result.reason is None
    assert len(chat.prompts) == 1 and chat.prompts[0][0]["content"] == OVERVIEW_PROMPT
    assert chat.codes_sent() == DOC_CODES  # all six documents, not a few best-matching passages
    assert result.sources and all(s.doc_code in DOC_CODES for s in result.sources)
    assert result.sources[0].quote  # copied from the cited chunk


def test_key_points_of_document_is_answered_too(indexed_samples: IndexedSamples) -> None:
    chat = Chat()
    assert ask(indexed_samples, chat, "key points of document").status == "answered"


def test_a_named_document_is_summarised_alone(indexed_samples: IndexedSamples) -> None:
    chat = Chat()
    result = ask(indexed_samples, chat, "give me the key points of the factory safety manual")
    assert result.status == "answered"
    assert chat.codes_sent() == {"APM-HSE-MAN-003"}
    assert "Showing" not in result.answer


def test_only_documents_the_person_may_read_are_ever_sent_to_the_model(
    indexed_samples: IndexedSamples,
) -> None:
    chat = Chat()
    result = ask(
        indexed_samples,
        chat,
        "summary of documents",
        search_filter=SearchFilter(departments=["safety"], include_company_wide=False),
    )
    assert result.status == "answered"
    assert chat.codes_sent() == {"APM-HSE-MAN-003"}
    assert {s.doc_code for s in result.sources} == {"APM-HSE-MAN-003"}


def test_naming_a_document_that_is_out_of_reach_reveals_nothing(
    indexed_samples: IndexedSamples,
) -> None:
    chat = Chat()
    result = ask(
        indexed_samples,
        chat,
        "key points of APM-HR-POL-005",  # an HR document, asked by someone who only sees safety
        search_filter=SearchFilter(departments=["safety"], include_company_wide=False),
    )
    excerpts = chat.sent.split("</context>")[0]  # the question itself is echoed after this
    assert "APM-HR-POL-005" not in excerpts and "leave" not in excerpts.lower()
    assert {s.doc_code for s in result.sources} <= {"APM-HSE-MAN-003"}


@pytest.mark.parametrize(
    "search_filter",
    [SearchFilter(departments=[]), SearchFilter(owner_id="nobody-with-documents")],
)
def test_a_person_with_no_documents_gets_not_found_and_no_model_call(
    indexed_samples: IndexedSamples, search_filter: SearchFilter
) -> None:
    chat = Chat()
    result = ask(indexed_samples, chat, "summary of documents", search_filter=search_filter)
    assert (result.status, result.reason, result.answer) == (
        "not_found",
        "no_relevant_context",
        NOT_FOUND_MESSAGE,
    )
    assert chat.prompts == []


def test_more_documents_than_the_limit_are_cut_with_a_clear_note(
    indexed_samples: IndexedSamples,
) -> None:
    chat = Chat()
    result = ask(indexed_samples, chat, "summary of documents", overview_max_documents=2)
    assert len(chat.codes_sent()) == 2
    assert "Showing 2 of 6 documents" in result.answer


def test_a_small_budget_samples_the_documents_and_says_so(indexed_samples: IndexedSamples) -> None:
    chat = Chat()
    result = ask(indexed_samples, chat, "key points of APM-PROD-MAN-001", overview_max_chars=4000)
    assert chat.codes_sent() == {"APM-PROD-MAN-001"}
    assert "not every page" not in result.answer  # the document fits in 4000 characters
    small = ask(indexed_samples, Chat(), "key points of APM-PROD-MAN-001", overview_max_chars=1000)
    assert "not every page" not in small.answer  # the floor of 4000 characters per document applies


def test_the_excerpts_in_the_prompt_are_numbered_for_citation(
    indexed_samples: IndexedSamples,
) -> None:
    chat = Chat()
    ask(indexed_samples, chat, "key points of APM-PROD-MAN-001")
    numbers = re.findall(r"^\[(\d+)\] ", chat.sent, re.MULTILINE)
    assert numbers == [str(i) for i in range(1, len(numbers) + 1)] and len(numbers) >= 2


# ---------- the same checks as every other answer ----------


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ("A summary with no citations at all.", "uncited_answer"),
        ("A summary pointing at nothing real [99].", "uncited_answer"),
        ("NOT_FOUND", "model_declined"),
        ("   ", "empty_answer"),
    ],
)
def test_an_unusable_summary_is_refused_like_any_other_answer(
    indexed_samples: IndexedSamples, reply: str, reason: str
) -> None:
    result = ask(indexed_samples, Chat(reply), "summary of documents")
    assert (result.status, result.reason) == ("not_found", reason)


def test_citation_markers_that_point_nowhere_are_removed(indexed_samples: IndexedSamples) -> None:
    result = ask(indexed_samples, Chat("Real [1]. Invented [99]."), "summary of documents")
    assert result.status == "answered" and "[99]" not in result.answer


# ---------- when it must not apply ----------


def test_a_question_about_content_still_takes_the_normal_route(
    indexed_samples: IndexedSamples,
) -> None:
    chat = Chat("Wait until authorised [1].")
    ask(indexed_samples, chat, "When can I re-enter an evacuated area?", min_score=0.0, top_k=3)
    assert chat.prompts[0][0]["content"] == SYSTEM_PROMPT


def test_it_can_be_switched_off(indexed_samples: IndexedSamples) -> None:
    chat = Chat()
    ask(indexed_samples, chat, "summary of documents", overview=False)
    assert all(p[0]["content"] != OVERVIEW_PROMPT for p in chat.prompts)


# ---------- through the API, as a real person with real permissions ----------


@pytest.fixture
def library(client: TestClient) -> Chat:
    """A production manual and a safety manual, uploaded by an admin; returns the stand-in model."""
    chat = Chat()
    client.app.dependency_overrides[get_chat_model_provider] = lambda: lambda: chat  # type: ignore[attr-defined]
    for filename, department in (
        ("01_Machine_Operation_Manual.pdf", "production"),
        ("03_Factory_Safety_Manual.pdf", "safety"),
    ):
        response = client.post(
            "/api/v1/documents",
            data={"scope": "enterprise", "department": department, "visibility": "department"},
            files=[("files", (filename, (SAMPLE_DOCS / filename).read_bytes()))],
        )
        assert response.status_code == 201
    return chat


def act_as(client: TestClient, role: Role, *departments: str) -> None:
    client.app.dependency_overrides[get_current_principal] = lambda: act_as_principal(  # type: ignore[attr-defined]
        "someone", role, frozenset(departments)
    )


def test_an_employee_gets_an_overview_of_only_their_own_departments_documents(
    client: TestClient, library: Chat
) -> None:
    act_as(client, Role.EMPLOYEE, "production")
    body = client.post("/api/v1/chat", json={"question": "key points of document"}).json()
    assert body["status"] == "answered"
    assert {s["doc_code"] for s in body["sources"]} == {"APM-PROD-MAN-001"}
    assert library.codes_sent() == {"APM-PROD-MAN-001"}  # the safety manual never reached the model


def test_an_employee_in_another_department_gets_the_other_overview(
    client: TestClient, library: Chat
) -> None:
    act_as(client, Role.EMPLOYEE, "safety")
    body = client.post("/api/v1/chat", json={"question": "summary of documents"}).json()
    assert {s["doc_code"] for s in body["sources"]} == {"APM-HSE-MAN-003"}
    assert library.codes_sent() == {"APM-HSE-MAN-003"}


def test_an_employee_with_no_matching_department_gets_nothing_to_summarise(
    client: TestClient, library: Chat
) -> None:
    act_as(client, Role.EMPLOYEE, "hr")
    body = client.post("/api/v1/chat", json={"question": "summary of documents"}).json()
    assert (body["status"], body["reason"]) == ("not_found", "no_relevant_context")
    assert library.prompts == []


def test_an_admin_gets_an_overview_of_all_company_documents(
    client: TestClient, library: Chat
) -> None:
    body = client.post("/api/v1/chat", json={"question": "summary of documents"}).json()
    assert body["status"] == "answered"
    assert library.codes_sent() == {"APM-PROD-MAN-001", "APM-HSE-MAN-003"}


def test_my_documents_never_include_someone_elses(client: TestClient, library: Chat) -> None:
    act_as(client, Role.EMPLOYEE, "production")
    body = client.post(
        "/api/v1/chat", json={"question": "summary of documents", "scope": "personal"}
    ).json()
    assert (body["status"], body["reason"]) == (
        "not_found",
        "no_relevant_context",
    )  # none of theirs
    assert library.prompts == []


def test_the_overview_can_be_switched_off_in_settings(
    client: TestClient, library: Chat, settings: Settings
) -> None:
    off = settings.model_copy(update={"overview_enabled": False})
    client.app.dependency_overrides[get_settings] = lambda: off  # type: ignore[attr-defined]
    client.post("/api/v1/chat", json={"question": "summary of documents"})
    assert all(prompt[0]["content"] != OVERVIEW_PROMPT for prompt in library.prompts)


# ---------- long replies ----------


class ResizableChat(Chat):
    """A model that, like the real one, can be asked for a copy with a larger reply limit."""

    def __init__(self, reply: str = "It covers safety rules [1].") -> None:
        super().__init__(reply)
        self.limits: list[int] = []

    def with_max_tokens(self, max_tokens: int) -> "ResizableChat":
        self.limits.append(max_tokens)
        return self


def test_an_overview_asks_for_a_larger_reply_limit_than_a_normal_answer(
    indexed_samples: IndexedSamples,
) -> None:
    chat = ResizableChat()
    ask(indexed_samples, chat, "summary of documents", overview_max_answer_tokens=1234)
    assert chat.limits == [1234]
    assert len(chat.prompts) == 1


def test_a_normal_question_never_asks_for_the_larger_limit(indexed_samples: IndexedSamples) -> None:
    chat = ResizableChat("Wait [1].")
    ask(indexed_samples, chat, "When can I re-enter an evacuated area?", min_score=0.0, top_k=3)
    assert chat.limits == []


def test_a_model_that_cannot_be_resized_is_still_used(indexed_samples: IndexedSamples) -> None:
    assert ask(indexed_samples, Chat(), "summary of documents").status == "answered"


def test_the_openai_model_makes_a_copy_with_the_new_limit() -> None:
    from app.rag.generation.llm import OpenAIChatModel

    seen: list[int] = []

    class Client:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs: object) -> object:
                    seen.append(int(kwargs["max_completion_tokens"]))  # type: ignore[call-overload]
                    message = type("M", (), {"content": "ok"})()
                    return type(
                        "R",
                        (),
                        {
                            "choices": [
                                type("C", (), {"message": message, "finish_reason": "stop"})()
                            ]
                        },
                    )()

    model = OpenAIChatModel("key", "m", max_tokens=600, client=Client())
    bigger = model.with_max_tokens(2000)
    model.complete([{"role": "user", "content": "x"}])
    bigger.complete([{"role": "user", "content": "x"}])
    assert seen == [600, 2000] and bigger is not model
