"""Tests for the Streamlit frontend: the API client, the display helpers, and the page itself
(driven with Streamlit's own test runner and a fake API, so no server or browser is needed)."""

from pathlib import Path
from typing import Any

import httpx
import pytest
from streamlit.testing.v1 import AppTest

import api_client
import ui
from api_client import ApiClient, ApiError, ApiUnreachable

APP = str(Path(__file__).resolve().parents[3] / "frontend" / "streamlit_app.py")

# ---------- API client ----------


def _client(handler: Any) -> ApiClient:
    transport = httpx.MockTransport(handler)
    return ApiClient("http://api.test/api/v1", httpx.Client(transport=transport))


def test_client_sends_chat_requests_and_reads_answers() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        return httpx.Response(200, json={"status": "answered", "answer": "x"})

    reply = _client(handler).chat("What now?", "enterprise", "hr", "a" * 32)
    assert reply["status"] == "answered"
    assert seen["url"] == "http://api.test/api/v1/chat"
    assert b'"department":"hr"' in seen["body"].replace(b" ", b"")
    assert b"a" * 32 in seen["body"]


def test_client_leaves_out_empty_options() -> None:
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.read())
        return httpx.Response(200, json={})

    _client(handler).chat("q", "personal", None, None)
    assert b"department" not in bodies[0]
    assert b"conversation_id" not in bodies[0]


def test_client_turns_error_responses_into_api_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "llm_not_configured"})

    with pytest.raises(ApiError) as exc:
        _client(handler).chat("q", "enterprise", None, None)
    assert (exc.value.status, exc.value.detail) == (503, "llm_not_configured")


def test_client_copes_with_error_bodies_that_are_not_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    with pytest.raises(ApiError) as exc:
        _client(handler).list_documents()
    assert exc.value.detail == "unknown_error"


def test_client_reports_an_unreachable_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _client(handler)
    with pytest.raises(ApiUnreachable):
        client.list_documents()
    assert client.healthy() is False


def test_upload_with_every_file_rejected_still_returns_the_per_file_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "results": [{"filename": "a.exe", "status": "rejected", "reason": "unsupported_type"}]
        }
        return httpx.Response(422, json=body)

    results = _client(handler).upload([("a.exe", b"MZ")], "enterprise", "hr")
    assert results[0]["reason"] == "unsupported_type"


def test_base_url_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_BASE_URL", "http://example.test:9000/api/v1/")
    assert api_client.base_url() == "http://example.test:9000/api/v1"


# ---------- display helpers ----------


def test_source_title_and_page_labels() -> None:
    source = {
        "doc_title": "Safety Manual",
        "section_title": "6. Emergency",
        "page_start": 2,
        "page_end": 2,
    }
    assert ui.source_title(source) == "Safety Manual · 6. Emergency · page 2"
    assert ui.page_label(2, 4) == "pages 2-4"
    assert ui.page_label(None, None) == ""
    assert ui.source_title({"source_name": "a.txt"}) == "a.txt"


def test_status_badges() -> None:
    assert ui.status_badge({"status": "ready"}) == ("Ready", "green")
    label, color = ui.status_badge({"status": "failed", "status_reason": "corrupt_pdf"})
    assert (label, color) == ("Failed: could not read the PDF", "red")
    assert ui.status_badge({"status": "processing"}) == ("Processing", "orange")


def test_error_messages_are_friendly() -> None:
    assert "OPENAI_API_KEY" in ui.error_message(ApiError(503, "llm_not_configured"))
    assert "(weird)" in ui.error_message(ApiError(500, "weird"))


def test_images_and_link_addresses_are_removed_from_model_output() -> None:
    text = "Step one [1]. ![x](http://evil.test/?q=secret) See [the manual](http://evil.test) [2]."
    cleaned = ui.safe_markdown(text)
    assert "evil.test" not in cleaned
    assert "![" not in cleaned
    assert "the manual" in cleaned  # the label survives
    assert "[1]" in cleaned
    assert "[2]" in cleaned  # citation markers are untouched


def test_reference_style_images_are_removed_too() -> None:
    assert "![" not in ui.safe_markdown("a ![alt][ref] b")


def test_document_text_is_escaped_so_it_is_shown_literally() -> None:
    escaped = ui.escape_markdown("*bold* [click](http://x) <b>hi</b> a_b #tag")
    assert escaped == "\\*bold\\* \\[click\\]\\(http://x\\) \\<b\\>hi\\</b\\> a\\_b \\#tag"


def test_sizes_and_departments() -> None:
    assert ui.human_size(512) == "512 B"
    assert ui.human_size(6800) == "7 KB"
    assert ui.human_size(5 * 1024 * 1024) == "5.0 MB"
    assert ui.department_label("quality") == "Quality Control"
    assert ui.department_label("") == "Personal"


# ---------- the page, with a fake API ----------

ANSWER = {
    "id": "m2",
    "role": "assistant",
    "content": "Wait for the incident controller [1].",
    "status": "answered",
    "reason": None,
    "rewritten_question": "When can I re-enter an evacuated area?",
    "created_at": "2026-10-31T10:00:00Z",
    "sources": [
        {
            "number": 1,
            "chunk_id": "c1",
            "doc_code": "APM-HSE-MAN-003",
            "doc_title": "Factory Health & Safety Manual",
            "source_name": "03_Factory_Safety_Manual.pdf",
            "section_title": "6. Emergency response",
            "page_start": 2,
            "page_end": 2,
            "quote": (
                "Do not re-enter an evacuated area until authorized by the incident controller."
            ),
        }
    ],
}
QUESTION = {"id": "m1", "role": "user", "content": "And when can I go back in?", "created_at": "x"}
DOCUMENT = {
    "id": "d1",
    "scope": "enterprise",
    "department": "safety",
    "original_filename": "03_Factory_Safety_Manual.pdf",
    "file_ext": ".pdf",
    "size_bytes": 6740,
    "status": "ready",
    "status_reason": None,
    "chunk_count": 9,
    "page_count": 3,
    "doc_code": "APM-HSE-MAN-003",
    "title": "Factory Health & Safety Manual",
    "created_at": "x",
}


class FakeApi:
    def __init__(self, *, healthy: bool = True, conversations: bool = True) -> None:
        self._healthy = healthy
        self.conversations = (
            [
                {
                    "id": "c" * 32,
                    "title": "When can I re-enter an area?",
                    "created_at": "x",
                    "updated_at": "x",
                }
            ]
            if conversations
            else []
        )
        self.asked: list[tuple[Any, ...]] = []
        self.deleted_docs: list[str] = []
        self.fail_with: ApiError | None = None

    def healthy(self) -> bool:
        return self._healthy

    def list_conversations(self) -> list[dict[str, Any]]:
        return self.conversations

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        return {"id": conversation_id, "title": "t", "messages": [QUESTION, ANSWER]}

    def chat(
        self, question: str, scope: str, department: str | None, cid: str | None
    ) -> dict[str, Any]:
        self.asked.append((question, scope, department, cid))
        if self.fail_with:
            raise self.fail_with
        return {"conversation_id": "d" * 32, "status": "answered", "answer": "x", "sources": []}

    def list_documents(self) -> list[dict[str, Any]]:
        return [
            DOCUMENT,
            {
                **DOCUMENT,
                "id": "d2",
                "original_filename": "bad.pdf",
                "title": None,
                "status": "failed",
                "status_reason": "corrupt_pdf",
                "chunk_count": 0,
            },
        ]

    def delete_conversation(self, conversation_id: str) -> None:
        return None

    def delete_document(self, document_id: str) -> None:
        self.deleted_docs.append(document_id)

    def reindex(self, document_id: str) -> None:
        return None


def _run(
    monkeypatch: pytest.MonkeyPatch, fake: FakeApi, *, select_conversation: bool = False
) -> AppTest:
    monkeypatch.setattr(api_client, "ApiClient", lambda *a, **k: fake)
    app = AppTest.from_file(APP, default_timeout=30)
    if select_conversation:
        app.session_state["conversation_id"] = "c" * 32
    return app.run()


def _all_text(app: AppTest) -> str:
    """All visible text, with the markdown escaping removed so it reads like the screen does."""
    parts = [m.value for m in app.markdown] + [c.value for c in app.caption]
    parts += [i.value for i in app.info] + [e.value for e in app.error]
    parts += [s.value for s in app.success] + [h.value for h in app.subheader]
    return "\n".join(parts).replace("\\", "")


def test_empty_chat_shows_welcome_and_example_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(monkeypatch, FakeApi(conversations=False))
    assert not app.exception
    assert app.title[0].value == "Ask the documents"
    labels = [b.label for b in app.button]
    assert ui.EXAMPLE_QUESTIONS[0] in labels
    assert "➕ New chat" in labels


def test_saved_conversation_shows_answer_and_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(monkeypatch, FakeApi(), select_conversation=True)
    assert not app.exception
    text = _all_text(app)
    assert "Wait for the incident controller [1]." in text
    assert "Searched as:" in text
    assert [e.label for e in app.expander] == ["Sources (1)"]
    assert "Do not re-enter an evacuated area" in text
    assert "APM-HSE-MAN-003" in text


def test_asking_a_question_calls_the_api_with_the_sidebar_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeApi(conversations=False)
    app = _run(monkeypatch, fake)
    app.selectbox[0].select("HR")
    app.chat_input[0].set_value("How do I submit a leave request?")
    app.run()
    assert fake.asked == [("How do I submit a leave request?", "enterprise", "hr", None)]
    assert app.session_state["conversation_id"] == "d" * 32


def test_api_errors_are_shown_in_plain_words(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(conversations=False)
    fake.fail_with = ApiError(503, "llm_not_configured")
    app = _run(monkeypatch, fake)
    app.chat_input[0].set_value("hello?")
    app.run()
    assert any("OPENAI_API_KEY" in e.value for e in app.error)
    assert "conversation_id" not in app.session_state


def test_unreachable_api_shows_how_to_start_it(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(monkeypatch, FakeApi(healthy=False))
    assert not app.exception
    assert any("not running" in e.value for e in app.error)
    assert not app.chat_input


def test_documents_page_lists_status_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(monkeypatch, FakeApi())
    app.sidebar.radio[0].set_value("📄 Documents").run()
    assert not app.exception
    assert app.title[0].value == "Documents"
    text = _all_text(app)
    assert "Library (2)" in text
    assert "Factory Health & Safety Manual" in text
    assert "9 passages" in text
    assert "APM-HSE-MAN-003" in text
    assert ":red-badge[Failed: could not read the PDF]" in text
    assert ":green-badge[Ready]" in text


def test_a_link_with_a_conversation_id_opens_that_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_client, "ApiClient", lambda *a, **k: FakeApi())
    app = AppTest.from_file(APP, default_timeout=30)
    app.query_params["c"] = "c" * 32
    app.run()
    assert app.session_state["conversation_id"] == "c" * 32
    assert "Wait for the incident controller [1]." in _all_text(app)


def test_a_malformed_conversation_link_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_client, "ApiClient", lambda *a, **k: FakeApi(conversations=False))
    app = AppTest.from_file(APP, default_timeout=30)
    app.query_params["c"] = "../../etc/passwd"
    app.run()
    assert "conversation_id" not in app.session_state
    assert not app.exception


def test_a_link_can_open_the_documents_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_client, "ApiClient", lambda *a, **k: FakeApi())
    app = AppTest.from_file(APP, default_timeout=30)
    app.query_params["page"] = "documents"
    app.run()
    assert app.title[0].value == "Documents"
