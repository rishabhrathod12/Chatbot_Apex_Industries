"""More access-control tests with real users and real tokens: who may add documents, personal
privacy, conversation privacy, the audit trail, user management and the account tools."""

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cli
from app.api.deps import get_chat_model_provider
from app.core.config import Settings
from app.core.permissions import Role
from app.models.audit import AuditLog
from tests.conftest import PASSWORD, ScriptedChat, add_user, login

DOCS = "/api/v1/documents"
USERS = "/api/v1/users"
_counter = iter(range(10_000))


def _note(topic: str = "Rules") -> tuple[str, bytes]:
    """A small unique text document, so that every upload is new (not a duplicate)."""
    n = next(_counter)
    return (
        f"note{n}.txt",
        f"1. {topic}\n- rule number {n} about {topic.lower()} procedures.\n".encode(),
    )


@pytest.fixture
def people(
    raw_client: TestClient, session: Session, settings: Settings
) -> dict[str, dict[str, str]]:
    """Real users, logged in. Returns the Authorization headers by name."""
    spec = {
        "admin": (Role.SUPER_ADMIN, []),
        "safety_mgr": (Role.DEPARTMENT_MANAGER, ["safety"]),
        "prod_emp": (Role.EMPLOYEE, ["production"]),
        "stores_emp": (Role.EMPLOYEE, ["stores"]),
    }
    headers = {}
    for name, (role, departments) in spec.items():
        add_user(session, settings, f"{name}@apex.test", role, departments)
        headers[name] = login(raw_client, f"{name}@apex.test")
    return headers


@pytest.fixture
def scripted(raw_client: TestClient) -> ScriptedChat:
    model = ScriptedChat("From the excerpts [1].")
    raw_client.app.dependency_overrides[get_chat_model_provider] = lambda: lambda: model  # type: ignore[attr-defined]
    return model


def _upload(client: TestClient, headers: dict[str, str], **form: str) -> Any:
    filename, content = _note()
    return client.post(DOCS, headers=headers, data=form, files=[("files", (filename, content))])


def _audit(session: Session, action: str, outcome: str) -> list[AuditLog]:
    session.expire_all()
    return list(
        session.scalars(
            select(AuditLog).where(AuditLog.action == action, AuditLog.outcome == outcome)
        )
    )


# ---------- who may add documents ----------


@pytest.mark.parametrize(
    ("user", "form", "expected"),
    [
        ("prod_emp", {"scope": "personal"}, 201),
        ("prod_emp", {"scope": "enterprise", "department": "production"}, 403),
        (
            "prod_emp",
            {"scope": "enterprise", "department": "production", "visibility": "company"},
            403,
        ),
        ("safety_mgr", {"scope": "personal"}, 201),
        ("safety_mgr", {"scope": "enterprise", "department": "safety"}, 201),
        (
            "safety_mgr",
            {"scope": "enterprise", "department": "safety", "visibility": "company"},
            201,
        ),
        ("safety_mgr", {"scope": "enterprise", "department": "hr"}, 403),
        ("safety_mgr", {"scope": "enterprise", "department": "hr", "visibility": "company"}, 403),
        ("admin", {"scope": "enterprise", "department": "quality"}, 201),
        ("admin", {"scope": "enterprise", "department": "quality", "visibility": "company"}, 201),
        ("admin", {"scope": "enterprise", "department": "finance"}, 422),
        ("admin", {"scope": "enterprise"}, 422),
        ("admin", {"scope": "enterprise", "department": "quality", "visibility": "everyone"}, 422),
    ],
)
def test_who_may_add_documents(
    raw_client: TestClient,
    people: dict[str, dict[str, str]],
    user: str,
    form: dict[str, str],
    expected: int,
) -> None:
    assert _upload(raw_client, people[user], **form).status_code == expected


def test_uploading_needs_a_login(raw_client: TestClient) -> None:
    filename, content = _note()
    response = raw_client.post(
        DOCS, data={"scope": "personal"}, files=[("files", (filename, content))]
    )
    assert response.status_code == 401


def test_a_company_wide_upload_by_a_manager_becomes_visible_to_everyone(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    only_dept = _upload(raw_client, people["safety_mgr"], scope="enterprise", department="safety")
    everyone = _upload(
        raw_client,
        people["safety_mgr"],
        scope="enterprise",
        department="safety",
        visibility="company",
    )
    private_id = only_dept.json()["results"][0]["document"]["id"]
    public_id = everyone.json()["results"][0]["document"]["id"]
    assert raw_client.get(f"{DOCS}/{public_id}", headers=people["stores_emp"]).status_code == 200
    assert raw_client.get(f"{DOCS}/{private_id}", headers=people["stores_emp"]).status_code == 404
    assert raw_client.get(f"{DOCS}/{private_id}", headers=people["safety_mgr"]).status_code == 200


def test_the_visibility_choice_is_ignored_for_personal_documents(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    response = _upload(raw_client, people["prod_emp"], scope="personal", visibility="company")
    document = response.json()["results"][0]["document"]
    assert document["visibility"] == "personal"
    assert raw_client.get(f"{DOCS}/{document['id']}", headers=people["admin"]).status_code == 404


# ---------- deleting ----------


@pytest.mark.parametrize(
    ("owner", "department", "visibility", "user", "expected"),
    [
        ("admin", "safety", "company", "admin", 204),
        ("admin", "safety", "company", "safety_mgr", 204),
        ("admin", "safety", "company", "prod_emp", 403),  # can read it, may not delete it
        ("admin", "safety", "company", "stores_emp", 403),
        ("admin", "production", "department", "prod_emp", 403),
        ("admin", "production", "department", "safety_mgr", 404),  # cannot even see it
        ("admin", "production", "department", "stores_emp", 404),
        ("admin", "production", "department", "admin", 204),
    ],
)
def test_who_may_delete_company_documents(
    raw_client: TestClient,
    people: dict[str, dict[str, str]],
    owner: str,
    department: str,
    visibility: str,
    user: str,
    expected: int,
) -> None:
    created = _upload(
        raw_client, people[owner], scope="enterprise", department=department, visibility=visibility
    )
    doc_id = created.json()["results"][0]["document"]["id"]
    assert raw_client.delete(f"{DOCS}/{doc_id}", headers=people[user]).status_code == expected


def test_a_refused_delete_leaves_the_document_and_its_search_data_alone(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    doc_id = _upload(
        raw_client, people["admin"], scope="enterprise", department="production"
    ).json()["results"][0]["document"]["id"]
    assert raw_client.delete(f"{DOCS}/{doc_id}", headers=people["stores_emp"]).status_code == 404
    assert raw_client.get(f"{DOCS}/{doc_id}", headers=people["admin"]).json()["status"] == "ready"
    hits = raw_client.post(
        "/api/v1/search", headers=people["prod_emp"], json={"query": "rule procedures"}
    ).json()
    assert hits


# ---------- personal documents and conversations are private ----------


def test_personal_documents_are_private_even_from_a_super_admin(
    raw_client: TestClient, people: dict[str, dict[str, str]], scripted: ScriptedChat
) -> None:
    doc_id = _upload(raw_client, people["prod_emp"], scope="personal").json()["results"][0][
        "document"
    ]["id"]
    body = {"query": "rule procedures", "scope": "personal"}

    assert raw_client.post("/api/v1/search", headers=people["prod_emp"], json=body).json()
    for outsider in ("admin", "safety_mgr", "stores_emp"):
        assert raw_client.get(f"{DOCS}/{doc_id}", headers=people[outsider]).status_code == 404
        assert raw_client.delete(f"{DOCS}/{doc_id}", headers=people[outsider]).status_code == 404
        assert (
            raw_client.post(f"{DOCS}/{doc_id}/reindex", headers=people[outsider]).status_code == 404
        )
        assert raw_client.post("/api/v1/search", headers=people[outsider], json=body).json() == []
        assert all(d["id"] != doc_id for d in raw_client.get(DOCS, headers=people[outsider]).json())
        reply = raw_client.post(
            "/api/v1/chat",
            headers=people[outsider],
            json={"question": "rule procedures", "scope": "personal"},
        ).json()
        assert reply["status"] == "not_found"


def test_personal_documents_never_appear_in_company_searches(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    _upload(raw_client, people["prod_emp"], scope="personal")
    for who in ("admin", "prod_emp"):
        assert (
            raw_client.post(
                "/api/v1/search", headers=people[who], json={"query": "rule procedures"}
            ).json()
            == []
        )


def test_conversations_belong_to_one_user_even_against_a_super_admin(
    raw_client: TestClient, people: dict[str, dict[str, str]], scripted: ScriptedChat
) -> None:
    started = raw_client.post(
        "/api/v1/chat", headers=people["prod_emp"], json={"question": "a private question"}
    ).json()
    cid = started["conversation_id"]
    for outsider in ("admin", "safety_mgr", "stores_emp"):
        base = f"/api/v1/conversations/{cid}"
        assert raw_client.get(base, headers=people[outsider]).status_code == 404
        assert raw_client.delete(base, headers=people[outsider]).status_code == 404
        follow_up = raw_client.post(
            "/api/v1/chat",
            headers=people[outsider],
            json={"question": "and then?", "conversation_id": cid},
        )
        assert follow_up.status_code == 404
        assert raw_client.get("/api/v1/conversations", headers=people[outsider]).json() == []
    assert (
        len(
            raw_client.get(f"/api/v1/conversations/{cid}", headers=people["prod_emp"]).json()[
                "messages"
            ]
        )
        == 2
    )


# ---------- the audit trail ----------


def test_refused_actions_are_recorded_with_who_and_what_but_no_content(
    raw_client: TestClient, people: dict[str, dict[str, str]], session: Session
) -> None:
    doc_id = _upload(
        raw_client, people["admin"], scope="enterprise", department="production"
    ).json()["results"][0]["document"]["id"]
    raw_client.get(f"{DOCS}/{doc_id}", headers=people["stores_emp"])  # cannot see it
    raw_client.delete(f"{DOCS}/{doc_id}", headers=people["prod_emp"])  # can see it, may not delete
    _upload(raw_client, people["prod_emp"], scope="enterprise", department="production")
    raw_client.get(USERS, headers=people["prod_emp"])  # not an admin

    stores = session.scalars(select(AuditLog).where(AuditLog.action == "document_access")).one()
    assert (stores.outcome, stores.target_type, stores.target_id) == ("denied", "document", doc_id)
    assert _audit(session, "document_delete", "denied")[0].target_id == doc_id
    assert _audit(session, "document_upload", "denied")[0].target_id == "production"
    assert len(_audit(session, "admin_endpoint", "denied")) == 1
    assert all(
        row.user_id
        for row in session.scalars(select(AuditLog))
        if row.outcome == "denied" and row.action != "login"
    )


def test_successful_changes_are_recorded_too(
    raw_client: TestClient, people: dict[str, dict[str, str]], session: Session
) -> None:
    doc_id = _upload(
        raw_client, people["admin"], scope="enterprise", department="production"
    ).json()["results"][0]["document"]["id"]
    raw_client.post(f"{DOCS}/{doc_id}/reindex", headers=people["admin"])
    raw_client.delete(f"{DOCS}/{doc_id}", headers=people["admin"])
    for action in ("document_upload", "document_reindex", "document_delete"):
        assert [r.target_id for r in _audit(session, action, "ok")] == [doc_id], action


# ---------- managing users ----------


def _new_user(**override: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "email": "new.person@apex.test",
        "full_name": "New Person",
        "password": "a-long-enough-password",
        "role": "employee",
        "departments": ["quality"],
    }
    return {**body, **override}


def test_an_admin_can_create_a_user_who_can_then_log_in(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    response = raw_client.post(
        USERS, headers=people["admin"], json=_new_user(email="New.Person@Apex.test")
    )
    assert response.status_code == 201
    body = response.json()
    assert (body["email"], body["role"], body["departments"], body["is_active"]) == (
        "new.person@apex.test",
        "employee",
        ["quality"],
        True,
    )
    assert "password" not in response.text
    login(raw_client, "new.person@apex.test", "a-long-enough-password")


@pytest.mark.parametrize(
    ("override", "status", "detail"),
    [
        ({"password": "short"}, 422, "password_too_short"),
        ({"role": "manager"}, 422, None),
        ({"departments": ["finance"]}, 422, None),
        ({"role": "department_manager", "departments": []}, 422, "manager_needs_department"),
        ({"email": "admin@apex.test"}, 409, "email_taken"),
        ({"email": "ADMIN@apex.test"}, 409, "email_taken"),
        ({"email": "not-an-email"}, 422, None),
    ],
)
def test_user_creation_rules(
    raw_client: TestClient,
    people: dict[str, dict[str, str]],
    override: dict[str, Any],
    status: int,
    detail: str | None,
) -> None:
    response = raw_client.post(USERS, headers=people["admin"], json=_new_user(**override))
    assert response.status_code == status
    if detail:
        assert response.json() == {"detail": detail}


@pytest.mark.parametrize("user", ["safety_mgr", "prod_emp", "stores_emp"])
def test_only_super_admins_can_manage_users(
    raw_client: TestClient, people: dict[str, dict[str, str]], user: str
) -> None:
    other = raw_client.get(USERS, headers=people["admin"]).json()[0]["id"]
    attempts = [
        raw_client.get(USERS, headers=people[user]),
        raw_client.post(USERS, headers=people[user], json=_new_user()),
        raw_client.patch(f"{USERS}/{other}", headers=people[user], json={"role": "super_admin"}),
        raw_client.post(
            f"{USERS}/{other}/password", headers=people[user], json={"password": "x" * 20}
        ),
    ]
    assert [a.status_code for a in attempts] == [403, 403, 403, 403]


def test_nobody_can_promote_themselves(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    me = raw_client.get("/api/v1/auth/me", headers=people["prod_emp"]).json()["user_id"]
    response = raw_client.patch(
        f"{USERS}/{me}", headers=people["prod_emp"], json={"role": "super_admin"}
    )
    assert response.status_code == 403
    assert (
        raw_client.get("/api/v1/auth/me", headers=people["prod_emp"]).json()["role"] == "employee"
    )


def test_changing_a_departments_takes_effect_at_once(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    doc_id = _upload(
        raw_client, people["admin"], scope="enterprise", department="production"
    ).json()["results"][0]["document"]["id"]
    assert raw_client.get(f"{DOCS}/{doc_id}", headers=people["stores_emp"]).status_code == 404
    stores_id = raw_client.get("/api/v1/auth/me", headers=people["stores_emp"]).json()["user_id"]

    moved = raw_client.patch(
        f"{USERS}/{stores_id}",
        headers=people["admin"],
        json={"departments": ["production", "stores"]},
    )
    assert moved.status_code == 200
    assert (
        raw_client.get(f"{DOCS}/{doc_id}", headers=people["stores_emp"]).status_code == 200
    )  # same token

    raw_client.patch(
        f"{USERS}/{stores_id}", headers=people["admin"], json={"departments": ["stores"]}
    )
    assert raw_client.get(f"{DOCS}/{doc_id}", headers=people["stores_emp"]).status_code == 404


def test_disabling_a_user_ends_their_sessions(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    pair = raw_client.post(
        "/api/v1/auth/login", json={"email": "prod_emp@apex.test", "password": PASSWORD}
    ).json()
    uid = pair["user"]["user_id"]
    off = raw_client.patch(f"{USERS}/{uid}", headers=people["admin"], json={"is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert raw_client.get("/api/v1/auth/me", headers=people["prod_emp"]).status_code == 401
    refreshed = raw_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
    )
    assert refreshed.status_code == 401
    login_try = raw_client.post(
        "/api/v1/auth/login", json={"email": "prod_emp@apex.test", "password": PASSWORD}
    )
    assert login_try.status_code == 401


def test_an_admin_cannot_demote_or_disable_themselves(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    me = raw_client.get("/api/v1/auth/me", headers=people["admin"]).json()["user_id"]
    for change in ({"role": "employee"}, {"is_active": False}):
        response = raw_client.patch(f"{USERS}/{me}", headers=people["admin"], json=change)
        assert (response.status_code, response.json()) == (
            409,
            {"detail": "cannot_demote_or_disable_self"},
        )


def test_a_password_reset_ends_old_sessions_and_the_old_password(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    pair = raw_client.post(
        "/api/v1/auth/login", json={"email": "prod_emp@apex.test", "password": PASSWORD}
    ).json()
    uid = pair["user"]["user_id"]
    short = raw_client.post(
        f"{USERS}/{uid}/password", headers=people["admin"], json={"password": "short"}
    )
    assert (short.status_code, short.json()) == (422, {"detail": "password_too_short"})

    done = raw_client.post(
        f"{USERS}/{uid}/password",
        headers=people["admin"],
        json={"password": "brand-new-password-1"},
    )
    assert done.status_code == 204
    assert (
        raw_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        ).status_code
        == 401
    )
    old = raw_client.post(
        "/api/v1/auth/login", json={"email": "prod_emp@apex.test", "password": PASSWORD}
    )
    assert old.status_code == 401
    login(raw_client, "prod_emp@apex.test", "brand-new-password-1")


def test_unknown_users_give_404(raw_client: TestClient, people: dict[str, dict[str, str]]) -> None:
    missing = "0" * 32
    assert (
        raw_client.patch(
            f"{USERS}/{missing}", headers=people["admin"], json={"is_active": False}
        ).status_code
        == 404
    )
    assert (
        raw_client.post(
            f"{USERS}/{missing}/password", headers=people["admin"], json={"password": "x" * 20}
        ).status_code
        == 404
    )


# ---------- the account tools (command line) ----------


@pytest.fixture
def cli_settings(settings: Settings, session: Session, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


def test_cli_creates_the_first_super_admin(
    cli_settings: Settings,
    raw_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FIRST_ADMIN_PASSWORD", "a-long-first-password")
    code = cli.main(
        [
            "create-user",
            "--email",
            "boss@apex.test",
            "--name",
            "The Boss",
            "--role",
            "super_admin",
            "--password-env",
            "FIRST_ADMIN_PASSWORD",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "boss@apex.test" in out
    assert "a-long-first-password" not in out  # the password is never echoed
    headers = login(raw_client, "boss@apex.test", "a-long-first-password")
    assert raw_client.get(USERS, headers=headers).status_code == 200


def test_cli_refuses_weak_passwords_and_duplicates(
    cli_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    args = [
        "create-user",
        "--email",
        "a@apex.test",
        "--name",
        "A",
        "--role",
        "employee",
        "--password-env",
        "PW",
    ]
    monkeypatch.setenv("PW", "short")
    assert cli.main(args) == 1
    assert "password_too_short" in capsys.readouterr().err
    monkeypatch.setenv("PW", "a-long-enough-password")
    assert cli.main(args) == 0
    assert cli.main(args) == 1
    assert "email_taken" in capsys.readouterr().err


def test_cli_needs_a_password_source(
    cli_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NOT_SET", raising=False)
    with pytest.raises(SystemExit):
        cli.main(
            [
                "create-user",
                "--email",
                "a@apex.test",
                "--name",
                "A",
                "--role",
                "employee",
                "--password-env",
                "NOT_SET",
            ]
        )


def test_seed_demo_creates_six_users_sharing_one_generated_password(
    cli_settings: Settings, raw_client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["seed-demo"]) == 0
    out = capsys.readouterr().out
    password = re.search(r"^\s{4}(\S{16})\s*$", out, re.MULTILINE)
    assert password, out
    for email in ("admin@apex.test", "safety.manager@apex.test", "stores.employee@apex.test"):
        assert email in out
        login(raw_client, email, password.group(1))

    assert cli.main(["seed-demo"]) == 0  # running it again changes nothing
    assert "already exist" in capsys.readouterr().out


def test_seed_demo_refuses_to_run_in_production(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    production = settings.model_copy(update={"app_env": "production"})
    monkeypatch.setattr(cli, "get_settings", lambda: production)
    assert cli.main(["seed-demo"]) == 1
    assert "refuses to run in production" in capsys.readouterr().err
