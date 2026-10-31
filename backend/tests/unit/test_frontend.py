"""Tests for the Streamlit frontend: the API client (login, token renewal), the display helpers,
and the pages themselves, driven with Streamlit's own test runner and a fake API (no server or
browser needed)."""

import json
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


def test_login_sends_the_chosen_portal_only_when_there_is_one() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.read()))
        return httpx.Response(200, json=_pair(EMPLOYEE))

    client = _client(handler, logged_in=False)
    client.login("esha", "secret-password")
    client.login("esha", "secret-password", portal="employee")
    assert "portal" not in bodies[0]
    assert bodies[1]["portal"] == "employee"


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
    assert exc.value.detail == "server_error"


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
    assert "(weird, HTTP 500)" in ui.error_message(ApiError(500, "weird"))


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


def test_audit_entries_read_as_plain_sentences() -> None:
    entry = {
        "actor_name": "Asha Admin",
        "action": "user_role_change",
        "target_label": "Esha Production",
        "detail": "role:super_admin",
    }
    assert ui.audit_summary(entry) == (
        "Asha Admin: Role changed (Esha Production) [role:super_admin]"
    )
    nobody = {"actor_name": None, "action": "login", "target_label": None, "detail": None}
    assert ui.audit_summary(nobody) == "Someone not signed in: Sign-in"
    assert ui.action_label("some_new_action") == "Some new action"  # unknown ones still read ok


def test_times_are_shortened_for_display() -> None:
    assert ui.short_time("2026-10-31T09:05:12.123456+00:00") == "2026-10-31 09:05"
    assert ui.short_time(None) == ""


def test_archived_documents_get_a_grey_badge() -> None:
    assert ui.status_badge({"status": "archived"}) == ("Archived", "gray")


def test_only_people_who_can_add_company_documents_get_a_department_list() -> None:
    assert ui.company_upload_targets(EMPLOYEE) == []
    assert ui.company_upload_targets(MANAGER) == ["safety"]
    assert ui.company_upload_targets(ADMIN) == list(ui.DEPARTMENTS)


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
FAILED_DOCUMENT = {
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
PERSONAL_DOCUMENT = {
    **DOCUMENT,
    "id": "d3",
    "scope": "personal",
    "department": "",
    "visibility": "personal",
    "original_filename": "my-notes.pdf",
    "title": "My shift notes",
}
ARCHIVED_DOCUMENT = {
    **DOCUMENT,
    "id": "d4",
    "status": "archived",
    "title": "Old Safety Rules",
    "original_filename": "old.pdf",
    "chunk_count": 0,
    "archived_at": "2026-10-30T10:00:00Z",
}
STATS = {
    "accounts": {
        "total": 6,
        "active": 5,
        "inactive": 1,
        "by_role": {"super_admin": 1, "department_manager": 2, "employee": 3},
    },
    "departments": [
        {"code": code, "employees": 2 if code == "production" else 0, "managers": 0, "documents": 1}
        for code in ("production", "maintenance", "hr", "safety", "quality", "stores")
    ],
    "documents": {"total": 6, "archived": 1, "by_status": {"ready": 6}},
    "recent_uploads": [
        {
            "document_id": "d1",
            "title": "Factory Health & Safety Manual",
            "filename": "03.pdf",
            "department": "safety",
            "status": "ready",
            "uploaded_by": "Asha Admin",
            "created_at": "2026-10-31T09:05:12+00:00",
        }
    ],
    "recent_activity": [
        {
            "id": 7,
            "created_at": "2026-10-31T09:06:00+00:00",
            "action": "user_deactivate",
            "outcome": "ok",
            "actor_id": "u-admin",
            "actor_name": "Asha Admin",
            "actor_login_id": "admin",
            "target_type": "user",
            "target_id": "u-emp",
            "target_label": "Esha Production",
            "detail": None,
        }
    ],
}
AUDIT_ENTRY = {**STATS["recent_activity"][0]}  # type: ignore[dict-item]
PEOPLE = [
    {**ADMIN, "id": "u-admin", "login_id": "admin", "is_active": True, "created_at": "x"},
    {**EMPLOYEE, "id": "u-emp", "login_id": "esha", "is_active": True, "created_at": "x"},
    {**MANAGER, "id": "u-off", "login_id": "sana", "is_active": False, "created_at": "x"},
]


class FakeApi:
    def __init__(
        self,
        *,
        user: dict[str, Any] | None = ADMIN,
        healthy: bool = True,
        conversations: bool = True,
        level: int | None = api_client.REQUIRED_API_LEVEL,
    ) -> None:
        self._healthy = healthy
        self._level = level
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
        self.login_portals: list[str | None] = []
        self.login_error: ApiError | None = None
        self.chat_error: ApiError | None = None
        self.logged_out = False
        self.setup_state: dict[str, bool] = {
            "setup_required": False,
            "token_required": False,
            "available": True,
        }
        self.setup_error: ApiError | None = None
        self.setup_calls: list[tuple[str, str, str, str]] = []
        self.change_error: ApiError | None = None
        self.change_calls: list[tuple[str, str]] = []
        self.temporary_password: str | None = None
        self.created: list[tuple[Any, ...]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self.documents: list[dict[str, Any]] = [DOCUMENT, FAILED_DOCUMENT, PERSONAL_DOCUMENT]
        self.uploaded: list[tuple[Any, ...]] = []
        self.document_calls: list[tuple[str, str]] = []
        self.audit_requests: list[dict[str, Any]] = []

    def healthy(self) -> bool:
        return self._healthy

    def api_level(self) -> int | None:
        return self._level

    def login(self, email: str, password: str, portal: str | None = None) -> dict[str, Any]:
        self.login_attempts.append((email, password))
        self.login_portals.append(portal)
        if self.login_error:
            raise self.login_error
        self.user, self.is_logged_in = EMPLOYEE, True
        return EMPLOYEE

    def setup_status(self) -> dict[str, Any]:
        return self.setup_state

    def setup(self, email: str, full_name: str, password: str, token: str = "") -> None:
        self.setup_calls.append((email, full_name, password, token))
        if self.setup_error:
            raise self.setup_error

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
        return self.documents

    def upload(
        self,
        files: list[tuple[str, bytes]],
        scope: str,
        department: str | None,
        company_wide: bool = False,
        details: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        self.uploaded.append((scope, department, company_wide, details))
        return [{"filename": name, "status": "accepted"} for name, _ in files]

    def archive_document(self, document_id: str) -> None:
        self.document_calls.append(("archive", document_id))

    def restore_document(self, document_id: str) -> None:
        self.document_calls.append(("restore", document_id))

    def reindex(self, document_id: str) -> None:
        self.document_calls.append(("reindex", document_id))

    def delete_document(self, document_id: str) -> None:
        self.document_calls.append(("delete", document_id))

    def list_versions(self, document_id: str) -> list[dict[str, Any]]:
        return [DOCUMENT]

    def admin_stats(self) -> dict[str, Any]:
        return STATS

    def admin_audit(self, page: int = 1, page_size: int = 25, **filters: str) -> dict[str, Any]:
        self.audit_requests.append({"page": page, **filters})
        return {
            "items": [AUDIT_ENTRY] * min(page_size, 3),
            "total": 60,
            "page": page,
            "page_size": page_size,
            "available_actions": ["login", "user_deactivate"],
        }

    def delete_conversation(self, conversation_id: str) -> None:
        return None

    def list_users(self) -> list[dict[str, Any]]:
        return PEOPLE

    def create_user(self, *args: Any) -> dict[str, Any]:
        self.created.append(args)
        return {
            "email": args[0],
            "login_id": "new.person",
            "temporary_password": self.temporary_password,
        }

    def change_password(self, current: str, new: str) -> dict[str, Any]:
        self.change_calls.append((current, new))
        if self.change_error:
            raise self.change_error
        assert self.user is not None
        self.user = {**self.user, "must_change_password": False}
        return self.user

    def update_user(self, user_id: str, **changes: Any) -> dict[str, Any]:
        self.updated.append((user_id, changes))
        return {}

    def reset_password(self, user_id: str, password: str | None) -> str | None:
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


def _page(monkeypatch: pytest.MonkeyPatch, fake: FakeApi, page: str) -> AppTest:
    """Open the app straight on one page (the sidebar remembers the choice in `nav`)."""
    app = _app(monkeypatch, fake)
    app.session_state["nav"] = page
    return app.run()


def _all_text(app: AppTest) -> str:
    """All visible text, with the markdown escaping removed so it reads like the screen does."""
    parts = [m.value for m in app.markdown] + [c.value for c in app.caption]
    parts += [i.value for i in app.info] + [e.value for e in app.error]
    parts += [s.value for s in app.success] + [h.value for h in app.subheader]
    return "\n".join(parts).replace("\\", "")


# ---- signing in ----


def _at_portal(monkeypatch: pytest.MonkeyPatch, fake: FakeApi, portal: str = "employee") -> AppTest:
    app = _app(monkeypatch, fake)
    app.session_state["portal"] = portal
    return app.run()


def test_signed_out_visitors_first_see_the_two_sign_in_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _run(monkeypatch, FakeApi(user=None))
    assert not app.exception
    assert not app.text_input  # nothing to type until a portal is chosen
    assert [b.label for b in app.button] == ["Continue as employee", "Continue as Super Admin"]
    assert not app.chat_input and not app.sidebar.radio  # no navigation before signing in
    text = _all_text(app)
    assert "Apex Precision Manufacturing" in text and "no self sign-up" in text


@pytest.mark.parametrize(
    ("button", "portal", "heading"),
    [(0, "employee", "Employee Login"), (1, "admin", "Super Admin Login")],
)
def test_each_card_opens_its_own_sign_in_screen(
    monkeypatch: pytest.MonkeyPatch, button: int, portal: str, heading: str
) -> None:
    app = _run(monkeypatch, FakeApi(user=None))
    app.button[button].click().run()
    assert app.session_state["portal"] == portal
    assert [t.label for t in app.text_input] == ["Login ID", "Password"]
    assert any(heading in m.value for m in app.markdown)
    assert any("Back to the sign-in options" in b.label for b in app.button)


def test_a_bookmarked_portal_link_opens_that_sign_in_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(monkeypatch, FakeApi(user=None))
    app.query_params["portal"] = "admin"
    app.run()
    assert any("Super Admin Login" in m.value for m in app.markdown)
    app = _app(monkeypatch, FakeApi(user=None))
    app.query_params["portal"] = "root"  # not a portal: ignored, landing page instead
    app.run()
    assert [b.label for b in app.button] == ["Continue as employee", "Continue as Super Admin"]


def test_back_returns_to_the_two_options(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _at_portal(monkeypatch, FakeApi(user=None), "admin")
    next(b for b in app.button if b.label.startswith("\u2190")).click().run()
    assert "portal" not in app.session_state
    assert [b.label for b in app.button] == ["Continue as employee", "Continue as Super Admin"]


@pytest.mark.parametrize("portal", ["employee", "admin"])
def test_signing_in_sends_the_chosen_portal_and_shows_the_app(
    monkeypatch: pytest.MonkeyPatch, portal: str
) -> None:
    fake = FakeApi(user=None, conversations=False)
    app = _at_portal(monkeypatch, fake, portal)
    app.text_input[0].set_value("esha")
    app.text_input[1].set_value("a-long-password-1")
    app.button[0].click().run()
    assert fake.login_attempts == [("esha", "a-long-password-1")]
    assert fake.login_portals == [portal]
    assert app.title[0].value == "Welcome, Esha"


def test_a_failed_sign_in_shows_a_plain_message_that_names_the_portal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeApi(user=None)
    fake.login_error = ApiError(401, "invalid_credentials")
    app = _at_portal(monkeypatch, fake, "admin")
    app.text_input[0].set_value("x")
    app.text_input[1].set_value("wrong")
    app.button[0].click().run()
    assert any("active Super Admin account" in e.value for e in app.error)
    assert not app.chat_input
    assert [t.label for t in app.text_input] == ["Login ID", "Password"]  # still on the form


def test_a_lockout_tells_the_user_how_long_to_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(user=None)
    fake.login_error = ApiError(429, "too_many_attempts", retry_after=840)
    app = _at_portal(monkeypatch, fake)
    app.text_input[0].set_value("x")
    app.text_input[1].set_value("wrong")
    app.button[0].click().run()
    assert any("14 minute" in e.value for e in app.error)


def test_an_ended_session_explains_itself_on_the_same_portals_sign_in_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _at_portal(monkeypatch, FakeApi(user=None), "admin")
    app.session_state["notice"] = "Your session ended. Please sign in again."
    app.run()
    assert any("session ended" in i.value for i in app.info)
    assert any("Super Admin Login" in m.value for m in app.markdown)


def test_signing_out_returns_to_the_two_options(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(user=EMPLOYEE)
    app = _app(monkeypatch, fake)
    app.session_state["portal"] = "employee"
    app.run()
    next(b for b in app.sidebar.button if b.label == "Sign out").click().run()
    assert fake.logged_out
    assert "portal" not in app.session_state
    assert [b.label for b in app.button] == ["Continue as employee", "Continue as Super Admin"]


def test_the_sidebar_shows_who_is_signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(monkeypatch, FakeApi(user=MANAGER))
    text = "\n".join(m.value for m in app.sidebar.markdown) + "\n".join(
        c.value for c in app.sidebar.caption
    )
    assert "Sana Safety" in text
    assert "Department Manager" in text and "Safety" in text


# ---- who sees which pages ----


@pytest.mark.parametrize(
    ("user", "first_title"),
    [(ADMIN, "Overview"), (MANAGER, "Welcome, Sana"), (EMPLOYEE, "Welcome, Esha")],
)
def test_each_role_gets_its_own_workspace(
    monkeypatch: pytest.MonkeyPatch, user: dict[str, Any], first_title: str
) -> None:
    app = _run(monkeypatch, FakeApi(user=user))
    assert not app.exception
    assert app.sidebar.radio[0].options == ui.pages_for(user)
    assert app.title[0].value == first_title


def test_the_pages_for_each_role() -> None:
    assert ui.pages_for(ADMIN) == ui.ADMIN_PAGES
    assert ui.pages_for(EMPLOYEE) == ui.EMPLOYEE_PAGES
    manager_pages = ui.pages_for(MANAGER)
    assert ui.DEPARTMENT_DOCUMENTS in manager_pages
    assert [p for p in manager_pages if p != ui.DEPARTMENT_DOCUMENTS] == ui.EMPLOYEE_PAGES
    assert not set(ui.ADMIN_PAGES) & set(ui.EMPLOYEE_PAGES) - {ui.UPLOAD_PDF}


def test_employees_never_see_admin_pages_even_if_the_page_is_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for forbidden in (ui.OVERVIEW, ui.EMPLOYEES, ui.AUDIT_LOGS, ui.COMPANY_DOCUMENTS):
        fake = FakeApi(user=EMPLOYEE)
        app = _page(monkeypatch, fake, forbidden)
        assert app.sidebar.radio[0].value == ui.DASHBOARD  # falls back to their own home
        assert app.title[0].value == "Welcome, Esha"


# ---- employee workspace ----


def test_the_dashboard_summarises_my_documents_and_conversations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _run(monkeypatch, FakeApi(user=EMPLOYEE))
    metrics = {m.label: m.value for m in app.metric}
    assert metrics == {
        "My documents": "1",
        "Ready to ask about": "1",
        "Company documents for me": "1",  # only the ready one
        "Conversations": "1",
    }
    assert any(b.label == "Upload a PDF" for b in app.button)
    assert "When can I re-enter an area?" in _all_text(app)


def test_the_dashboard_warns_about_a_document_that_could_not_be_processed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeApi(user=EMPLOYEE)
    fake.documents = [{**PERSONAL_DOCUMENT, "status": "failed", "status_reason": "corrupt_pdf"}]
    app = _run(monkeypatch, fake)
    assert any("Could not process" in w.value for w in app.warning)


def test_a_dashboard_button_goes_to_its_page(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(monkeypatch, FakeApi(user=EMPLOYEE))
    next(b for b in app.button if b.key == "go-upload").click().run()
    assert app.sidebar.radio[0].value == ui.UPLOAD_PDF
    assert app.title[0].value == "Upload PDF"


def test_upload_pdf_page_explains_privacy(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _page(monkeypatch, FakeApi(user=EMPLOYEE), ui.UPLOAD_PDF)
    assert not app.exception
    assert any("Only you can see" in i.value for i in app.info)
    assert next(b for b in app.button if b.key == "personal-upload-go").disabled


def test_my_documents_lists_only_my_own_with_delete_but_no_company_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _page(monkeypatch, FakeApi(user=EMPLOYEE), ui.MY_DOCUMENTS)
    assert not app.exception
    text = _all_text(app)
    assert "My documents (1)" in text and "My shift notes" in text
    assert "Factory Health" not in text  # company documents are not listed here
    labels = [b.label for b in app.button]
    assert labels.count("Re-index") == 1 and "Archive" not in labels


def test_chat_with_my_documents_searches_only_my_library(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(user=EMPLOYEE, conversations=False)
    app = _page(monkeypatch, fake, ui.CHAT_MINE)
    assert app.title[0].value == "Chat with my documents"
    assert not app.selectbox  # no way to pick another scope or department here
    app.chat_input[0].set_value("What did I note about the shift?")
    app.run()
    assert fake.asked == [("What did I note about the shift?", "personal", None, None)]
    assert app.session_state["conversation_id"] == "d" * 32


def test_company_knowledge_asks_with_the_chosen_department(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeApi(user=EMPLOYEE, conversations=False)
    app = _page(monkeypatch, fake, ui.COMPANY_KNOWLEDGE)
    assert app.title[0].value == "Company knowledge"
    assert ui.EXAMPLE_QUESTIONS[0] in [b.label for b in app.button]
    next(s for s in app.selectbox if s.label == "Only in department").select("HR")
    app.chat_input[0].set_value("How do I submit a leave request?")
    app.run()
    assert fake.asked == [("How do I submit a leave request?", "enterprise", "hr", None)]


def test_company_knowledge_can_browse_the_documents_the_person_may_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeApi(user=EMPLOYEE)
    fake.documents = [DOCUMENT, FAILED_DOCUMENT, ARCHIVED_DOCUMENT, PERSONAL_DOCUMENT]
    app = _page(monkeypatch, fake, ui.COMPANY_KNOWLEDGE)
    app.radio(key="knowledge-view").set_value("Browse documents").run()
    text = _all_text(app)
    assert "Documents you can ask about (2)" in text  # not the archived or the personal one
    assert "Factory Health & Safety Manual" in text
    assert "Old Safety Rules" not in text and "My shift notes" not in text
    assert not [b for b in app.button if b.label in {"Archive", "Re-index", "Delete"}]


def test_conversation_history_reads_and_continues_a_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _page(monkeypatch, FakeApi(user=EMPLOYEE), ui.HISTORY)
    assert app.title[0].value == "Conversation history"
    assert "When can I re-enter an area?" in _all_text(app)
    assert "Wait for the incident controller" not in _all_text(app)  # closed until read
    next(b for b in app.button if b.label == "Read").click().run()
    assert "Wait for the incident controller [1]." in _all_text(app)
    next(b for b in app.button if b.label == "Continue in Company Knowledge").click().run()
    assert app.sidebar.radio[0].value == ui.COMPANY_KNOWLEDGE
    assert app.session_state["conversation_id"] == "c" * 32
    assert "Wait for the incident controller [1]." in _all_text(app)


def test_a_conversation_does_not_follow_you_to_an_unrelated_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _page(monkeypatch, FakeApi(user=EMPLOYEE), ui.CHAT_MINE)
    app.session_state["conversation_id"] = "c" * 32
    app.run()
    assert "conversation_id" in app.session_state
    app.sidebar.radio[0].set_value(ui.COMPANY_KNOWLEDGE).run()
    assert "conversation_id" not in app.session_state


def test_a_saved_conversation_shows_answer_and_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _page(monkeypatch, FakeApi(user=EMPLOYEE), ui.COMPANY_KNOWLEDGE)
    app.session_state["conversation_id"] = "c" * 32
    app.run()
    text = _all_text(app)
    assert "Wait for the incident controller [1]." in text and "Searched as:" in text
    assert [e.label for e in app.expander] == ["Sources (1)"]
    assert "Do not re-enter an evacuated area" in text and "APM-HSE-MAN-003" in text


def test_api_errors_are_shown_in_plain_words(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(user=EMPLOYEE, conversations=False)
    fake.chat_error = ApiError(503, "llm_not_configured")
    app = _page(monkeypatch, fake, ui.COMPANY_KNOWLEDGE)
    app.chat_input[0].set_value("hello?")
    app.run()
    assert any("OPENAI_API_KEY" in e.value for e in app.error)
    assert "conversation_id" not in app.session_state


def test_unreachable_api_shows_how_to_start_it(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(monkeypatch, FakeApi(healthy=False))
    assert not app.exception
    assert any("not running" in e.value for e in app.error)
    assert not app.chat_input and not app.text_input


# ---- department managers ----


def test_a_manager_manages_only_their_own_departments_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _page(monkeypatch, FakeApi(user=MANAGER), ui.DEPARTMENT_DOCUMENTS)
    assert not app.exception
    assert app.title[0].value == "Department documents"
    labels = [b.label for b in app.button]
    assert labels.count("Re-index") == 1  # the safety document, not the HR one
    assert "Load the 6 sample documents" not in labels
    text = _all_text(app)
    assert "Company documents (1 of 1)" in text


# ---- admin workspace ----


def test_the_overview_shows_the_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(monkeypatch, FakeApi(user=ADMIN))
    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Accounts"] == "6" and metrics["Disabled"] == "1"
    assert metrics["Company documents"] == "6" and metrics["Archived documents"] == "1"
    assert metrics["Employees"] == "3" and metrics["Super Admins"] == "1"
    rows = app.dataframe[0].value
    assert list(rows["Department"])[:2] == ["Production", "Maintenance"]
    text = _all_text(app)
    assert "Factory Health & Safety Manual" in text
    assert "Asha Admin: Account disabled (Esha Production)" in text


def test_employee_management_lists_everyone_and_protects_your_own_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _page(monkeypatch, FakeApi(user=ADMIN), ui.EMPLOYEES)
    assert not app.exception
    text = _all_text(app)
    assert "People (3 of 3)" in text and "Asha Admin" in text and "Esha Production" in text
    labels = [b.label for b in app.button]
    assert "Disable" in labels and "Enable" in labels  # the employee, the disabled manager
    assert labels.count("Disable") == 1  # none for your own row


def test_employee_management_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _page(monkeypatch, FakeApi(user=ADMIN), ui.EMPLOYEES)
    next(t for t in app.text_input if t.key == "people-search").set_value("esha").run()
    assert "People (1 of 3)" in _all_text(app)
    next(s for s in app.selectbox if s.key == "people-status").select("Disabled").run()
    assert "People (0 of 3)" in _all_text(app)


def test_disabling_a_user_calls_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(user=ADMIN)
    app = _page(monkeypatch, fake, ui.EMPLOYEES)
    next(b for b in app.button if b.key == "toggle-u-emp").click().run()
    assert fake.updated == [("u-emp", {"is_active": False})]


def test_editing_a_role_and_departments_calls_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(user=ADMIN)
    app = _page(monkeypatch, fake, ui.EMPLOYEES)
    next(s for s in app.selectbox if s.key == "role-u-emp").select("Department Manager")
    next(m for m in app.multiselect if m.key == "deps-u-emp").set_value(["Safety", "HR"])
    next(b for b in app.button if b.key == "save-u-emp").click().run()
    assert fake.updated == [
        ("u-emp", {"role": "department_manager", "departments": ["hr", "safety"]})
    ]


def test_the_create_employee_page_shows_a_generated_password_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeApi(user=ADMIN)
    fake.temporary_password = "Gen3rated-Temp-Pass"
    app = _page(monkeypatch, fake, ui.CREATE_EMPLOYEE)
    labels = [t.label for t in app.text_input]
    assert "Temporary password (optional)" in labels and "Login ID (optional)" in labels
    next(t for t in app.text_input if t.label == "Email").set_value("new@apex.test")
    next(t for t in app.text_input if t.label == "Full name").set_value("New Person")
    app.button[0].click().run()
    assert fake.created[0][2] is None  # blank means "generate one"
    assert any(c.value == "Gen3rated-Temp-Pass" for c in app.code)
    app.run()  # any later interaction: the password is gone
    assert not any(c.value == "Gen3rated-Temp-Pass" for c in app.code)


def test_company_documents_offers_every_management_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeApi(user=ADMIN)
    fake.documents = [DOCUMENT, FAILED_DOCUMENT, ARCHIVED_DOCUMENT, PERSONAL_DOCUMENT]
    app = _page(monkeypatch, fake, ui.COMPANY_DOCUMENTS)
    assert not app.exception
    text = _all_text(app)
    assert "Company documents (3 of 3)" in text  # the personal one is not a company document
    assert ":gray-badge[Archived]" in text and ":green-badge[Ready]" in text
    labels = [b.label for b in app.button]
    assert labels.count("Archive") == 2 and labels.count("Restore") == 1
    assert "Upload new version" in labels and "Show versions" in labels


def test_archiving_and_restoring_call_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(user=ADMIN)
    fake.documents = [DOCUMENT, ARCHIVED_DOCUMENT]
    app = _page(monkeypatch, fake, ui.COMPANY_DOCUMENTS)
    next(b for b in app.button if b.key == "archive-d1").click().run()
    next(b for b in app.button if b.key == "restore-d4").click().run()
    assert fake.document_calls == [("archive", "d1"), ("restore", "d4")]


def test_company_documents_can_be_filtered(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(user=ADMIN)
    fake.documents = [DOCUMENT, FAILED_DOCUMENT, ARCHIVED_DOCUMENT]
    app = _page(monkeypatch, fake, ui.COMPANY_DOCUMENTS)
    next(s for s in app.selectbox if s.key == "manage-status").select("Archived").run()
    assert "Company documents (1 of 3)" in _all_text(app)
    next(t for t in app.text_input if t.key == "manage-search").set_value("nothing like it").run()
    assert "No documents match." in _all_text(app)


def test_upload_company_documents_lets_a_super_admin_pick_any_department(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _page(monkeypatch, FakeApi(user=ADMIN), ui.UPLOAD_COMPANY)
    assert not app.exception
    department = next(s for s in app.selectbox if s.key == "company-department")
    assert list(department.options) == [
        "Production",
        "Maintenance",
        "HR",
        "Safety",
        "Quality Control",
        "Stores",
    ]
    assert [t.key for t in app.text_input if t.key in {"company-title", "company-version"}] == [
        "company-title",
        "company-version",
    ]
    assert "Load the 6 sample documents" in [b.label for b in app.button]


def test_a_manager_can_only_upload_to_their_own_departments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _page(monkeypatch, FakeApi(user=MANAGER), ui.DEPARTMENT_DOCUMENTS)
    department = next(s for s in app.selectbox if s.key == "company-department")
    assert list(department.options) == ["Safety"]
    assert "Load the 6 sample documents" not in [b.label for b in app.button]


def test_the_departments_page_shows_all_six(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _page(monkeypatch, FakeApi(user=ADMIN), ui.DEPARTMENTS_PAGE)
    assert not app.exception
    text = _all_text(app)
    for name in ui.DEPARTMENTS.values():
        assert name in text
    assert "Managers: none yet" in text or "Managers:" in text


def test_the_audit_log_pages_and_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(user=ADMIN)
    app = _page(monkeypatch, fake, ui.AUDIT_LOGS)
    assert not app.exception
    assert "60 entries" in _all_text(app)
    rows = app.dataframe[0].value
    assert list(rows["Action"]) == ["Account disabled"] * 3
    assert list(rows["Who"]) == ["Asha Admin"] * 3
    assert fake.audit_requests[-1]["page"] == 1
    next(b for b in app.button if b.key == "audit-older").click().run()
    assert fake.audit_requests[-1]["page"] == 2
    next(s for s in app.selectbox if s.key == "audit-outcome").select("denied").run()
    assert fake.audit_requests[-1].get("outcome") == "denied"


# ---- links ----


def test_a_link_with_a_conversation_id_opens_that_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(monkeypatch, FakeApi(user=EMPLOYEE))
    app.session_state["nav"] = ui.CHAT_MINE
    app.query_params["c"] = "c" * 32
    app.run()
    assert app.session_state["conversation_id"] == "c" * 32
    assert "Wait for the incident controller [1]." in _all_text(app)


def test_a_malformed_conversation_link_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(monkeypatch, FakeApi(user=EMPLOYEE, conversations=False))
    app.session_state["nav"] = ui.CHAT_MINE
    app.query_params["c"] = "../../etc/passwd"
    app.run()
    assert "conversation_id" not in app.session_state
    assert not app.exception


@pytest.mark.parametrize(
    ("user", "title"),
    [(EMPLOYEE, "My documents"), (ADMIN, "Company documents")],
)
def test_an_old_documents_link_opens_the_right_page_for_the_role(
    monkeypatch: pytest.MonkeyPatch, user: dict[str, Any], title: str
) -> None:
    app = _app(monkeypatch, FakeApi(user=user))
    app.query_params["page"] = "documents"
    app.run()
    assert app.title[0].value == title


def test_an_old_users_link_works_for_admins_and_is_ignored_for_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _app(monkeypatch, FakeApi(user=ADMIN))
    admin.query_params["page"] = "users"
    admin.run()
    assert admin.title[0].value == "Employee management"
    other = _app(monkeypatch, FakeApi(user=EMPLOYEE))
    other.query_params["page"] = "users"
    other.run()
    assert other.title[0].value == "Welcome, Esha"


# ---- first-time setup ----


def _needs_setup(**state: bool) -> FakeApi:
    fake = FakeApi(user=None)
    fake.setup_state = {
        "setup_required": True,
        "token_required": False,
        "available": True,
        **state,
    }
    return fake


def _fill_setup(
    app: AppTest, password: str = "a-long-password-1", repeat: str | None = None
) -> None:
    app.text_input[0].set_value("The Boss")
    app.text_input[1].set_value("boss@apex.test")
    app.text_input[2].set_value(password)
    app.text_input[3].set_value(password if repeat is None else repeat)


def test_when_no_account_exists_the_setup_form_replaces_sign_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _run(monkeypatch, _needs_setup())
    assert not app.exception
    assert [t.label for t in app.text_input] == [
        "Full name",
        "Email",
        "Password",
        "Repeat password",
    ]
    assert "First-time setup" in [s.value for s in app.subheader]
    assert "no self sign-up" not in _all_text(app)


def test_the_setup_token_field_appears_only_when_one_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _run(monkeypatch, _needs_setup(token_required=True))
    assert [t.label for t in app.text_input][-1] == "Setup token"


def test_creating_the_first_account_signs_the_person_in(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _needs_setup()
    app = _run(monkeypatch, fake)
    _fill_setup(app)
    app.button[0].click().run()
    assert fake.setup_calls == [("boss@apex.test", "The Boss", "a-long-password-1", "")]
    assert fake.login_attempts == [("boss@apex.test", "a-long-password-1")]
    assert fake.login_portals == ["admin"]  # the first account is a Super Admin
    assert app.title[0].value == "Welcome, Esha"


def test_the_token_is_sent_when_required(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _needs_setup(token_required=True)
    app = _run(monkeypatch, fake)
    _fill_setup(app)
    app.text_input[4].set_value("the-token")
    app.button[0].click().run()
    assert fake.setup_calls[0][3] == "the-token"


def test_mismatched_passwords_are_caught_before_anything_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _needs_setup()
    app = _run(monkeypatch, fake)
    _fill_setup(app, repeat="something-else-123")
    app.button[0].click().run()
    assert fake.setup_calls == [] and fake.login_attempts == []
    assert any("do not match" in e.value for e in app.error)


def test_a_server_refusal_is_shown_in_plain_words(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _needs_setup(token_required=True)
    fake.setup_error = ApiError(403, "invalid_setup_token")
    app = _run(monkeypatch, fake)
    _fill_setup(app)
    app.text_input[4].set_value("wrong")
    app.button[0].click().run()
    assert any("setup token is not correct" in e.value for e in app.error)
    assert fake.login_attempts == []


def test_when_setup_is_switched_off_the_page_explains_the_command_line_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _run(monkeypatch, _needs_setup(available=False))
    assert not app.text_input  # no form to fill in
    assert any("SETUP_TOKEN" in w.value for w in app.warning)
    assert "create-user" in app.code[0].value


def test_once_an_account_exists_the_sign_in_options_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _run(monkeypatch, FakeApi(user=None))  # setup_required is False by default
    assert not any("First-time setup" in s.value for s in app.subheader)
    assert [b.label for b in app.button] == ["Continue as employee", "Continue as Super Admin"]


# ---- temporary passwords ----

TEMPORARY = {**EMPLOYEE, "must_change_password": True}


def _fill_change(app: AppTest, current: str, new: str, repeat: str | None = None) -> None:
    app.text_input[0].set_value(current)
    app.text_input[1].set_value(new)
    app.text_input[2].set_value(new if repeat is None else repeat)


def test_a_temporary_password_shows_only_the_change_form(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(monkeypatch, FakeApi(user=TEMPORARY))
    assert not app.exception
    assert [t.label for t in app.text_input] == [
        "Current (temporary) password",
        "New password",
        "Repeat new password",
    ]
    assert not app.chat_input and not app.sidebar.radio  # nothing else is reachable


def test_choosing_a_new_password_continues_into_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(user=TEMPORARY, conversations=False)
    app = _run(monkeypatch, fake)
    _fill_change(app, "temp-password-1", "my-own-password-9")
    app.button[0].click().run()
    assert fake.change_calls == [("temp-password-1", "my-own-password-9")]
    assert app.title[0].value == "Welcome, Esha"


def test_mismatched_new_passwords_are_caught_before_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeApi(user=TEMPORARY)
    app = _run(monkeypatch, fake)
    _fill_change(app, "temp-password-1", "my-own-password-9", repeat="different-password-9")
    app.button[0].click().run()
    assert fake.change_calls == []
    assert any("do not match" in e.value for e in app.error)


def test_a_server_refusal_stays_on_the_change_form_in_plain_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeApi(user=TEMPORARY)
    fake.change_error = ApiError(422, "current_password_incorrect")
    app = _run(monkeypatch, fake)
    _fill_change(app, "wrong", "my-own-password-9")
    app.button[0].click().run()
    assert any("current password is not correct" in e.value for e in app.error)
    assert [t.label for t in app.text_input][0] == "Current (temporary) password"


def test_a_source_the_person_can_no_longer_read_shows_a_plain_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gone = {
        "number": 2,
        "chunk_id": "",
        "doc_code": "",
        "doc_title": "",
        "source_name": "",
        "section_title": "",
        "quote": "",
        "available": False,
    }

    class WithGoneSource(FakeApi):
        def get_conversation(self, conversation_id: str) -> dict[str, Any]:
            answer = {**ANSWER, "sources": [*ANSWER["sources"], gone]}  # type: ignore[dict-item]
            return {"id": conversation_id, "title": "t", "messages": [QUESTION, answer]}

    app = _page(monkeypatch, WithGoneSource(user=EMPLOYEE), ui.COMPANY_KNOWLEDGE)
    app.session_state["conversation_id"] = "c" * 32
    app.run()
    assert not app.exception
    text = _all_text(app)
    assert "no longer available to you" in text
    assert "APM-HSE-MAN-003" in text  # the readable source is still shown


# ---- an old API copy, and errors that say what is wrong ----


def _respond(code: int, /, **body: Any) -> ApiClient:
    """A client whose API always answers with this HTTP code and JSON body."""
    return _client(lambda request: httpx.Response(code, json=body), logged_in=False)


def test_a_validation_problem_is_named_not_called_unknown() -> None:
    """HTTP 422 carries a list of problems. That used to surface as "unknown_error"."""
    client = _respond(422, detail=[{"type": "missing", "loc": ["body", "email"], "msg": "x"}])
    with pytest.raises(ApiError) as exc:
        client.login("someone", "a-password-1", portal="admin")
    assert (exc.value.status, exc.value.detail) == (422, "request_not_understood")
    text = ui.error_message(exc.value)
    assert "older copy" in text and "dev.ps1 -Restart" in text


def test_a_server_failure_without_a_reason_is_named() -> None:
    client = _respond(500, error="internal_server_error", request_id="abc123")
    with pytest.raises(ApiError) as exc:
        client.login("a", "b")
    assert exc.value.detail == "server_error"
    assert "API window" in ui.error_message(exc.value)


def test_a_reason_the_api_gives_is_still_used_as_it_is() -> None:
    with pytest.raises(ApiError) as exc:
        _respond(401, detail="invalid_credentials").login("a", "b")
    assert exc.value.detail == "invalid_credentials"


def test_unlisted_errors_show_their_status_too() -> None:
    assert "HTTP 418" in ui.error_message(ApiError(418, "teapot"))


@pytest.mark.parametrize(
    ("body", "expected"),
    [({"status": "ok", "api_level": 2}, 2), ({"status": "ok"}, None), ({"api_level": "2"}, None)],
)
def test_the_api_level_is_read_from_the_health_reply(
    body: dict[str, Any], expected: int | None
) -> None:
    assert _respond(200, **body).api_level() == expected


def test_an_unreachable_api_has_no_level() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert _client(refuse, logged_in=False).api_level() is None


@pytest.mark.parametrize("level", [None, api_client.REQUIRED_API_LEVEL - 1])
def test_an_old_api_copy_stops_the_page_before_sign_in(
    monkeypatch: pytest.MonkeyPatch, level: int | None
) -> None:
    app = _run(monkeypatch, FakeApi(user=None, level=level))
    assert not app.exception
    assert any("older copy" in e.value for e in app.error)
    assert "scripts\\dev.ps1 -Restart" in [c.value for c in app.code]
    assert not app.text_input and not app.button  # no sign-in form to fail confusingly


def test_a_current_api_shows_the_normal_page(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _run(monkeypatch, FakeApi(user=None))
    assert not any("older copy" in e.value for e in app.error)
    assert [b.label for b in app.button] == ["Continue as employee", "Continue as Super Admin"]


# ---- a page left running while the code was edited ----


class ClientFromOlderCode:
    """What Streamlit still holds in a session after a code reload: an object made by the old copy
    of the class, from before `api_level` existed."""

    user = None
    is_logged_in = False

    def healthy(self) -> bool:
        return True


def test_a_client_left_over_from_older_code_is_replaced_not_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: "AttributeError: 'ApiClient' object has no attribute 'api_level'"."""
    fake = FakeApi(user=None)
    app = _app(monkeypatch, fake)
    app.session_state["api"] = ClientFromOlderCode()  # kept by the browser session across a reload
    app.run()
    assert not app.exception
    assert app.session_state["api"] is fake  # the old object was dropped, a current one made
    assert [b.label for b in app.button] == ["Continue as employee", "Continue as Super Admin"]


def test_a_reload_of_the_client_code_signs_the_person_out_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = FakeApi(user=EMPLOYEE)
    app = _app(monkeypatch, first)
    app.run()
    assert app.title[0].value == "Welcome, Esha"
    assert app.session_state["api"] is first

    # The module is reloaded: `ApiClient` is now a different class object. The signed-in client
    # belongs to the old class, so it must not be used for the new code.
    second = FakeApi(user=None)
    monkeypatch.setattr(api_client, "ApiClient", lambda *a, **k: second)
    app.run()
    assert not app.exception
    assert app.session_state["api"] is second
    assert [b.label for b in app.button] == ["Continue as employee", "Continue as Super Admin"]


def test_an_ordinary_rerun_keeps_the_signed_in_client(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(user=ADMIN)
    app = _app(monkeypatch, fake)
    app.run()
    app.run()
    app.sidebar.radio[0].set_value(ui.EMPLOYEES).run()
    assert app.session_state["api"] is fake
    assert app.title[0].value == "Employee management"  # still signed in, nothing recreated
