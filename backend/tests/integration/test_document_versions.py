"""Uploading a new version of a company document (auth redesign, phase 6)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.permissions import Role
from app.models import AuditLog, Document
from app.rag.ingestion import pipeline
from app.rag.ingestion.extractors import ExtractionError
from tests.conftest import SAMPLE_DOCS, add_user, login

DOCS = "/api/v1/documents"
SEARCH = "/api/v1/search"
SAFETY = (SAMPLE_DOCS / "03_Factory_Safety_Manual.pdf").read_bytes()
HR = (SAMPLE_DOCS / "05_HR_Policy.pdf").read_bytes()
MAINTENANCE = (SAMPLE_DOCS / "02_Maintenance_Manual.pdf").read_bytes()


@pytest.fixture
def people(
    raw_client: TestClient, session: Session, settings: Settings
) -> dict[str, dict[str, str]]:
    spec = {
        "admin": (Role.SUPER_ADMIN, []),
        "safety_mgr": (Role.DEPARTMENT_MANAGER, ["safety"]),
        "hr_mgr": (Role.DEPARTMENT_MANAGER, ["hr"]),
        "safety_emp": (Role.EMPLOYEE, ["safety"]),
    }
    headers = {}
    for name, (role, departments) in spec.items():
        add_user(session, settings, f"{name}@apex.test", role, departments)
        headers[name] = login(raw_client, f"{name}@apex.test")
    return headers


def _upload(
    client: TestClient, headers: dict[str, str], content: bytes = SAFETY, **metadata: str
) -> dict[str, Any]:
    response = client.post(
        DOCS,
        headers=headers,
        data={"scope": "enterprise", "department": "safety", **metadata},
        files=[("files", ("manual.pdf", content))],
    )
    assert response.status_code == 201, response.text
    return response.json()["results"][0]["document"]  # type: ignore[no-any-return]


def _new_version(
    client: TestClient,
    headers: dict[str, str],
    doc_id: str,
    content: bytes = HR,
    name: str = "manual-v2.pdf",
    **metadata: str,
) -> Any:
    return client.post(
        f"{DOCS}/{doc_id}/versions", headers=headers, data=metadata, files={"file": (name, content)}
    )


def _get(client: TestClient, headers: dict[str, str], doc_id: str) -> dict[str, Any]:
    response = client.get(f"{DOCS}/{doc_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def _found(client: TestClient, headers: dict[str, str], query: str) -> list[str]:
    hits = client.post(SEARCH, headers=headers, json={"query": query, "top_k": 5}).json()
    return sorted({h["document_id"] for h in hits})


# ---- the swap ----


def test_a_new_version_replaces_the_old_one_once_it_is_ready(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    admin = people["admin"]
    old = _upload(raw_client, admin, title="Safety Rules", document_type="sop", version="1")
    assert _found(raw_client, admin, "emergency stop") == [old["id"]]

    response = _new_version(raw_client, admin, old["id"], version="2")
    assert response.status_code == 202
    new = response.json()
    assert new["id"] != old["id"] and new["supersedes_id"] == old["id"]
    assert new["status"] == "uploaded"  # the answer comes before indexing

    # by now indexing has run: the new version is live and the old one is out of use
    now = _get(raw_client, admin, new["id"])
    assert (now["status"], now["doc_version"]) == ("ready", "2")
    assert (now["title"], now["document_type"]) == ("Safety Rules", "sop")  # inherited
    assert _get(raw_client, admin, old["id"])["status"] == "archived"
    assert _found(raw_client, admin, "leave request") == [new["id"]]
    assert _found(raw_client, admin, "emergency stop") != [old["id"]]
    # the people the document is for now see only the new one
    assert _get(raw_client, people["safety_emp"], new["id"])["status"] == "ready"
    assert raw_client.get(f"{DOCS}/{old['id']}", headers=people["safety_emp"]).status_code == 404


def test_the_new_version_keeps_the_department_and_visibility(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    admin = people["admin"]
    old = raw_client.post(
        DOCS,
        headers=admin,
        data={"scope": "enterprise", "department": "safety", "visibility": "company"},
        files=[("files", ("a.pdf", SAFETY))],
    ).json()["results"][0]["document"]
    new = _new_version(raw_client, admin, old["id"]).json()
    assert (new["department"], new["visibility"], new["scope"]) == (
        "safety",
        "company",
        "enterprise",
    )


def test_a_failed_new_version_leaves_the_old_one_exactly_as_it_was(
    raw_client: TestClient,
    people: dict[str, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    admin = people["admin"]
    old = _upload(raw_client, admin)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ExtractionError("corrupt_pdf")

    monkeypatch.setattr(pipeline, "extract", broken)
    new = _new_version(raw_client, admin, old["id"]).json()

    assert _get(raw_client, admin, new["id"])["status"] == "failed"
    assert _get(raw_client, admin, old["id"])["status"] == "ready"  # never took it away
    assert _found(raw_client, admin, "emergency stop") == [old["id"]]
    session.expire_all()
    rows = session.scalars(
        select(AuditLog).where(AuditLog.action == "document_version_replace")
    ).all()
    assert [(r.outcome, r.detail) for r in rows] == [("failed", "corrupt_pdf")]


def test_if_the_old_version_cannot_be_removed_both_stay_rather_than_lose_it(
    raw_client: TestClient,
    people: dict[str, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    admin = people["admin"]
    old = _upload(raw_client, admin)
    real = pipeline.replace_document

    def failing_store(scope: str, dimensions: int) -> Any:
        raise RuntimeError("store unavailable")

    def replace_with_broken_cleanup(
        new_id: str, old_id: str, actor: str, settings: Settings, embedder: Any, store: Any
    ) -> None:
        calls = {"n": 0}

        def store_that_fails_on_second_use(scope: str, dimensions: int) -> Any:
            calls["n"] += 1
            if calls["n"] > 1:  # the first use indexes the new version, the second removes the old
                return failing_store(scope, dimensions)
            return store(scope, dimensions)

        real(new_id, old_id, actor, settings, embedder, store_that_fails_on_second_use)

    monkeypatch.setattr("app.api.documents.replace_document", replace_with_broken_cleanup)
    new = _new_version(raw_client, admin, old["id"]).json()
    assert _get(raw_client, admin, new["id"])["status"] == "ready"
    assert _get(raw_client, admin, old["id"])["status"] == "ready"  # not archived
    session.expire_all()
    detail = session.scalars(
        select(AuditLog.detail).where(AuditLog.action == "document_version_replace")
    ).all()
    assert detail == ["old_version_kept"]


# ---- who may, and what is refused ----


def test_only_the_people_who_look_after_a_document_can_replace_it(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    doc_id = _upload(raw_client, people["admin"])["id"]
    denied = _new_version(raw_client, people["safety_emp"], doc_id)
    assert (denied.status_code, denied.json()["detail"]) == (403, "not_allowed")
    assert _new_version(raw_client, people["hr_mgr"], doc_id).status_code == 404
    unauthenticated = raw_client.post(f"{DOCS}/{doc_id}/versions", files={"file": ("a.pdf", HR)})
    assert unauthenticated.status_code == 401
    assert _get(raw_client, people["admin"], doc_id)["status"] == "ready"  # untouched
    assert _new_version(raw_client, people["safety_mgr"], doc_id).status_code == 202


def test_personal_documents_have_no_versions(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    mine = raw_client.post(
        DOCS,
        headers=people["safety_emp"],
        data={"scope": "personal"},
        files=[("files", ("mine.pdf", HR))],
    ).json()["results"][0]["document"]["id"]
    refused = _new_version(raw_client, people["safety_emp"], mine, content=SAFETY)
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        "only_company_documents_have_versions",
    )


@pytest.mark.parametrize(
    ("content", "metadata", "status", "detail"),
    [
        (SAFETY, {}, 409, "same_file"),
        (HR, {"version": "1"}, 422, "same_version"),
        (HR, {"version": "not valid!"}, 422, "invalid_version"),
        (HR, {"document_type": "poster"}, 422, "invalid_document_type"),
        (b"not a pdf at all", {}, 422, "content_mismatch"),
    ],
)
def test_bad_replacements_are_refused_and_change_nothing(
    raw_client: TestClient,
    people: dict[str, dict[str, str]],
    session: Session,
    content: bytes,
    metadata: dict[str, str],
    status: int,
    detail: str,
) -> None:
    admin = people["admin"]
    old = _upload(raw_client, admin, version="1")
    refused = _new_version(raw_client, admin, old["id"], content=content, **metadata)
    assert (refused.status_code, refused.json()["detail"]) == (status, detail)
    assert _get(raw_client, admin, old["id"])["status"] == "ready"
    assert len(session.scalars(select(Document)).all()) == 1


def test_a_file_that_is_already_another_document_is_not_added_twice(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    admin = people["admin"]
    first = _upload(raw_client, admin)
    _upload(raw_client, admin, HR)
    refused = _new_version(raw_client, admin, first["id"], content=HR)
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        "duplicate_of_another_document",
    )


def test_archived_and_busy_documents_cannot_be_replaced(
    raw_client: TestClient, people: dict[str, dict[str, str]], session: Session
) -> None:
    admin = people["admin"]
    doc_id = _upload(raw_client, admin)["id"]

    document = session.get(Document, doc_id)
    assert document is not None
    document.status = "processing"
    session.commit()
    busy = _new_version(raw_client, admin, doc_id)
    assert (busy.status_code, busy.json()["detail"]) == (409, "still_processing")

    document.status = "ready"
    session.commit()
    pending = Document(
        scope="enterprise", owner_id="x", department="safety", dedupe_key="k", file_hash="h",
        original_filename="n.pdf", stored_filename="n.pdf", file_ext=".pdf", mime_type="a",
        size_bytes=1, status="processing", supersedes_id=doc_id,
    )  # fmt: skip
    session.add(pending)
    session.commit()
    again = _new_version(raw_client, admin, doc_id)
    assert (again.status_code, again.json()["detail"]) == (409, "still_processing")

    session.delete(pending)
    session.commit()
    raw_client.post(f"{DOCS}/{doc_id}/archive", headers=admin)
    archived = _new_version(raw_client, admin, doc_id)
    assert (archived.status_code, archived.json()["detail"]) == (409, "archived_restore_it_first")


# ---- history ----


def test_version_history_lists_every_version_but_only_those_you_may_see(
    raw_client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    admin = people["admin"]
    v1 = _upload(raw_client, admin, version="1")
    v2 = _new_version(raw_client, admin, v1["id"], content=HR, version="2").json()
    v3 = _new_version(raw_client, admin, v2["id"], content=MAINTENANCE, version="3").json()

    for start in (v1, v2, v3):  # the same chain from any point, newest first
        chain = raw_client.get(f"{DOCS}/{start['id']}/versions", headers=admin).json()
        assert [d["id"] for d in chain] == [v3["id"], v2["id"], v1["id"]]
        assert [d["status"] for d in chain] == ["ready", "archived", "archived"]

    seen = raw_client.get(f"{DOCS}/{v3['id']}/versions", headers=people["safety_emp"]).json()
    assert [d["id"] for d in seen] == [v3["id"]]  # archived ones are not for employees
    manager = raw_client.get(f"{DOCS}/{v3['id']}/versions", headers=people["safety_mgr"]).json()
    assert len(manager) == 3
    assert (
        raw_client.get(f"{DOCS}/{v3['id']}/versions", headers=people["hr_mgr"]).status_code == 404
    )


def test_uploading_and_replacing_are_audited(
    raw_client: TestClient, people: dict[str, dict[str, str]], session: Session
) -> None:
    admin = people["admin"]
    old = _upload(raw_client, admin)
    new = _new_version(raw_client, admin, old["id"]).json()
    _new_version(raw_client, people["safety_emp"], new["id"])  # denied
    session.expire_all()
    rows = session.scalars(
        select(AuditLog).where(AuditLog.action.like("document_version_%")).order_by(AuditLog.id)
    ).all()
    assert [(r.action, r.outcome, r.detail) for r in rows] == [
        ("document_version_upload", "ok", f"replaces:{old['id']}"),
        ("document_version_replace", "ok", f"replaces:{old['id']}"),
        ("document_version_upload", "denied", None),
    ]
