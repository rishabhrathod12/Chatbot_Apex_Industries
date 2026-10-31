"""Saved conversations stop showing citations the person can no longer read (phase 6)."""

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.permissions import Role
from app.models import Document, User
from app.repositories import conversations as repo
from app.schemas.chat import Source
from tests.conftest import add_stored_document, add_user, login

DOCS = "/api/v1/documents"
CONVERSATIONS = "/api/v1/conversations"
PDF = b"%PDF-1.4 test"


@dataclass
class World:
    users: dict[str, User]
    docs: dict[str, Document]


def _source(number: int, doc: Document, title: str) -> Source:
    return Source(
        number=number,
        chunk_id=f"{doc.id}:1:{number}",
        doc_code=f"CODE-{number}",
        doc_title=title,
        source_name=f"{title}.pdf",
        section_title=f"Section of {title}",
        page_start=2,
        page_end=2,
        quote=f"A sentence from {title}.",
    )


def _save(session: Session, user: User, sources: list[Source]) -> str:
    conversation = repo.create(session, user.id, "What do the rules say?")
    repo.add_message(conversation, "user", "What do the rules say?")
    repo.add_message(
        conversation,
        "assistant",
        "It says this [1] and that [2].",
        status="answered",
        sources=sources,
    )
    session.commit()
    return conversation.id


@pytest.fixture
def world(session: Session, settings: Settings, raw_client: TestClient) -> World:
    users = {
        "admin": add_user(session, settings, "admin@apex.test", Role.SUPER_ADMIN),
        "mgr": add_user(session, settings, "mgr@apex.test", Role.DEPARTMENT_MANAGER, ["safety"]),
        "emp": add_user(session, settings, "emp@apex.test", Role.EMPLOYEE, ["safety"]),
        "other": add_user(session, settings, "other@apex.test", Role.EMPLOYEE, ["production"]),
    }
    admin_id = users["admin"].id

    def company(name: str, department: str) -> Document:
        return add_stored_document(
            session, settings, PDF, name=name, department=department, owner_id=admin_id
        )

    def personal(name: str, owner: User) -> Document:
        return add_stored_document(
            session, settings, PDF, name=name, scope="personal", department="", owner_id=owner.id
        )

    docs = {
        "safety": company("safety", "safety"),
        "hr": company("hr", "hr"),
        "mine": personal("mine", users["emp"]),
        "theirs": personal("theirs", users["other"]),
    }
    return World(users, docs)


def _messages(client: TestClient, email: str, conversation_id: str) -> list[dict[str, object]]:
    response = client.get(f"{CONVERSATIONS}/{conversation_id}", headers=login(client, email))
    assert response.status_code == 200, response.text
    return response.json()["messages"]  # type: ignore[no-any-return]


def _shown(client: TestClient, email: str, conversation_id: str) -> list[dict[str, object]]:
    return _messages(client, email, conversation_id)[1]["sources"]  # type: ignore[return-value]


def test_sources_the_person_can_still_read_are_shown_in_full(
    raw_client: TestClient, session: Session, world: World
) -> None:
    sources = [_source(1, world.docs["safety"], "Safety"), _source(2, world.docs["mine"], "Mine")]
    cid = _save(session, world.users["emp"], sources)
    shown = _shown(raw_client, "emp@apex.test", cid)
    assert [s["available"] for s in shown] == [True, True]
    assert shown[0]["doc_title"] == "Safety" and shown[0]["quote"] == "A sentence from Safety."


def test_a_source_from_a_document_they_cannot_read_is_blanked_but_keeps_its_number(
    raw_client: TestClient, session: Session, world: World
) -> None:
    sources = [
        _source(1, world.docs["safety"], "Safety"),
        _source(2, world.docs["hr"], "Confidential HR"),
    ]
    cid = _save(session, world.users["emp"], sources)
    response = raw_client.get(f"{CONVERSATIONS}/{cid}", headers=login(raw_client, "emp@apex.test"))
    shown = response.json()["messages"][1]["sources"]
    assert [(s["number"], s["available"]) for s in shown] == [(1, True), (2, False)]
    assert shown[1]["doc_title"] == shown[1]["quote"] == shown[1]["chunk_id"] == ""
    for leaked in ("Confidential HR", "CODE-2", "hr.pdf", world.docs["hr"].id):
        assert leaked not in response.text


def test_someone_elses_personal_document_is_never_shown(
    raw_client: TestClient, session: Session, world: World
) -> None:
    cid = _save(session, world.users["emp"], [_source(1, world.docs["theirs"], "Their diary")])
    shown = _shown(raw_client, "emp@apex.test", cid)
    assert shown[0]["available"] is False and shown[0]["doc_title"] == ""


def test_archiving_hides_old_citations_from_employees_but_not_from_the_managers(
    raw_client: TestClient, session: Session, world: World
) -> None:
    doc = world.docs["safety"]
    source = _source(1, doc, "Safety")
    emp_chat = _save(session, world.users["emp"], [source])
    mgr_chat = _save(session, world.users["mgr"], [source])
    assert _shown(raw_client, "emp@apex.test", emp_chat)[0]["available"] is True

    doc.status = "archived"
    session.commit()
    assert _shown(raw_client, "emp@apex.test", emp_chat)[0]["available"] is False
    assert _shown(raw_client, "mgr@apex.test", mgr_chat)[0]["available"] is True

    doc.status = "ready"
    session.commit()  # restored: the citation comes back
    assert _shown(raw_client, "emp@apex.test", emp_chat)[0]["available"] is True


def test_deleting_a_document_blanks_its_old_citations(
    raw_client: TestClient, session: Session, world: World
) -> None:
    doc = world.docs["safety"]
    cid = _save(session, world.users["admin"], [_source(1, doc, "Safety")])
    admin = login(raw_client, "admin@apex.test")
    assert raw_client.delete(f"{DOCS}/{doc.id}", headers=admin).status_code == 204
    assert _shown(raw_client, "admin@apex.test", cid)[0]["available"] is False


def test_moving_out_of_a_department_blanks_old_citations(
    raw_client: TestClient, session: Session, world: World
) -> None:
    cid = _save(session, world.users["emp"], [_source(1, world.docs["safety"], "Safety")])
    assert _shown(raw_client, "emp@apex.test", cid)[0]["available"] is True
    admin = login(raw_client, "admin@apex.test")
    moved = raw_client.patch(
        f"/api/v1/users/{world.users['emp'].id}",
        headers=admin,
        json={"departments": ["production"]},
    )
    assert moved.status_code == 200
    assert _shown(raw_client, "emp@apex.test", cid)[0]["available"] is False


def test_conversations_without_sources_and_other_peoples_conversations_still_behave(
    raw_client: TestClient, session: Session, world: World
) -> None:
    empty = repo.create(session, world.users["emp"].id, "Hello")
    repo.add_message(empty, "user", "Hello")
    repo.add_message(empty, "assistant", "Hi", status="not_found")
    session.commit()
    assert _shown(raw_client, "emp@apex.test", empty.id) == []
    stranger = raw_client.get(
        f"{CONVERSATIONS}/{empty.id}", headers=login(raw_client, "other@apex.test")
    )
    assert stranger.status_code == 404
