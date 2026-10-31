"""Company document details, archiving and restoring (auth redesign, phase 5)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.permissions import Role
from app.models import AuditLog, Document
from app.rag.ingestion.embedder import HashingEmbedder
from app.rag.ingestion.pipeline import Scope, run_ingestion
from app.rag.vectorstore import QdrantVectorStore, collection_name
from tests.conftest import SAMPLE_DOCS, add_user, login

DOCS = "/api/v1/documents"
SEARCH = "/api/v1/search"
STATS = "/api/v1/admin/stats"
SAFETY = (SAMPLE_DOCS / "03_Factory_Safety_Manual.pdf").read_bytes()
HR = (SAMPLE_DOCS / "05_HR_Policy.pdf").read_bytes()


@pytest.fixture
def people(
    raw_client: TestClient, session: Session, settings: Settings
) -> dict[str, dict[str, str]]:
    spec = {
        "admin": (Role.SUPER_ADMIN, []),
        "safety_mgr": (Role.DEPARTMENT_MANAGER, ["safety"]),
        "hr_mgr": (Role.DEPARTMENT_MANAGER, ["hr"]),
        "safety_emp": (Role.EMPLOYEE, ["safety"]),
        "prod_emp": (Role.EMPLOYEE, ["production"]),
    }
    headers = {}
    for name, (role, departments) in spec.items():
        add_user(session, settings, f"{name}@apex.test", role, departments)
        headers[name] = login(raw_client, f"{name}@apex.test")
    return headers


def _upload(
    client: TestClient,
    headers: dict[str, str],
    content: bytes = SAFETY,
    *,
    name: str = "safety.pdf",
    department: str = "safety",
    **metadata: str,
) -> Any:
    return client.post(
        DOCS,
        headers=headers,
        data={"scope": "enterprise", "department": department, **metadata},
        files=[("files", (name, content))],
    )


def _ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 201, response.text
    return response.json()["results"][0]["document"]  # type: ignore[no-any-return]


def _get(client: TestClient, headers: dict[str, str], doc_id: str) -> Any:
    return client.get(f"{DOCS}/{doc_id}", headers=headers)


def _hits(client: TestClient, headers: dict[str, str], query: str = "emergency stop") -> list[Any]:
    response = client.post(SEARCH, headers=headers, json={"query": query, "top_k": 5})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


# ---- details typed at upload ----


def test_the_details_typed_at_upload_reach_the_document_and_the_citations(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    admin = people["admin"]
    uploaded = _ok(
        _upload(
            raw_client,
            admin,
            title="  Site Safety Rules  ",
            description="Read before entering the shop floor.",
            document_type="SOP",
            version="2.1",
        )
    )
    assert uploaded["description"] == "Read before entering the shop floor."
    assert uploaded["document_type"] == "sop"

    doc = _get(raw_client, admin, uploaded["id"]).json()  # ingestion has run by now
    assert doc["status"] == "ready"
    assert (doc["title"], doc["doc_version"]) == ("Site Safety Rules", "2.1")
    assert doc["doc_code"] == "APM-HSE-MAN-003"  # still read from the file

    top = _hits(raw_client, admin)[0]
    assert (top["doc_title"], top["doc_code"]) == ("Site Safety Rules", "APM-HSE-MAN-003")


def test_without_details_the_title_and_version_still_come_from_the_file(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    doc = _get(raw_client, people["admin"], _ok(_upload(raw_client, people["admin"]))["id"]).json()
    assert doc["title"] == "Factory Health & Safety Manual"
    assert doc["description"] is None and doc["document_type"] is None


def test_indexing_again_never_overwrites_what_the_admin_typed(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    admin = people["admin"]
    uploaded = _ok(_upload(raw_client, admin, title="My Title", version="7"))
    assert raw_client.post(f"{DOCS}/{uploaded['id']}/reindex", headers=admin).status_code == 202
    doc = _get(raw_client, admin, uploaded["id"]).json()
    assert (doc["status"], doc["title"], doc["doc_version"]) == ("ready", "My Title", "7")


@pytest.mark.parametrize(
    ("metadata", "detail"),
    [
        ({"document_type": "poster"}, "invalid_document_type"),
        ({"version": "v 2!"}, "invalid_version"),
        ({"version": "x" * 33}, "invalid_version"),
        ({"title": "t" * 256}, "metadata_too_long"),
        ({"description": "d" * 2001}, "metadata_too_long"),
    ],
)
def test_bad_details_are_refused_and_nothing_is_stored(
    raw_client: TestClient,
    people: dict[str, dict[str, str]],
    session: Session,
    metadata: dict[str, str],
    detail: str,
) -> None:
    refused = _upload(raw_client, people["admin"], **metadata)
    assert (refused.status_code, refused.json()["detail"]) == (422, detail)
    assert session.scalars(select(Document)).first() is None


def test_blank_details_count_as_not_given(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    uploaded = _ok(_upload(raw_client, people["admin"], title="  ", document_type="", version=""))
    assert uploaded["document_type"] is None


def test_details_are_only_for_company_documents_and_a_title_needs_one_file(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    personal = raw_client.post(
        DOCS,
        headers=people["prod_emp"],
        data={"scope": "personal", "title": "Mine"},
        files=[("files", ("a.pdf", HR))],
    )
    assert personal.json()["detail"] == "metadata_for_company_documents_only"
    two = raw_client.post(
        DOCS,
        headers=people["admin"],
        data={"scope": "enterprise", "department": "hr", "title": "Both"},
        files=[("files", ("a.pdf", HR)), ("files", ("b.pdf", SAFETY))],
    )
    assert (two.status_code, two.json()["detail"]) == (422, "title_and_version_need_a_single_file")


# ---- archiving ----


def test_archiving_takes_a_document_out_of_search_and_out_of_sight(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    admin = people["admin"]
    doc_id = _ok(_upload(raw_client, admin, title="Old Rules"))["id"]
    assert _hits(raw_client, people["safety_emp"])  # employees can search it now

    archived = raw_client.post(f"{DOCS}/{doc_id}/archive", headers=admin)
    assert archived.status_code == 200
    body = archived.json()
    assert (body["status"], body["chunk_count"], body["title"]) == ("archived", 0, "Old Rules")
    assert body["archived_at"] is not None

    # nobody can find or cite it any more, whoever asks
    for who in ("admin", "safety_mgr", "safety_emp"):
        assert _hits(raw_client, people[who]) == [], who
    # the people it is for no longer see it; those who look after it still do
    assert doc_id not in [
        d["id"] for d in raw_client.get(DOCS, headers=people["safety_emp"]).json()
    ]
    assert _get(raw_client, people["safety_emp"], doc_id).status_code == 404
    for who in ("admin", "safety_mgr"):
        assert _get(raw_client, people[who], doc_id).json()["status"] == "archived"
        assert doc_id in [d["id"] for d in raw_client.get(DOCS, headers=people[who]).json()]
    # a manager of another department does not
    assert _get(raw_client, people["hr_mgr"], doc_id).status_code == 404


def test_only_the_people_who_look_after_a_document_can_archive_it(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    doc_id = _ok(_upload(raw_client, people["admin"]))["id"]
    # an employee of the department can read it but not archive it
    denied = raw_client.post(f"{DOCS}/{doc_id}/archive", headers=people["safety_emp"])
    assert (denied.status_code, denied.json()["detail"]) == (403, "not_allowed")
    # someone from another department cannot even tell it exists
    assert (
        raw_client.post(f"{DOCS}/{doc_id}/archive", headers=people["prod_emp"]).status_code == 404
    )
    assert raw_client.post(f"{DOCS}/{doc_id}/archive", headers=people["hr_mgr"]).status_code == 404
    assert _get(raw_client, people["admin"], doc_id).json()["status"] == "ready"
    # the manager of that department can
    assert (
        raw_client.post(f"{DOCS}/{doc_id}/archive", headers=people["safety_mgr"]).status_code == 200
    )


def test_personal_documents_cannot_be_archived(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    mine = raw_client.post(
        DOCS,
        headers=people["prod_emp"],
        data={"scope": "personal"},
        files=[("files", ("mine.pdf", HR))],
    ).json()["results"][0]["document"]["id"]
    refused = raw_client.post(f"{DOCS}/{mine}/archive", headers=people["prod_emp"])
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        "only_company_documents_can_be_archived",
    )


def test_archiving_twice_is_harmless_and_a_busy_document_waits(
    raw_client: TestClient, people: dict[str, dict[str, str]], session: Session
) -> None:
    admin = people["admin"]
    doc_id = _ok(_upload(raw_client, admin))["id"]
    assert raw_client.post(f"{DOCS}/{doc_id}/archive", headers=admin).status_code == 200
    assert raw_client.post(f"{DOCS}/{doc_id}/archive", headers=admin).status_code == 200

    other = _ok(_upload(raw_client, admin, HR, name="hr.pdf", department="hr"))["id"]
    document = session.get(Document, other)
    assert document is not None
    document.status = "processing"
    session.commit()
    busy = raw_client.post(f"{DOCS}/{other}/archive", headers=admin)
    assert (busy.status_code, busy.json()["detail"]) == (409, "still_processing")


def test_an_archived_document_cannot_be_indexed_again_by_accident(
    raw_client: TestClient,
    people: dict[str, dict[str, str]],
    settings: Settings,
) -> None:
    admin = people["admin"]
    doc_id = _ok(_upload(raw_client, admin))["id"]
    raw_client.post(f"{DOCS}/{doc_id}/archive", headers=admin)

    refused = raw_client.post(f"{DOCS}/{doc_id}/reindex", headers=admin)
    assert (refused.status_code, refused.json()["detail"]) == (409, "archived_restore_it_first")

    # a task that was already queued when it was archived must not bring it back either
    def store(scope: Scope, dimensions: int) -> QdrantVectorStore:
        return QdrantVectorStore(settings.vector_store_path, collection_name(scope), dimensions)

    run_ingestion(doc_id, settings, HashingEmbedder, store)
    assert _get(raw_client, admin, doc_id).json()["status"] == "archived"
    assert _hits(raw_client, admin) == []


# ---- restoring ----


def test_restoring_makes_the_document_searchable_again_with_its_details(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    admin = people["admin"]
    doc_id = _ok(_upload(raw_client, admin, title="Back Again", version="3"))["id"]
    raw_client.post(f"{DOCS}/{doc_id}/archive", headers=admin)

    restored = raw_client.post(f"{DOCS}/{doc_id}/restore", headers=admin)
    assert restored.status_code == 202
    doc = _get(raw_client, admin, doc_id).json()  # ingestion has run by now
    assert (doc["status"], doc["title"], doc["doc_version"]) == ("ready", "Back Again", "3")
    assert doc["archived_at"] is None and doc["chunk_count"] > 0
    assert _hits(raw_client, people["safety_emp"])[0]["doc_title"] == "Back Again"
    assert _get(raw_client, people["safety_emp"], doc_id).status_code == 200


def test_only_archived_documents_can_be_restored_and_only_by_their_managers(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    admin = people["admin"]
    doc_id = _ok(_upload(raw_client, admin))["id"]
    not_archived = raw_client.post(f"{DOCS}/{doc_id}/restore", headers=admin)
    assert (not_archived.status_code, not_archived.json()["detail"]) == (409, "not_archived")

    raw_client.post(f"{DOCS}/{doc_id}/archive", headers=admin)
    for who in ("safety_emp", "prod_emp", "hr_mgr"):
        # they cannot see an archived document, so it looks like it does not exist
        assert raw_client.post(f"{DOCS}/{doc_id}/restore", headers=people[who]).status_code == 404
    assert _get(raw_client, admin, doc_id).json()["status"] == "archived"
    assert (
        raw_client.post(f"{DOCS}/{doc_id}/restore", headers=people["safety_mgr"]).status_code == 202
    )


def test_an_archived_document_can_be_deleted(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    admin = people["admin"]
    doc_id = _ok(_upload(raw_client, admin))["id"]
    raw_client.post(f"{DOCS}/{doc_id}/archive", headers=admin)
    assert raw_client.delete(f"{DOCS}/{doc_id}", headers=admin).status_code == 204
    assert _get(raw_client, admin, doc_id).status_code == 404


# ---- dashboard and audit ----


def test_archived_documents_leave_the_dashboard_counts_and_are_audited(
    raw_client: TestClient, people: dict[str, dict[str, str]], session: Session
) -> None:
    admin = people["admin"]
    keep = _ok(_upload(raw_client, admin, HR, name="hr.pdf", department="hr"))["id"]
    drop = _ok(_upload(raw_client, admin))["id"]
    before = raw_client.get(STATS, headers=admin).json()
    assert before["documents"]["total"] == 2 and before["documents"]["archived"] == 0

    raw_client.post(f"{DOCS}/{drop}/archive", headers=admin)
    stats = raw_client.get(STATS, headers=admin).json()
    assert stats["documents"] == {"total": 1, "archived": 1, "by_status": {"ready": 1}}
    per_department = {d["code"]: d["documents"] for d in stats["departments"]}
    assert (per_department["hr"], per_department["safety"]) == (1, 0)
    assert [u["document_id"] for u in stats["recent_uploads"]] == [keep]

    raw_client.post(f"{DOCS}/{drop}/restore", headers=admin)
    session.expire_all()
    actions = [
        (a.action, a.outcome)
        for a in session.scalars(
            select(AuditLog).where(AuditLog.action.in_(["document_archive", "document_restore"]))
        )
    ]
    assert actions == [("document_archive", "ok"), ("document_restore", "ok")]


def test_denied_archive_attempts_are_audited(
    raw_client: TestClient, people: dict[str, dict[str, str]], session: Session
) -> None:
    doc_id = _ok(_upload(raw_client, people["admin"]))["id"]
    raw_client.post(f"{DOCS}/{doc_id}/archive", headers=people["safety_emp"])
    session.expire_all()
    denied = session.scalars(
        select(AuditLog).where(AuditLog.action == "document_archive", AuditLog.outcome == "denied")
    ).all()
    assert len(denied) == 1 and denied[0].target_id == doc_id
