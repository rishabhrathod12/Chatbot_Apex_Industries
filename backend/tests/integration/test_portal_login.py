"""Portal-aware sign-in and last-admin protection (auth redesign, phase 3).

The portal a person picks is only ever a way to be REFUSED. It never decides what an account may
do: the role always comes from the database.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.permissions import Principal, Role
from app.models import AuditLog
from app.schemas.auth import UserUpdate
from app.services import users as user_service
from tests.conftest import PASSWORD, add_user, login

LOGIN = "/api/v1/auth/login"
USERS = "/api/v1/users"


def _sign_in(client: TestClient, who: str, portal: str | None = None, password: str = PASSWORD):  # noqa: ANN202
    body = {"login_id": who, "password": password}
    if portal is not None:
        body["portal"] = portal
    return client.post(LOGIN, json=body)


@pytest.fixture
def accounts(session: Session, settings: Settings) -> None:
    add_user(session, settings, "boss@apex.test", Role.SUPER_ADMIN)
    add_user(session, settings, "mgr@apex.test", Role.DEPARTMENT_MANAGER, ["hr"])
    add_user(session, settings, "emp@apex.test", Role.EMPLOYEE, ["production"])


# ---- the portal gate ----


@pytest.mark.parametrize(
    ("who", "portal", "allowed"),
    [
        ("boss", "admin", True),
        ("boss", "employee", False),
        ("mgr", "employee", True),
        ("mgr", "admin", False),
        ("emp", "employee", True),
        ("emp", "admin", False),
    ],
)
def test_each_portal_admits_only_its_own_kind_of_account(
    raw_client: TestClient, accounts: None, who: str, portal: str, allowed: bool
) -> None:
    response = _sign_in(raw_client, who, portal)
    assert response.status_code == (200 if allowed else 401)
    if not allowed:
        assert response.json() == {"detail": "invalid_credentials"}


def test_without_a_portal_sign_in_still_works_for_existing_clients(
    raw_client: TestClient, accounts: None
) -> None:
    assert _sign_in(raw_client, "boss").status_code == 200
    assert _sign_in(raw_client, "emp").status_code == 200


def test_an_unknown_portal_is_rejected(raw_client: TestClient, accounts: None) -> None:
    assert _sign_in(raw_client, "boss", "superuser").status_code == 422


def test_a_role_sent_by_the_client_is_ignored(raw_client: TestClient, accounts: None) -> None:
    """The role is read from the database. Extra fields in the request change nothing."""
    response = raw_client.post(
        LOGIN,
        json={"login_id": "emp", "password": PASSWORD, "portal": "employee", "role": "super_admin"},
    )
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "employee"
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    assert raw_client.get(USERS, headers=headers).status_code == 403


def test_the_wrong_door_looks_exactly_like_a_wrong_password(
    raw_client: TestClient, accounts: None
) -> None:
    """So the sign-in page cannot be used to find out which accounts are admins."""
    wrong_door = _sign_in(raw_client, "emp", "admin")
    wrong_password = _sign_in(raw_client, "emp", "employee", password="not-the-password-1")
    unknown = _sign_in(raw_client, "nobody", "admin")
    assert wrong_door.status_code == wrong_password.status_code == unknown.status_code == 401
    assert wrong_door.json() == wrong_password.json() == unknown.json()
    assert dict(wrong_door.headers).keys() == dict(wrong_password.headers).keys()


def test_wrong_door_attempts_count_toward_the_lockout(
    raw_client: TestClient, accounts: None, settings: Settings
) -> None:
    for _ in range(settings.login_max_failures):
        assert _sign_in(raw_client, "emp", "admin").status_code == 401
    blocked = _sign_in(raw_client, "emp", "employee")  # the right door, right password
    assert blocked.status_code == 429


def test_a_wrong_door_attempt_is_audited_without_the_typed_name(
    raw_client: TestClient, accounts: None, session: Session
) -> None:
    _sign_in(raw_client, "emp", "admin")
    session.expire_all()
    rows = session.scalars(
        select(AuditLog).where(AuditLog.action == "login", AuditLog.target_type == "portal")
    ).all()
    assert len(rows) == 1
    assert (rows[0].outcome, rows[0].target_id, rows[0].user_id is not None) == (
        "failed",
        "admin",
        True,
    )


def test_a_successful_sign_in_after_a_wrong_door_clears_nothing_it_should_not(
    raw_client: TestClient, accounts: None
) -> None:
    assert _sign_in(raw_client, "boss", "employee").status_code == 401
    assert _sign_in(raw_client, "boss", "admin").status_code == 200


# ---- the last Super Admin ----


def _admin_headers(client: TestClient, who: str) -> dict[str, str]:
    return login(client, f"{who}@apex.test")


def test_an_admin_can_disable_another_admin_while_one_remains(
    raw_client: TestClient, session: Session, settings: Settings
) -> None:
    add_user(session, settings, "a@apex.test", Role.SUPER_ADMIN)
    b = add_user(session, settings, "b@apex.test", Role.SUPER_ADMIN)
    headers = _admin_headers(raw_client, "a")
    done = raw_client.patch(f"{USERS}/{b.id}", headers=headers, json={"is_active": False})
    assert done.status_code == 200 and done.json()["is_active"] is False


def test_an_admin_still_cannot_demote_or_disable_themselves(
    raw_client: TestClient, session: Session, settings: Settings
) -> None:
    a = add_user(session, settings, "a@apex.test", Role.SUPER_ADMIN)
    add_user(session, settings, "b@apex.test", Role.SUPER_ADMIN)
    headers = _admin_headers(raw_client, "a")
    for change in ({"is_active": False}, {"role": "employee"}):
        refused = raw_client.patch(f"{USERS}/{a.id}", headers=headers, json=change)
        assert (refused.status_code, refused.json()["detail"]) == (
            409,
            "cannot_demote_or_disable_self",
        )


def _principal(user: object) -> Principal:
    from app.services.auth import principal_of

    return principal_of(user)  # type: ignore[arg-type]


@pytest.mark.parametrize("change", [{"is_active": False}, {"role": Role.EMPLOYEE}])
def test_nobody_can_remove_the_last_active_super_admin(
    session: Session, settings: Settings, change: dict[str, object]
) -> None:
    """Two admins removing each other at the same moment: the second request still carries a
    valid-looking admin, but the first removal already happened, so only one admin is left."""
    a = add_user(session, settings, "a@apex.test", Role.SUPER_ADMIN)
    b = add_user(session, settings, "b@apex.test", Role.SUPER_ADMIN)
    stale_actor = _principal(a)
    a.is_active = False  # the other admin's request landed first
    session.commit()

    with pytest.raises(user_service.UserError) as err:
        user_service.update_user(session, stale_actor, b, UserUpdate(**change))  # type: ignore[arg-type]
    assert (err.value.reason, err.value.status) == ("last_super_admin", 409)
    session.refresh(b)
    assert b.is_active and b.role == "super_admin"


def test_changing_other_things_on_the_last_admin_is_fine(
    session: Session, settings: Settings
) -> None:
    a = add_user(session, settings, "a@apex.test", Role.SUPER_ADMIN)
    b = add_user(session, settings, "b@apex.test", Role.SUPER_ADMIN)
    stale_actor = _principal(a)
    a.is_active = False
    session.commit()
    updated = user_service.update_user(session, stale_actor, b, UserUpdate(full_name="Renamed"))
    assert updated.full_name == "Renamed"


def test_employees_and_managers_cannot_touch_admin_accounts(
    raw_client: TestClient, session: Session, settings: Settings
) -> None:
    boss = add_user(session, settings, "boss@apex.test", Role.SUPER_ADMIN)
    add_user(session, settings, "emp@apex.test", Role.EMPLOYEE)
    add_user(session, settings, "mgr@apex.test", Role.DEPARTMENT_MANAGER, ["hr"])
    for who in ("emp", "mgr"):
        headers = _admin_headers(raw_client, who)
        for change in ({"is_active": False}, {"role": "employee"}, {"full_name": "x"}):
            assert (
                raw_client.patch(f"{USERS}/{boss.id}", headers=headers, json=change).status_code
                == 403
            )
