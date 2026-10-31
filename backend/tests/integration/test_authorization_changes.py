"""The final authorization sweep, part 2: changes that must take effect at once (phase 9).

Each test changes the company (disables an account, demotes an admin, adds a document), so each
gets a fresh one. The read-only attacks are in test_authorization_sweep.py.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import User
from tests.arena import (
    API,
    FILES,
    PENDING_OK,
    SIGNED_IN,
    Arena,
    admin_routes,
    all_routes,
    call,
    make_arena,
)
from tests.conftest import PASSWORD, SAMPLE_DOCS


@pytest.fixture
def arena(raw_client: TestClient, session: Session, settings: Settings) -> Arena:
    return make_arena(raw_client, settings, session)


# ===============================================================================================
# 1. Accounts that must not get through, on every route
# ===============================================================================================


def test_someone_with_a_temporary_password_can_reach_nothing_but_the_way_out(
    arena: Arena, session: Session
) -> None:
    """Every signed-in route answers 403 password_change_required until the password is changed."""
    session.get(User, arena.users["alice"].id).must_change_password = True  # type: ignore[union-attr]
    session.commit()
    headers = arena.headers["alice"]
    for route in sorted(SIGNED_IN | admin_routes(all_routes(arena.client))):
        response = call(arena.client, route, headers)
        assert (response.status_code, response.json().get("detail")) == (
            403,
            "password_change_required",
        ), route
    for route in sorted(PENDING_OK):
        assert call(arena.client, route, headers).status_code not in (401, 403), route


def test_a_disabled_account_is_refused_everywhere_at_once(arena: Arena, session: Session) -> None:
    """Even with a perfectly valid, unexpired token issued before the account was disabled."""
    session.get(User, arena.users["alice"].id).is_active = False  # type: ignore[union-attr]
    session.commit()
    headers = arena.headers["alice"]
    for route in sorted(SIGNED_IN | PENDING_OK | admin_routes(all_routes(arena.client))):
        assert call(arena.client, route, headers).status_code == 401, route


# ===============================================================================================
# 2. Who may add company documents, for every department
# ===============================================================================================

DEPARTMENTS = ["production", "maintenance", "hr", "safety", "quality", "stores"]


def _company_upload(arena: Arena, who: str, department: str, visibility: str = "department") -> int:
    content = (SAMPLE_DOCS / FILES["alice_private"]).read_bytes() + department.encode()
    return arena.client.post(
        f"{API}/documents",
        headers=arena.headers[who],
        data={"scope": "enterprise", "department": department, "visibility": visibility},
        files=[("files", (f"{department}.pdf", content))],
    ).status_code


@pytest.mark.parametrize("visibility", ["department", "company"])
@pytest.mark.parametrize("department", DEPARTMENTS)
def test_employees_can_add_company_documents_to_no_department(
    arena: Arena, department: str, visibility: str
) -> None:
    assert _company_upload(arena, "alice", department, visibility) == 403


@pytest.mark.parametrize("department", DEPARTMENTS)
def test_a_manager_can_add_only_to_their_own_department(arena: Arena, department: str) -> None:
    expected = 201 if department == "production" else 403
    assert _company_upload(arena, "mgr_prod", department) == expected


# ===============================================================================================
# 3. Changes take effect on the very next request
# ===============================================================================================


def test_demoting_an_admin_ends_their_admin_powers_at_once(arena: Arena) -> None:
    old_token = arena.headers["admin"]
    assert arena.client.get(f"{API}/admin/stats", headers=old_token).status_code == 200
    demote = arena.client.patch(
        f"{API}/users/{arena.users['admin'].id}",
        headers=arena.headers["admin2"],
        json={"role": "employee"},
    )
    assert demote.status_code == 200
    for route in ("/admin/stats", "/admin/audit", "/users"):
        assert arena.client.get(API + route, headers=old_token).status_code == 403, route


def test_promoting_an_employee_gives_the_new_powers_at_once(arena: Arena) -> None:
    assert _company_upload(arena, "carol", "hr") == 403
    promote = arena.client.patch(
        f"{API}/users/{arena.users['carol'].id}",
        headers=arena.headers["admin"],
        json={"role": "department_manager"},
    )
    assert promote.status_code == 200
    assert _company_upload(arena, "carol", "hr") == 201  # same token as before the promotion
    assert _company_upload(arena, "carol", "quality") == 403


def test_disabling_an_account_stops_its_access_and_its_sessions(arena: Arena) -> None:
    pair = arena.client.post(
        f"{API}/auth/login", json={"login_id": "bob", "password": PASSWORD}
    ).json()
    arena.client.patch(
        f"{API}/users/{arena.users['bob'].id}",
        headers=arena.headers["admin"],
        json={"is_active": False},
    )
    old = {"Authorization": f"Bearer {pair['access_token']}"}
    assert arena.client.get(f"{API}/documents", headers=old).status_code == 401
    refresh = arena.client.post(
        f"{API}/auth/refresh", json={"refresh_token": pair["refresh_token"]}
    )
    assert refresh.status_code == 401
    again = arena.client.post(f"{API}/auth/login", json={"login_id": "bob", "password": PASSWORD})
    assert again.status_code == 401


# ===============================================================================================
# 4. Fields a client should never be able to set
# ===============================================================================================


def test_extra_fields_cannot_change_what_an_update_does_not_allow(
    arena: Arena, session: Session
) -> None:
    carol = arena.users["carol"]
    before = (carol.login_id, carol.password_hash, carol.created_by, carol.email)
    response = arena.client.patch(
        f"{API}/users/{carol.id}",
        headers=arena.headers["admin"],
        json={
            "full_name": "Carol Renamed",
            "login_id": "hijacked",
            "password_hash": "scrypt$x",
            "email": "evil@apex.test",
            "created_by": "someone",
            "must_change_password": True,
            "id": "f" * 32,
        },
    )
    assert response.status_code == 200
    session.expire_all()
    reloaded = session.get(User, carol.id)
    assert reloaded is not None
    assert reloaded.full_name == "Carol Renamed"
    assert (
        reloaded.login_id,
        reloaded.password_hash,
        reloaded.created_by,
        reloaded.email,
    ) == before
    assert reloaded.must_change_password is False and reloaded.id == carol.id


def test_an_admin_cannot_create_an_account_that_skips_the_forced_password_change(
    arena: Arena,
) -> None:
    response = arena.client.post(
        f"{API}/users",
        headers=arena.headers["admin"],
        json={
            "email": "new@apex.test",
            "full_name": "New Person",
            "role": "employee",
            "departments": [],
            "must_change_password": False,
            "created_by": "forged",
            "password_hash": "scrypt$x",
        },
    )
    assert response.status_code == 201
    assert response.json()["must_change_password"] is True


# ===============================================================================================
# 5. The login limiter cannot be dodged by lying about where the request came from
# ===============================================================================================


def test_spoofed_forwarding_headers_do_not_reset_the_lockout(
    arena: Arena, settings: Settings
) -> None:
    for i in range(settings.login_max_failures):
        arena.client.post(
            f"{API}/auth/login",
            json={"login_id": "carol", "password": "wrong-password-1"},
            headers={"X-Forwarded-For": f"10.0.0.{i}", "X-Real-IP": f"10.1.0.{i}"},
        )
    blocked = arena.client.post(
        f"{API}/auth/login",
        json={"login_id": "carol", "password": PASSWORD},
        headers={"X-Forwarded-For": "203.0.113.77", "Forwarded": "for=198.51.100.9"},
    )
    assert blocked.status_code == 429
