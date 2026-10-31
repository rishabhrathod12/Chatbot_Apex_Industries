"""Admin dashboard numbers, the audit log viewer and the split audit actions (phase 4)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.permissions import Role
from app.models import AuditLog, User
from app.services import audit
from tests.conftest import add_stored_document, add_user, login

STATS = "/api/v1/admin/stats"
AUDIT = "/api/v1/admin/audit"
USERS = "/api/v1/users"
PDF = b"%PDF-1.4 test"


@pytest.fixture
def world(session: Session, settings: Settings) -> dict[str, User]:
    people = {
        "boss": add_user(session, settings, "boss@apex.test", Role.SUPER_ADMIN),
        "mgr": add_user(session, settings, "mgr@apex.test", Role.DEPARTMENT_MANAGER, ["hr"]),
        "emp": add_user(session, settings, "emp@apex.test", Role.EMPLOYEE, ["production"]),
        "emp2": add_user(session, settings, "emp2@apex.test", Role.EMPLOYEE, ["production"]),
        "gone": add_user(session, settings, "gone@apex.test", Role.EMPLOYEE, ["stores"]),
    }
    people["gone"].is_active = False
    session.commit()
    return people


def _admin(client: TestClient) -> dict[str, str]:
    return login(client, "boss@apex.test")


# ---- who may look ----


@pytest.mark.parametrize("path", [STATS, AUDIT])
def test_admin_pages_need_a_super_admin(
    raw_client: TestClient, world: dict[str, User], path: str
) -> None:
    assert raw_client.get(path).status_code == 401
    for who in ("mgr@apex.test", "emp@apex.test"):
        denied = raw_client.get(path, headers=login(raw_client, who))
        assert (denied.status_code, denied.json()["detail"]) == (403, "not_allowed")
    assert raw_client.get(path, headers=_admin(raw_client)).status_code == 200


# ---- stats ----


def test_stats_count_accounts_and_departments(
    raw_client: TestClient, world: dict[str, User]
) -> None:
    stats = raw_client.get(STATS, headers=_admin(raw_client)).json()
    assert stats["accounts"] == {
        "total": 5,
        "active": 4,
        "inactive": 1,
        "by_role": {"super_admin": 1, "department_manager": 1, "employee": 3},
    }
    by_code = {d["code"]: d for d in stats["departments"]}
    assert list(by_code) == ["production", "maintenance", "hr", "safety", "quality", "stores"]
    assert (by_code["production"]["employees"], by_code["production"]["managers"]) == (2, 0)
    assert (by_code["hr"]["employees"], by_code["hr"]["managers"]) == (0, 1)
    assert by_code["stores"]["employees"] == 0  # the only stores employee is disabled
    assert by_code["quality"] == {"code": "quality", "employees": 0, "managers": 0, "documents": 0}


def test_stats_count_only_company_documents_and_never_name_personal_ones(
    raw_client: TestClient, world: dict[str, User], session: Session, settings: Settings
) -> None:
    boss, emp = world["boss"], world["emp"]
    add_stored_document(
        session, settings, PDF, name="m1", department="production", owner_id=boss.id
    )
    add_stored_document(
        session, settings, PDF, name="m2", department="production", owner_id=boss.id
    )
    add_stored_document(session, settings, PDF, name="h1", department="hr", owner_id=boss.id)
    secret = add_stored_document(
        session,
        settings,
        PDF,
        name="my-private-notes",
        scope="personal",
        department="",
        owner_id=emp.id,
    )
    deleted = add_stored_document(
        session, settings, PDF, name="old", department="hr", owner_id=boss.id
    )
    deleted.deleted_at = datetime.now(UTC)
    session.commit()

    response = raw_client.get(STATS, headers=_admin(raw_client))
    stats = response.json()
    assert stats["documents"]["total"] == 3
    per_department = {d["code"]: d["documents"] for d in stats["departments"]}
    assert (per_department["production"], per_department["hr"]) == (2, 1)
    uploads = stats["recent_uploads"]
    assert len(uploads) == 3 and {u["uploaded_by"] for u in uploads} == {boss.full_name}
    assert "my-private-notes" not in response.text and secret.id not in response.text


def test_recent_activity_shows_admin_events_but_not_personal_document_events(
    raw_client: TestClient, world: dict[str, User], session: Session, settings: Settings
) -> None:
    boss, emp = world["boss"], world["emp"]
    company = add_stored_document(session, settings, PDF, name="manual", owner_id=boss.id)
    personal = add_stored_document(
        session, settings, PDF, name="diary", scope="personal", department="", owner_id=emp.id
    )
    for doc, who in ((company, boss), (personal, emp)):
        audit.record(
            session,
            "document_upload",
            "ok",
            user_id=who.id,
            target_type="document",
            target_id=doc.id,
        )
    audit.record(session, "user_create", "failed", user_id=boss.id)  # failures are not "activity"

    stats = raw_client.get(STATS, headers=_admin(raw_client)).json()
    uploads = [a for a in stats["recent_activity"] if a["action"] == "document_upload"]
    assert [a["target_id"] for a in uploads] == [company.id]
    assert uploads[0]["target_label"] == "manual.pdf"
    assert uploads[0]["actor_name"] == boss.full_name
    assert not any(a["outcome"] != "ok" for a in stats["recent_activity"])
    assert "diary" not in str(stats)


# ---- the audit log ----


def _seed_audit(session: Session, boss: User, emp: User) -> None:
    for i in range(30):
        audit.record(
            session,
            "login",
            "ok" if i % 3 else "failed",
            user_id=(boss if i % 2 else emp).id,
            detail=None,
        )
    audit.record(
        session, "password_reset", "ok", user_id=boss.id, target_type="user", target_id=emp.id
    )


def test_the_audit_log_is_paged_newest_first(
    raw_client: TestClient, world: dict[str, User], session: Session
) -> None:
    before = session.scalars(select(AuditLog)).all()
    _seed_audit(session, world["boss"], world["emp"])
    headers = _admin(raw_client)
    total = len(before) + 31 + 0  # the admin's own sign-in below is added by login()
    first = raw_client.get(AUDIT, headers=headers, params={"page_size": 10}).json()
    assert first["page"] == 1 and first["page_size"] == 10 and len(first["items"]) == 10
    assert first["total"] >= total
    ids = [e["id"] for e in first["items"]]
    assert ids == sorted(ids, reverse=True)
    second = raw_client.get(AUDIT, headers=headers, params={"page_size": 10, "page": 2}).json()
    assert not set(ids) & {e["id"] for e in second["items"]}
    assert max(e["id"] for e in second["items"]) < min(ids)
    last = raw_client.get(AUDIT, headers=headers, params={"page_size": 10, "page": 999}).json()
    assert last["items"] == [] and last["total"] == first["total"]


def test_the_audit_log_can_be_filtered(
    raw_client: TestClient, world: dict[str, User], session: Session
) -> None:
    boss, emp = world["boss"], world["emp"]
    _seed_audit(session, boss, emp)
    headers = _admin(raw_client)

    def fetch(**params: Any) -> dict[str, Any]:
        return raw_client.get(AUDIT, headers=headers, params={"page_size": 100, **params}).json()

    resets = fetch(action="password_reset")
    assert resets["total"] == 1
    entry = resets["items"][0]
    assert (entry["actor_name"], entry["actor_login_id"]) == (boss.full_name, boss.login_id)
    assert (entry["target_type"], entry["target_label"]) == ("user", emp.full_name)

    failed = fetch(action="login", outcome="failed")
    assert failed["total"] == 10 and all(e["outcome"] == "failed" for e in failed["items"])
    mine = fetch(user_id=emp.id)
    assert mine["total"] > 0 and all(e["actor_id"] == emp.id for e in mine["items"])

    now = datetime.now(UTC)
    assert fetch(date_from=(now + timedelta(days=1)).isoformat())["total"] == 0
    assert fetch(date_to=(now - timedelta(days=1)).isoformat())["total"] == 0
    assert fetch(date_from=(now - timedelta(days=1)).isoformat())["total"] > 0
    assert "password_reset" in fetch()["available_actions"]


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page_size": 0},
        {"page_size": 101},
        {"action": "Bad Action!"},
        {"action": "login'; DROP TABLE audit_log; --"},
        {"outcome": "maybe"},
        {"user_id": "not-hex"},
        {"date_from": "yesterday"},
    ],
)
def test_bad_audit_filters_are_rejected_before_they_reach_the_database(
    raw_client: TestClient, world: dict[str, User], params: dict[str, Any]
) -> None:
    assert raw_client.get(AUDIT, headers=_admin(raw_client), params=params).status_code == 422


def test_audit_entries_never_show_personal_document_names_or_secrets(
    raw_client: TestClient, world: dict[str, User], session: Session, settings: Settings
) -> None:
    emp = world["emp"]
    personal = add_stored_document(
        session,
        settings,
        PDF,
        name="salary-negotiation",
        scope="personal",
        department="",
        owner_id=emp.id,
    )
    audit.record(
        session,
        "document_delete",
        "ok",
        user_id=emp.id,
        target_type="document",
        target_id=personal.id,
    )
    headers = _admin(raw_client)
    temporary = raw_client.post(
        USERS,
        headers=headers,
        json={"email": "n@apex.test", "full_name": "New N", "role": "employee", "departments": []},
    ).json()["temporary_password"]

    page = raw_client.get(AUDIT, headers=headers, params={"page_size": 100})
    assert "salary-negotiation" not in page.text
    deleted = next(e for e in page.json()["items"] if e["action"] == "document_delete")
    assert deleted["target_label"] == "a personal document"
    assert temporary not in page.text


# ---- split audit actions ----


def _actions(session: Session, target: User) -> list[tuple[str, str | None]]:
    session.expire_all()
    rows = session.scalars(
        select(AuditLog).where(AuditLog.target_id == target.id).order_by(AuditLog.id)
    ).all()
    return [(r.action, r.detail) for r in rows if r.action != "user_create"]


def test_each_kind_of_account_change_has_its_own_audit_action(
    raw_client: TestClient, world: dict[str, User], session: Session
) -> None:
    headers, emp = _admin(raw_client), world["emp"]

    def patch(**body: Any) -> None:
        assert raw_client.patch(f"{USERS}/{emp.id}", headers=headers, json=body).status_code == 200

    patch(is_active=False)
    patch(is_active=True)
    patch(role="department_manager", departments=["production"])
    patch(departments=["production", "safety"])
    patch(full_name="Renamed Person")
    assert _actions(session, emp) == [
        ("user_deactivate", None),
        ("user_activate", None),
        ("user_role_change", "role:department_manager"),
        ("user_departments_change", "production,safety"),
        ("user_update", None),
    ]


def test_one_request_with_several_changes_records_each_and_a_no_op_records_nothing(
    raw_client: TestClient, world: dict[str, User], session: Session
) -> None:
    headers, emp = _admin(raw_client), world["emp"]
    raw_client.patch(
        f"{USERS}/{emp.id}",
        headers=headers,
        json={"is_active": False, "role": "department_manager", "departments": ["quality"]},
    )
    assert [a for a, _ in _actions(session, emp)] == [
        "user_deactivate",
        "user_role_change",
        "user_departments_change",
    ]
    before = len(_actions(session, emp))
    raw_client.patch(
        f"{USERS}/{emp.id}",
        headers=headers,
        json={"is_active": False, "role": "department_manager", "departments": ["quality"]},
    )
    assert len(_actions(session, emp)) == before  # nothing changed, nothing to record


def test_promoting_someone_to_super_admin_is_visible_in_the_log(
    raw_client: TestClient, world: dict[str, User], session: Session
) -> None:
    headers, emp = _admin(raw_client), world["emp"]
    raw_client.patch(f"{USERS}/{emp.id}", headers=headers, json={"role": "super_admin"})
    page = raw_client.get(AUDIT, headers=headers, params={"action": "user_role_change"}).json()
    assert [(e["detail"], e["target_label"]) for e in page["items"]] == [
        ("role:super_admin", emp.full_name)
    ]
