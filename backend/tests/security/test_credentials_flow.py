"""Temporary passwords and the forced change at first sign-in (auth redesign, phase 2)."""

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.permissions import Role
from app.models import AuditLog, User
from app.services import users as user_service
from tests.conftest import PASSWORD, add_user, login

USERS = "/api/v1/users"
LOGIN = "/api/v1/auth/login"
CHANGE = "/api/v1/auth/change-password"
ME = "/api/v1/auth/me"
NEW_PASSWORD = "My-Own-Password-42"


@pytest.fixture
def admin(raw_client: TestClient, session: Session, settings: Settings) -> dict[str, str]:
    add_user(session, settings, "boss@apex.test", Role.SUPER_ADMIN)
    return login(raw_client, "boss@apex.test")


def _create(client: TestClient, admin: dict[str, str], **changes: Any):  # noqa: ANN202
    body = {
        "email": "esha@apex.test",
        "full_name": "Esha Production",
        "role": "employee",
        "departments": ["production"],
        **changes,
    }
    return client.post(USERS, headers=admin, json=body)


def _sign_in(client: TestClient, who: str, password: str):  # noqa: ANN202
    return client.post(LOGIN, json={"login_id": who, "password": password})


def _headers(response: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ---- creating an account ----


def test_an_account_made_without_a_password_gets_a_generated_one_shown_once(
    raw_client: TestClient, admin: dict[str, str], session: Session, settings: Settings
) -> None:
    created = _create(raw_client, admin)
    assert created.status_code == 201
    body = created.json()
    temporary = body["temporary_password"]
    assert temporary and len(temporary) >= settings.password_min_length
    assert body["must_change_password"] is True
    assert body["login_id"] == "esha.production"  # made from the name

    # never shown again
    listed = raw_client.get(USERS, headers=admin)
    assert temporary not in listed.text
    assert all("temporary_password" not in u for u in listed.json())

    # never stored: not in any column of any table, and the hash really is a hash
    session.expire_all()
    user = session.scalars(select(User).where(User.login_id == "esha.production")).one()
    assert user.password_hash.startswith("scrypt$") and temporary not in user.password_hash
    for table in ("users", "audit_log", "refresh_tokens", "user_departments"):
        rows = session.execute(text(f"SELECT * FROM {table}")).fetchall()  # noqa: S608
        assert temporary not in repr(rows), table
    assert user.created_by is not None


def test_a_typed_password_is_not_echoed_but_still_has_to_be_changed(
    raw_client: TestClient, admin: dict[str, str]
) -> None:
    created = _create(raw_client, admin, password="Typed-By-The-Admin-1")
    body = created.json()
    assert body["temporary_password"] is None
    assert body["must_change_password"] is True
    assert "Typed-By-The-Admin-1" not in created.text


def test_the_temporary_password_is_never_logged(
    raw_client: TestClient, admin: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    temporary = _create(raw_client, admin).json()["temporary_password"]
    assert _sign_in(raw_client, "esha.production", temporary).status_code == 200
    assert temporary not in caplog.text


def test_a_generated_password_always_meets_the_length_rule(settings: Settings) -> None:
    settings.password_min_length = 40
    assert len(user_service.generate_temporary_password(settings)) >= 40
    assert user_service.generate_temporary_password(
        settings
    ) != user_service.generate_temporary_password(settings)


def test_login_ids_come_from_the_name_and_can_be_chosen(
    raw_client: TestClient, admin: dict[str, str]
) -> None:
    first = _create(raw_client, admin, email="a@apex.test", full_name="Rahul K. Kumar")
    second = _create(raw_client, admin, email="b@apex.test", full_name="Rahul Kumar")
    chosen = _create(raw_client, admin, email="c@apex.test", login_id="custom-id.7")
    assert first.json()["login_id"] == "rahul.kumar"
    assert second.json()["login_id"] == "rahul.kumar2"
    assert chosen.json()["login_id"] == "custom-id.7"
    taken = _create(raw_client, admin, email="d@apex.test", login_id="CUSTOM-ID.7")
    assert (taken.status_code, taken.json()["detail"]) == (409, "login_id_taken")
    assert _create(raw_client, admin, email="e@apex.test", login_id="no spaces").status_code == 422
    assert _create(raw_client, admin, email="f@apex.test", login_id="ab").status_code == 422


def test_only_a_super_admin_can_create_accounts_or_reset_passwords(
    raw_client: TestClient, session: Session, settings: Settings
) -> None:
    boss = add_user(session, settings, "boss@apex.test", Role.SUPER_ADMIN)
    add_user(session, settings, "emp@apex.test", Role.EMPLOYEE, ["production"])
    add_user(session, settings, "mgr@apex.test", Role.DEPARTMENT_MANAGER, ["hr"])
    for email in ("emp@apex.test", "mgr@apex.test"):
        headers = login(raw_client, email)
        assert _create(raw_client, headers, email="x@apex.test").status_code == 403
        reset = raw_client.post(f"{USERS}/{boss.id}/password", headers=headers, json={})
        assert reset.status_code == 403
    assert raw_client.post(USERS, json={}).status_code == 401


# ---- the forced change ----


def test_someone_with_a_temporary_password_can_only_change_it(
    raw_client: TestClient, admin: dict[str, str]
) -> None:
    temporary = _create(raw_client, admin).json()["temporary_password"]
    signed_in = _sign_in(raw_client, "esha.production", temporary)
    assert signed_in.status_code == 200
    assert signed_in.json()["user"]["must_change_password"] is True
    headers = _headers(signed_in)

    # the app, documents, chat, conversations, admin pages: all blocked, server-side
    for method, path in (
        ("GET", "/api/v1/documents"),
        ("GET", "/api/v1/conversations"),
        ("POST", "/api/v1/chat"),
        ("POST", "/api/v1/search"),
        ("GET", USERS),
        ("POST", USERS),
    ):
        blocked = raw_client.request(method, path, headers=headers, json={})
        assert (blocked.status_code, blocked.json()["detail"]) == (
            403,
            "password_change_required",
        ), path
    # ... but they can see who they are, and that they must change the password
    me = raw_client.get(ME, headers=headers)
    assert me.status_code == 200 and me.json()["must_change_password"] is True


def test_changing_the_password_unlocks_the_app_and_ends_other_sessions(
    raw_client: TestClient, admin: dict[str, str]
) -> None:
    temporary = _create(raw_client, admin).json()["temporary_password"]
    first = _sign_in(raw_client, "esha.production", temporary).json()
    other = _sign_in(raw_client, "esha.production", temporary).json()  # a second session
    headers = {"Authorization": f"Bearer {first['access_token']}"}

    changed = raw_client.post(
        CHANGE,
        headers=headers,
        json={"current_password": temporary, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 200
    pair = changed.json()
    assert pair["user"]["must_change_password"] is False
    new_headers = {"Authorization": f"Bearer {pair['access_token']}"}

    assert raw_client.get("/api/v1/documents", headers=new_headers).status_code == 200
    assert raw_client.get(ME, headers=new_headers).json()["must_change_password"] is False
    # old password dead, new one works, the other session's refresh token is revoked
    assert _sign_in(raw_client, "esha.production", temporary).status_code == 401
    assert _sign_in(raw_client, "esha.production", NEW_PASSWORD).status_code == 200
    stale = raw_client.post("/api/v1/auth/refresh", json={"refresh_token": other["refresh_token"]})
    assert stale.status_code == 401
    # the flag lives in the database, so the earlier access token is released too
    assert raw_client.get("/api/v1/documents", headers=headers).status_code == 200


def test_a_wrong_current_password_changes_nothing(
    raw_client: TestClient, admin: dict[str, str], session: Session
) -> None:
    temporary = _create(raw_client, admin).json()["temporary_password"]
    headers = _headers(_sign_in(raw_client, "esha.production", temporary))
    refused = raw_client.post(
        CHANGE,
        headers=headers,
        json={"current_password": "not-it-at-all-1", "new_password": NEW_PASSWORD},
    )
    # 422, never 401: the app would read a 401 as "your session expired"
    assert (refused.status_code, refused.json()) == (422, {"detail": "current_password_incorrect"})
    assert _sign_in(raw_client, "esha.production", temporary).json()["user"]["must_change_password"]
    assert _sign_in(raw_client, "esha.production", NEW_PASSWORD).status_code == 401
    session.expire_all()
    failed = session.scalars(
        select(AuditLog).where(AuditLog.action == "password_change", AuditLog.outcome == "failed")
    ).all()
    assert len(failed) == 1


@pytest.mark.parametrize(
    ("new_password", "reason"),
    [("short", "password_too_short"), (None, "password_unchanged")],
)
def test_a_new_password_must_be_long_enough_and_different(
    raw_client: TestClient, admin: dict[str, str], new_password: str | None, reason: str
) -> None:
    temporary = _create(raw_client, admin).json()["temporary_password"]
    headers = _headers(_sign_in(raw_client, "esha.production", temporary))
    refused = raw_client.post(
        CHANGE,
        headers=headers,
        json={"current_password": temporary, "new_password": new_password or temporary},
    )
    assert (refused.status_code, refused.json()["detail"]) == (422, reason)


def test_guessing_the_current_password_is_rate_limited(
    raw_client: TestClient, admin: dict[str, str], settings: Settings
) -> None:
    temporary = _create(raw_client, admin).json()["temporary_password"]
    headers = _headers(_sign_in(raw_client, "esha.production", temporary))
    for _ in range(settings.login_max_failures):
        wrong = raw_client.post(
            CHANGE,
            headers=headers,
            json={"current_password": "guess-guess-guess", "new_password": NEW_PASSWORD},
        )
        assert wrong.status_code == 422
    blocked = raw_client.post(
        CHANGE, headers=headers, json={"current_password": temporary, "new_password": NEW_PASSWORD}
    )
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0


def test_changing_a_password_needs_a_login(raw_client: TestClient) -> None:
    body = {"current_password": "x", "new_password": NEW_PASSWORD}
    assert raw_client.post(CHANGE, json=body).status_code == 401


def test_an_ordinary_password_change_is_allowed_and_audited_without_secrets(
    raw_client: TestClient, session: Session, settings: Settings
) -> None:
    add_user(session, settings, "emp@apex.test", Role.EMPLOYEE, ["production"])
    headers = login(raw_client, "emp@apex.test")
    done = raw_client.post(
        CHANGE, headers=headers, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert done.status_code == 200
    session.expire_all()
    rows = session.scalars(select(AuditLog).where(AuditLog.action == "password_change")).all()
    assert [r.outcome for r in rows] == ["ok"]
    assert NEW_PASSWORD not in repr(session.execute(text("SELECT * FROM audit_log")).fetchall())


# ---- resetting ----


def test_a_reset_generates_a_password_forces_a_change_and_ends_sessions(
    raw_client: TestClient, admin: dict[str, str], session: Session, settings: Settings
) -> None:
    add_user(session, settings, "emp@apex.test", Role.EMPLOYEE, ["production"])
    pair = _sign_in(raw_client, "emp", PASSWORD).json()
    uid = pair["user"]["user_id"]

    reset = raw_client.post(f"{USERS}/{uid}/password", headers=admin, json={})
    assert reset.status_code == 200
    temporary = reset.json()["temporary_password"]
    assert temporary and temporary not in raw_client.get(USERS, headers=admin).text

    assert _sign_in(raw_client, "emp", PASSWORD).status_code == 401
    assert (
        raw_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        ).status_code
        == 401
    )
    again = _sign_in(raw_client, "emp", temporary)
    assert again.status_code == 200 and again.json()["user"]["must_change_password"] is True
    assert raw_client.get("/api/v1/documents", headers=_headers(again)).status_code == 403


def test_a_super_admin_with_a_temporary_password_cannot_use_admin_pages_either(
    raw_client: TestClient, admin: dict[str, str]
) -> None:
    uid = raw_client.get(ME, headers=admin).json()["user_id"]
    temporary = raw_client.post(f"{USERS}/{uid}/password", headers=admin, json={}).json()[
        "temporary_password"
    ]
    headers = _headers(_sign_in(raw_client, "boss", temporary))
    assert raw_client.get(USERS, headers=headers).status_code == 403
    assert raw_client.get(USERS, headers=headers).json()["detail"] == "password_change_required"


# ---- accounts that are not forced ----


def test_accounts_made_by_the_owner_themselves_are_not_forced_to_change(
    raw_client: TestClient, session: Session, settings: Settings
) -> None:
    add_user(session, settings, "me@apex.test", Role.SUPER_ADMIN)  # like the command line
    assert _sign_in(raw_client, "me", PASSWORD).json()["user"]["must_change_password"] is False
