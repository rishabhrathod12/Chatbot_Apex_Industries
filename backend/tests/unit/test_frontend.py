"""Tests for the Streamlit frontend: the API client (login, token renewal), the display helpers,
and the pages themselves, driven with Streamlit's own test runner and a fake API (no server or
browser needed)."""

from pathlib import Path
from typing import Any

import httpx
import pytest
from streamlit.testing.v1 import AppTest

import api_client
import ui
from api_client import ApiClient, ApiError, ApiUnreachable, SessionExpired

APP = str(Path(__file__).resolve().parents[3] / "frontend" / "streamlit_app.py")

ADMIN = {
    "user_id": "u-admin",
    "email": "admin@apex.test",
    "full_name": "Asha Admin",
    "role": "super_admin",
    "departments": [],
}
MANAGER = {
    "user_id": "u-mgr",
    "email": "safety.manager@apex.test",
    "full_name": "Sana Safety",
    "role": "department_manager",
    "departments": ["safety"],
}
EMPLOYEE = {
    "user_id": "u-emp",
    "email": "production.employee@apex.test",
    "full_name": "Esha Production",
    "role": "employee",
    "departments": ["production"],
}


def _pair(
    user: dict[str, Any], access: str = "access-1", refresh: str = "refresh-1"
) -> dict[str, Any]:
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": 1800,
        "user": user,
    }


# ---------- API client: login and tokens ----------


def _client(handler: Any, *, logged_in: bool = True) -> ApiClient:
    client = ApiClient(
        "http://api.test/api/v1", httpx.Client(transport=httpx.MockTransport(handler))
    )
    if logged_in:
        client._access, client._refresh, client.user = "access-1", "refresh-1", ADMIN
    return client


def test_login_stores_the_tokens_and_the_user() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), request.read()
        return httpx.Response(200, json=_pair(EMPLOYEE))

    client = _client(handler, logged_in=False)
    assert not client.is_logged_in
    user = client.login("a@b.co", "secret-password")
    assert user == EMPLOYEE
    assert client.is_logged_in
    assert seen["url"] == "http://api.test/api/v1/auth/login"
    assert b"secret-password" in seen["body"]


def test_a_wrong_password_raises_and_does_not_log_in() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid_credentials"})

    client = _client(handler, logged_in=False)
    with pytest.raises(ApiError) as exc:
        client.login("a@b.co", "nope")
    assert exc.value.detail == "invalid_credentials"
    assert not client.is_logged_in


def test_the_lockout_wait_time_is_available_to_show() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"detail": "too_many_attempts"}, headers={"Retry-After": "600"}
        )

    with pytest.raises(ApiError) as exc:
        _client(handler, logged_in=False).login("a@b.co", "x")
    assert exc.value.retry_after == 600
    assert "10 minute" in ui.retry_message(exc.value)


def test_every_request_carries_the_access_token() -> None:
    headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json=[])

    _client(handler).list_documents()
    assert headers == ["Bearer access-1"]


def test_without_a_login_requests_are_not_even_sent() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200, json=[])

    with pytest.raises(SessionExpired):
        _client(handler, logged_in=False).list_documents()
    assert sent == []


def test_an_expired_token_is_renewed_once_and_the_request_retried() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.headers.get("authorization", "")))
        if request.url.path.endswith("/auth/refresh"):
            return httpx.Response(200, json=_pair(ADMIN, "access-2", "refresh-2"))
        if request.headers.get("authorization") == "Bearer access-1":
            return httpx.Response(401, json={"detail": "token_expired"})
        return httpx.Response(200, json=[{"id": "d1"}])

    client = _client(handler)
    assert client.list_documents() == [{"id": "d1"}]
    assert [path.rsplit("/", 1)[-1] for path, _ in calls] == ["documents", "refresh", "documents"]
    assert calls[-1][1] == "Bearer access-2"
    assert client.list_documents() == [{"id": "d1"}]  # the new token keeps being used


def test_when_renewal_fails_the_session_ends_and_tokens_are_forgotten() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/refresh"):
            return httpx.Response(401, json={"detail": "invalid_refresh_token"})
        return httpx.Response(401, json={"detail": "token_expired"})

    client = _client(handler)
    with pytest.raises(SessionExpired):
        client.list_documents()
    assert not client.is_logged_in
    assert client.user is None


def test_other_errors_are_not_mistaken_for_an_expired_session() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "not_allowed"})

    client = _client(handler)
    with pytest.raises(ApiError) as exc:
        client.list_users()
    assert exc.value.status == 403
    assert client.is_logged_in


def test_logout_tells_the_server_and_forgets_everything() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(204)

    client = _client(handler)
    client.logout()
    assert seen == ["/api/v1/auth/logout"]
    assert not client.is_logged_in


def test_logout_still_works_when_the_api_is_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _client(handler)
    client.logout()
    assert not client.is_logged_in


def test_chat_and_upload_send_what_the_api_expects() -> None:
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.read())
        return httpx.Response(200, json={"results": []})

    client = _client(handler)
    client.chat("What now?", "enterprise", "hr", "a" * 32)
    assert b'"department":"hr"' in bodies[0].replace(b" ", b"")
    client.upload([("a.txt", b"x")], "enterprise", "safety", company_wide=True)
    assert b'name="visibility"' in bodies[1] and b"company" in bodies[1]
    client.upload([("b.txt", b"x")], "personal", None)
    assert b"visibility" not in bodies[2]


def test_unreachable_api_is_reported_and_health_is_false() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _client(handler)
    with pytest.raises(ApiUnreachable):
        client.list_documents()
    assert client.healthy() is False


def test_error_bodies_that_are_not_json_are_handled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    with pytest.raises(ApiError) as exc:
        _client(handler).list_documents()
    assert exc.value.detail == "unknown_error"


def test_upload_with_every_file_rejected_still_returns_per_file_results() -> None:
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
    assert "permission" in ui.error_message(ApiError(403, "not_allowed"))
    assert "(weird)" in ui.error_message(ApiError(500, "weird"))


def test_images_and_link_addresses_are_removed_from_model_output() -> None:
    text = "Step one [1]. ![x](http://evil.test/?q=secret) See [the manual](http://evil.test) [2]."
    cleaned = ui.safe_markdown(text)
    assert "evil.test" not in cleaned and "![" not in cleaned
    assert "the manual" in cleaned and "[1]" in cleaned and "[2]" in cleaned


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


def test_where_each_role_may_add_documents() -> None:
    assert ui.upload_targets(EMPLOYEE) == [""]
    assert ui.upload_targets(MANAGER) == ["", "safety"]
    admin_targets = ui.upload_targets(ADMIN)
    assert admin_targets[0] == "" and set(admin_targets[1:]) == set(ui.DEPARTMENTS)


def test_which_buttons_are_shown_is_only_a_convenience() -> None:
    company = {"scope": "enterprise", "department": "safety"}
    other = {"scope": "enterprise", "department": "hr"}
    assert ui.can_manage(ADMIN, company) and ui.can_manage(ADMIN, other)
    assert ui.can_manage(MANAGER, company) and not ui.can_manage(MANAGER, other)
    assert not ui.can_manage(EMPLOYEE, {"scope": "enterprise", "department": "production"})
    assert ui.can_manage(EMPLOYEE, {"scope": "personal", "department": ""})


def test_visibility_labels() -> None:
    assert ui.visibility_label({"scope": "personal"}) == "Personal"
    assert ui.visibility_label({"scope": "enterprise", "visibility": "company"}) == "Company-wide"
    assert (
        ui.visibility_label({"scope": "enterprise", "visibility": "department"})
        == "Department only"
    )


def test_the_sample_set_marks_safety_and_hr_company_wide() -> None:
    wide = {name for name, (_, is_wide) in ui.SAMPLE_DOCUMENTS.items() if is_wide}
    assert wide == {"03_Factory_Safety_Manual.pdf", "05_HR_Policy.pdf"}


# ---------- the pages, with a fake API ----------

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
    "visibility": "company",
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
PEOPLE = [
    {**ADMIN, "id": "u-admin", "is_active": True, "created_at": "x"},
    {**EMPLOYEE, "id": "u-emp", "is_active": True, "created_at": "x"},
    {**MANAGER, "id": "u-off", "is_active": False, "created_at": "x"},
]


class FakeApi:
    def __init__(
        self,
        *,
        user: dict[str, Any] | None = ADMIN,
        healthy: bool = True,
        conversations: bool = True,
    ) -> None:
        self._healthy = healthy
        self.user = user
        self.is_logged_in = user is not None
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
        self.login_attempts: list[tuple[str, str]] = []
        self.login_error: ApiError | None = None
        self.chat_error: ApiError | None = None
        self.logged_out = False
        self.created: list[tuple[Any, ...]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []

    def healthy(self) -> bool:
        return self._healthy

    def login(self, email: str, password: str) -> dict[str, Any]:
        self.login_attempts.append((email, password))
        if self.login_error:
            raise self.login_error
        self.user, self.is_logged_in = EMPLOYEE, True
        return EMPLOYEE

    def logout(self) -> None:
        self.logged_out = True
        self.user, self.is_logged_in = None, False

    def list_conversations(self) -> list[dict[str, Any]]:
        return self.conversations

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        return {"id": conversation_id, "title": "t", "messages": [QUESTION, ANSWER]}

    def chat(
        self, question: str, scope: str, department: str | None, cid: str | None
    ) -> dict[str, Any]:
        self.asked.append((question, scope, department, cid))
        if self.chat_error:
            raise self.chat_error
        return {"conversation_id": "d" * 32, "status": "answered", "answer": "x", "sources": []}

    def list_documents(self) -> list[dict[str, Any]]:
        failed = {
            **DOCUMENT,
            "id": "d2",
            "original_filename": "bad.pdf",
            "title": None,
            "status": "failed",
            "status_reason": "corrupt_pdf",
            "chunk_count": 0,
            "department": "hr",
            "visibility": "department",
        }
        return [DOCUMENT, failed]

    def delete_conversation(self, conversation_id: str) -> None:
        return None

    def list_users(self) -> list[dict[str, Any]]:
        return PEOPLE

    def create_user(self, *args: Any) -> dict[str, Any]:
        self.created.append(args)
        return {"email": args[0]}

    def update_user(self, user_id: str, **changes: Any) -> dict[str, Any]:
        self.updated.append((user_id, changes))
        return {}

    def reset_password(self, user_id: str, password: str) -> None:
        return None


def _app(monkeypatch: pytest.MonkeyPatch, fake: FakeApi) -> AppTest:
    monkeypatch.setattr(api_client, "ApiClient", lambda *a, **k: fake)
    return AppTest.from_file(APP, default_timeout=30)


def _run(
    monkeypatch: pytest.MonkeyPatch, fake: FakeApi, *, select_conversation: bool = False
) -> AppTest:
    app = _app(monkeypatch, fake)
    if select_conversation:
        app.session_state["conversation_id"] = "c" * 32
    return app.run()


def _all_text(app: AppTest) -> str:
    """All visible text, with the markdown escaping removed so it reads like the screen does."""
    parts = [m.value for m in app.markdown] + [c.value for c in app.caption]
    parts += [i.value for i in app.info] + [e.value for e in app.error]
    parts += [s.value for s in app.success] + [h.value for h in app.subheader]
    return "\n".join(parts).replace("\\", "")


# ---- signing in ----


def test_signed_out_visitors_see_only_the_sign_in_form(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(monkeypatch, FakeApi(user=None))
    assert not app.exception
    assert [t.label for t in app.text_input] == ["Email", "Password"]
    assert not app.chat_input
    assert not app.sidebar.radio  # no navigation before signing in
    assert "no self sign-up" in _all_text(app)


def test_signing_in_with_the_form_logs_in_and_shows_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeApi(user=None, conversations=False)
    app = _run(monkeypatch, fake)
    app.text_input[0].set_value("esha@apex.test")
    app.text_input[1].set_value("a-long-password-1")
    app.button[0].click().run()
    assert fake.login_attempts == [("esha@apex.test", "a-long-password-1")]
    assert app.title[0].value == "Ask the documents"


def test_a_failed_sign_in_shows_a_plain_message_and_stays_on_the_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeApi(user=None)
    fake.login_error = ApiError(401, "invalid_credentials")
    app = _run(monkeypatch, fake)
    app.text_input[0].set_value("x@apex.test")
    app.text_input[1].set_value("wrong")
    app.button[0].click().run()
    assert any("do not match" in e.value for e in app.error)
    assert not app.chat_input


def test_a_lockout_tells_the_user_how_long_to_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(user=None)
    fake.login_error = ApiError(429, "too_many_attempts", retry_after=840)
    app = _run(monkeypatch, fake)
    app.text_input[0].set_value("x@apex.test")
    app.text_input[1].set_value("wrong")
    app.button[0].click().run()
    assert any("14 minute" in e.value for e in app.error)


def test_signing_out_returns_to_the_sign_in_form(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(user=EMPLOYEE)
    app = _run(monkeypatch, fake)
    next(b for b in app.sidebar.button if b.label == "Sign out").click().run()
    assert fake.logged_out
    assert [t.label for t in app.text_input] == ["Email", "Password"]


def test_the_sidebar_shows_who_is_signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(monkeypatch, FakeApi(user=MANAGER))
    text = "\n".join(m.value for m in app.sidebar.markdown) + "\n".join(
        c.value for c in app.sidebar.caption
    )
    assert "Sana Safety" in text
    assert "Department Manager" in text and "Safety" in text


# ---- who sees which pages ----


@pytest.mark.parametrize(
    ("user", "pages"),
    [(ADMIN, 3), (MANAGER, 2), (EMPLOYEE, 2)],
)
def test_only_super_admins_get_the_users_page(
    monkeypatch: pytest.MonkeyPatch, user: dict[str, Any], pages: int
) -> None:
    app = _run(monkeypatch, FakeApi(user=user))
    options = app.sidebar.radio[0].options
    assert len(options) == pages
    assert ("👥 Users" in options) == (user["role"] == "super_admin")


def test_the_users_page_lists_everyone_and_protects_your_own_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeApi(user=ADMIN)
    app = _app(monkeypatch, fake).run()
    app.sidebar.radio[0].set_value("👥 Users").run()
    assert not app.exception
    text = _all_text(app)
    assert "Asha Admin" in text and "Esha Production" in text
    labels = [b.label for b in app.button]
    assert "Disable" in labels  # for the employee
    assert "Enable" in labels  # for the disabled manager
    assert labels.count("Disable") == 1  # none for your own row


def test_disabling_a_user_calls_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(user=ADMIN)
    app = _app(monkeypatch, fake).run()
    app.sidebar.radio[0].set_value("👥 Users").run()
    next(b for b in app.button if b.key == "toggle-u-emp").click().run()
    assert fake.updated == [("u-emp", {"is_active": False})]


# ---- chat ----


def test_empty_chat_shows_welcome_and_example_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(monkeypatch, FakeApi(conversations=False))
    assert not app.exception
    assert app.title[0].value == "Ask the documents"
    labels = [b.label for b in app.button]
    assert ui.EXAMPLE_QUESTIONS[0] in labels and "➕ New chat" in labels


def test_saved_conversation_shows_answer_and_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(monkeypatch, FakeApi(), select_conversation=True)
    assert not app.exception
    text = _all_text(app)
    assert "Wait for the incident controller [1]." in text
    assert "Searched as:" in text
    assert [e.label for e in app.expander] == ["Sources (1)"]
    assert "Do not re-enter an evacuated area" in text and "APM-HSE-MAN-003" in text


def test_asking_a_question_calls_the_api_with_the_sidebar_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeApi(conversations=False)
    app = _run(monkeypatch, fake)
    app.sidebar.selectbox[0].select("HR")
    app.chat_input[0].set_value("How do I submit a leave request?")
    app.run()
    assert fake.asked == [("How do I submit a leave request?", "enterprise", "hr", None)]
    assert app.session_state["conversation_id"] == "d" * 32


def test_api_errors_are_shown_in_plain_words(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(conversations=False)
    fake.chat_error = ApiError(503, "llm_not_configured")
    app = _run(monkeypatch, fake)
    app.chat_input[0].set_value("hello?")
    app.run()
    assert any("OPENAI_API_KEY" in e.value for e in app.error)
    assert "conversation_id" not in app.session_state


def test_unreachable_api_shows_how_to_start_it(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(monkeypatch, FakeApi(healthy=False))
    assert not app.exception
    assert any("not running" in e.value for e in app.error)
    assert not app.chat_input and not app.text_input


# ---- documents ----


def _documents(monkeypatch: pytest.MonkeyPatch, user: dict[str, Any]) -> AppTest:
    app = _app(monkeypatch, FakeApi(user=user)).run()
    return app.sidebar.radio[0].set_value("📄 Documents").run()


def test_documents_page_lists_status_visibility_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _documents(monkeypatch, ADMIN)
    assert not app.exception
    assert app.title[0].value == "Documents"
    text = _all_text(app)
    assert "Library (2)" in text
    assert "Factory Health & Safety Manual" in text
    assert "Company-wide" in text and "Department only" in text
    assert "9 passages" in text
    assert ":red-badge[Failed: could not read the PDF]" in text
    assert ":green-badge[Ready]" in text


def test_admins_can_load_the_samples_and_see_manage_buttons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _documents(monkeypatch, ADMIN)
    labels = [b.label for b in app.button]
    assert "Load the 6 sample documents" in labels
    assert labels.count("Re-index") == 2


def test_a_manager_only_gets_manage_buttons_for_their_own_department(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _documents(monkeypatch, MANAGER)
    labels = [b.label for b in app.button]
    assert "Load the 6 sample documents" not in labels
    assert labels.count("Re-index") == 1  # the safety document, not the HR one


def test_an_employee_can_only_add_private_documents_and_manage_nothing_here(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _documents(monkeypatch, EMPLOYEE)
    text = _all_text(app)
    assert "your own private library" in text
    assert not app.selectbox or all(s.label != "Add to" for s in app.selectbox)
    assert "Re-index" not in [b.label for b in app.button]


# ---- links ----


def test_a_link_with_a_conversation_id_opens_that_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(monkeypatch, FakeApi())
    app.query_params["c"] = "c" * 32
    app.run()
    assert app.session_state["conversation_id"] == "c" * 32
    assert "Wait for the incident controller [1]." in _all_text(app)


def test_a_malformed_conversation_link_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(monkeypatch, FakeApi(conversations=False))
    app.query_params["c"] = "../../etc/passwd"
    app.run()
    assert "conversation_id" not in app.session_state
    assert not app.exception


def test_a_link_can_open_the_documents_page(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(monkeypatch, FakeApi())
    app.query_params["page"] = "documents"
    app.run()
    assert app.title[0].value == "Documents"


def test_a_link_to_the_users_page_is_ignored_for_non_admins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(monkeypatch, FakeApi(user=EMPLOYEE))
    app.query_params["page"] = "users"
    app.run()
    assert app.title[0].value == "Ask the documents"
