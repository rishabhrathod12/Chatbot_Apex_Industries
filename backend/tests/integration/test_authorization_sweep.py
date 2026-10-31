"""The final authorization sweep, part 1: attacks that change nothing (auth redesign, phase 9).

Where the other access-control tests check one rule at a time, these try to break the walls
between people: every route is classified on purpose, every other role attacks one person's
private documents through every endpoint, and forged fields are thrown at the search and chat
APIs. Nothing here is allowed to succeed, so one shared company is built once for all of it.

Part 2 (test_authorization_changes.py) covers the attacks that need a fresh company each time:
role and account changes, temporary passwords, forged account fields and uploads.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import Base, get_engine, get_session_factory
from app.core.rate_limit import LoginLimiter
from app.main import create_app
from app.models import AuditLog, Document
from app.rag.vectorstore import close_client
from tests.arena import (
    API,
    CODE,
    FAKE_ID,
    FILES,
    PENDING_OK,
    PUBLIC,
    QUERY,
    SIGNED_IN,
    Arena,
    admin_routes,
    all_routes,
    call,
    make_arena,
)
from tests.conftest import PASSWORD, SAMPLE_DOCS


@pytest.fixture(scope="module")
def shared_settings(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Settings]:
    base: Path = tmp_path_factory.mktemp("sweep")
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        database_url=f"sqlite:///{base / 'sweep.db'}",
        upload_dir=str(base / "uploads"),
        vector_store_path=str(base / "vectors"),
        embedding_provider="hashing",
        # The same search settings as a real deployment: keyword + meaning, then a reranking
        # check by the language model. Both must respect who is asking.
        retrieval_mode="hybrid",
        rerank_enabled=True,
        jwt_secret_key="sweep-secret-" + "s" * 32,
        password_hash_cost_log2=10,
        max_upload_mb=1,
        login_max_failures=1000,  # sign-in is not what is under test here
    )
    yield settings
    close_client(settings.vector_store_path)
    get_engine(settings.database_url).dispose()


@pytest.fixture(scope="module")
def arena(shared_settings: Settings) -> Arena:
    Base.metadata.create_all(get_engine(shared_settings.database_url))
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: shared_settings
    app.state.login_limiter = LoginLimiter(1000, 60)
    client = TestClient(app)
    with get_session_factory(shared_settings.database_url)() as db:
        return make_arena(client, shared_settings, db)


@pytest.fixture
def db(shared_settings: Settings) -> Iterator[Session]:
    with get_session_factory(shared_settings.database_url)() as session:
        yield session


# ===============================================================================================
# 1. Every route is classified, and the classification is enforced
# ===============================================================================================


def test_every_route_has_been_classified_on_purpose(arena: Arena) -> None:
    """A new endpoint fails here until someone decides who may call it."""
    routes = all_routes(arena.client)
    classified = PUBLIC | PENDING_OK | SIGNED_IN | admin_routes(routes)
    assert routes == classified, (
        f"unclassified: {sorted(routes - classified)}; stale: {sorted(classified - routes)}"
    )


def test_admin_routes_refuse_every_other_role_before_looking_at_the_request(arena: Arena) -> None:
    for route in sorted(admin_routes(all_routes(arena.client))):
        for who in ("mgr_prod", "mgr_hr", "alice", "carol"):
            response = call(arena.client, route, arena.headers[who])
            assert (response.status_code, response.json().get("detail")) == (
                403,
                "not_allowed",
            ), (route, who)
        assert call(arena.client, route).status_code == 401, route
        assert call(arena.client, route, arena.headers["admin"]).status_code not in (401, 403), (
            route
        )


# ===============================================================================================
# 2. One person's private documents against everyone else, through every endpoint
# ===============================================================================================

DOCUMENT_ENDPOINTS = [
    ("GET", "/documents/{id}"),
    ("GET", "/documents/{id}/versions"),
    ("POST", "/documents/{id}/reindex"),
    ("POST", "/documents/{id}/archive"),
    ("POST", "/documents/{id}/restore"),
    ("POST", "/documents/{id}/versions"),
    ("DELETE", "/documents/{id}"),
]


def hit(client: TestClient, headers: dict[str, str], method: str, path: str, doc_id: str) -> Any:
    url = API + path.replace("{id}", doc_id)
    if path.endswith("/versions") and method == "POST":
        content = (SAMPLE_DOCS / FILES["safety"]).read_bytes()
        return client.post(url, headers=headers, files={"file": ("x.pdf", content)})
    return client.request(method, url, headers=headers)


@pytest.mark.parametrize(("method", "path"), DOCUMENT_ENDPOINTS)
@pytest.mark.parametrize("attacker", ["bob", "carol", "mgr_prod", "mgr_hr", "admin", "admin2"])
def test_nobody_but_the_owner_can_touch_a_private_document(
    arena: Arena, attacker: str, method: str, path: str
) -> None:
    """Everyone else, the Super Admin included, gets the same answer as for a document that does
    not exist: no hint that it is there."""
    real = hit(arena.client, arena.headers[attacker], method, path, arena.docs["alice_private"])
    nothing = hit(arena.client, arena.headers[attacker], method, path, FAKE_ID)
    assert real.status_code == nothing.status_code == 404
    assert real.json() == nothing.json()


def test_the_owner_can_still_use_their_document_and_nothing_changed(
    arena: Arena, db: Session
) -> None:
    for attacker in ("bob", "carol", "mgr_prod", "mgr_hr", "admin"):
        for method, path in DOCUMENT_ENDPOINTS:
            hit(arena.client, arena.headers[attacker], method, path, arena.docs["alice_private"])
    mine = arena.client.get(
        f"{API}/documents/{arena.docs['alice_private']}", headers=arena.headers["alice"]
    )
    assert (mine.status_code, mine.json()["status"]) == (200, "ready")
    found = arena.client.post(
        f"{API}/search",
        headers=arena.headers["alice"],
        json={"query": QUERY["alice_private"], "scope": "personal"},
    ).json()
    assert {h["doc_code"] for h in found} == {CODE["alice_private"]}
    row = db.get(Document, arena.docs["alice_private"])
    assert row is not None
    assert (row.status, row.deleted_at, row.archived_at) == ("ready", None, None)
    assert row.chunk_count > 0


def test_a_private_document_is_in_nobody_elses_library(arena: Arena) -> None:
    for who in ("bob", "carol", "mgr_prod", "mgr_hr", "admin", "admin2"):
        listing = arena.client.get(f"{API}/documents", headers=arena.headers[who]).json()
        assert arena.docs["alice_private"] not in [d["id"] for d in listing], who
    own = arena.client.get(f"{API}/documents", headers=arena.headers["alice"]).json()
    assert arena.docs["alice_private"] in [d["id"] for d in own]


# ===============================================================================================
# 3. Search and chat: the server decides, whatever the request says
# ===============================================================================================

FORGERIES: list[dict[str, Any]] = [
    {},
    {"owner_id": "ALICE"},
    {"user_id": "ALICE"},
    {"document_ids": ["ALICE_DOC"]},
    {"departments": ["quality", "hr"]},
    {"include_company_wide": True},
    {"filter": {"owner_id": "ALICE"}},
    {"search_filter": {"departments": ["quality"]}},
    {"role": "super_admin"},
]


def _forge(arena: Arena, body: dict[str, Any]) -> dict[str, Any]:
    ids = {"ALICE_DOC": arena.docs["alice_private"], "ALICE": arena.users["alice"].id}

    def substitute(value: Any) -> Any:
        if isinstance(value, str):
            return ids.get(value, value)
        if isinstance(value, list):
            return [substitute(v) for v in value]
        if isinstance(value, dict):
            return {k: substitute(v) for k, v in value.items()}
        return value

    return substitute(body)  # type: ignore[no-any-return]


@pytest.mark.parametrize("forgery", FORGERIES)
@pytest.mark.parametrize("scope", ["personal", "enterprise"])
def test_forged_fields_never_widen_what_bob_can_find(
    arena: Arena, forgery: dict[str, Any], scope: str
) -> None:
    forged = _forge(arena, forgery)
    for key in CODE:
        hits = arena.client.post(
            f"{API}/search",
            headers=arena.headers["bob"],
            json={"query": QUERY[key], "scope": scope, "top_k": 20, **forged},
        )
        assert hits.status_code == 200, hits.text
        codes = {h["doc_code"] for h in hits.json()}
        assert CODE["alice_private"] not in codes and CODE["quality"] not in codes
        if scope == "personal":
            assert codes <= {CODE["bob_private"]}
        else:
            assert CODE["bob_private"] not in codes  # a personal document is never a company one


@pytest.mark.parametrize("forgery", FORGERIES)
def test_forged_fields_never_widen_what_the_model_is_shown(
    arena: Arena, forgery: dict[str, Any]
) -> None:
    forged = _forge(arena, forgery)
    before = len(arena.chat.calls)
    for scope in ("personal", "enterprise"):
        for key in CODE:
            response = arena.client.post(
                f"{API}/chat",
                headers=arena.headers["bob"],
                json={"question": QUERY[key], "scope": scope, **forged},
            )
            assert response.status_code == 200, response.text
            cited = {s["doc_code"] for s in response.json()["sources"]}
            assert cited.isdisjoint({CODE["alice_private"], CODE["quality"]})
    prompts = arena.prompts(before)
    assert prompts, "the model was never called"
    assert CODE["alice_private"] not in prompts and CODE["quality"] not in prompts


@pytest.mark.parametrize("who", ["bob", "carol", "mgr_prod", "mgr_hr", "admin", "admin2"])
def test_no_one_but_alice_ever_sends_alices_private_text_to_the_model(
    arena: Arena, who: str
) -> None:
    before = len(arena.chat.calls)
    for scope in ("personal", "enterprise"):
        for key in CODE:
            arena.client.post(
                f"{API}/chat",
                headers=arena.headers[who],
                json={"question": QUERY[key], "scope": scope},
            )
    assert CODE["alice_private"] not in arena.prompts(before)


def test_a_super_admin_asking_the_company_library_never_gets_a_private_document(
    arena: Arena,
) -> None:
    hits = arena.client.post(
        f"{API}/search",
        headers=arena.headers["admin"],
        json={"query": QUERY["alice_private"], "scope": "enterprise", "top_k": 20},
    ).json()
    codes = {h["doc_code"] for h in hits}
    assert CODE["alice_private"] not in codes and CODE["bob_private"] not in codes
    # ... while the company documents the admin may read are found
    found = arena.client.post(
        f"{API}/search",
        headers=arena.headers["admin"],
        json={"query": QUERY["quality"], "scope": "enterprise", "top_k": 20},
    ).json()
    assert CODE["quality"] in {h["doc_code"] for h in found}


def test_the_department_box_cannot_be_used_to_reach_a_forbidden_department(arena: Arena) -> None:
    for department in ("quality", "QUALITY", " quality ", "Quality Control", "qc"):
        response = arena.client.post(
            f"{API}/search",
            headers=arena.headers["bob"],
            json={"query": QUERY["quality"], "department": department, "top_k": 20},
        )
        if response.status_code == 200:
            assert CODE["quality"] not in {h["doc_code"] for h in response.json()}
        else:
            assert response.status_code == 422


def test_conversations_stay_with_their_owner(arena: Arena) -> None:
    made = arena.client.post(
        f"{API}/chat", headers=arena.headers["alice"], json={"question": QUERY["safety"]}
    ).json()["conversation_id"]
    for who in ("bob", "carol", "mgr_prod", "admin"):
        headers = arena.headers[who]
        assert arena.client.get(f"{API}/conversations/{made}", headers=headers).status_code == 404
        assert (
            arena.client.delete(f"{API}/conversations/{made}", headers=headers).status_code == 404
        )
        reply = arena.client.post(
            f"{API}/chat", headers=headers, json={"question": "hello", "conversation_id": made}
        )
        assert reply.status_code == 404
    mine = arena.client.get(f"{API}/conversations/{made}", headers=arena.headers["alice"]).json()
    assert len(mine["messages"]) == 2  # nothing was added by the intruders


# ===============================================================================================
# 4. The user API, and the audit trail
# ===============================================================================================


def test_people_cannot_edit_themselves_or_anyone_else_through_the_user_api(arena: Arena) -> None:
    for who in ("alice", "mgr_prod"):
        mine = arena.users[who].id
        for change in ({"role": "super_admin"}, {"departments": ["hr"]}, {"is_active": True}):
            response = arena.client.patch(
                f"{API}/users/{mine}", headers=arena.headers[who], json=change
            )
            assert response.status_code == 403, (who, change)
        reset = arena.client.post(
            f"{API}/users/{mine}/password", headers=arena.headers[who], json={}
        )
        assert reset.status_code == 403


def test_the_setup_form_is_closed_once_anyone_exists(arena: Arena) -> None:
    attempt = arena.client.post(
        f"{API}/auth/setup",
        json={"email": "root@apex.test", "full_name": "Root", "password": "A-long-password-1"},
    )
    assert (attempt.status_code, attempt.json()["detail"]) == (409, "setup_already_done")
    assert arena.client.get(f"{API}/auth/setup").json()["setup_required"] is False


def test_the_attacks_leave_an_audit_trail_with_no_document_text(arena: Arena, db: Session) -> None:
    for method, path in DOCUMENT_ENDPOINTS[2:6]:
        hit(arena.client, arena.headers["carol"], method, path, arena.docs["alice_private"])
    call(arena.client, ("GET", "/api/v1/admin/stats"), arena.headers["carol"])
    rows = db.scalars(select(AuditLog).where(AuditLog.user_id == arena.users["carol"].id)).all()
    assert any(r.outcome == "denied" for r in rows), "refused attempts should be recorded"
    dump = repr(db.execute(text("SELECT * FROM audit_log")).fetchall())
    for secret in ("leave request", "Factory Health", PASSWORD, "Bearer "):
        assert secret not in dump
