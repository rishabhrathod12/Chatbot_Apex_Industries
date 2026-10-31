"""Who can see what: a matrix of real users (real tokens) against documents in different
departments. This is the cross-department unauthorised-access test the project plan asks for.

Documents (all indexed for real, with the offline embedder):
  safety       dept safety       company-wide     HR policy: dept hr, company-wide
  production   dept production   department-only  maintenance: dept maintenance, department-only
  quality      dept quality      department-only
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_chat_model_provider
from app.core.config import Settings, get_settings
from app.core.database import Base, get_engine, get_session_factory
from app.core.permissions import Role
from app.core.rate_limit import LoginLimiter
from app.main import create_app
from app.rag.vectorstore import close_client
from tests.conftest import SAMPLE_DOCS, ScriptedChat, add_user, login

# key: (file, department, visibility, document code)
DOCS = {
    "safety": ("03_Factory_Safety_Manual.pdf", "safety", "company", "APM-HSE-MAN-003"),
    "hr": ("05_HR_Policy.pdf", "hr", "company", "APM-HR-POL-005"),
    "production": (
        "01_Machine_Operation_Manual.pdf",
        "production",
        "department",
        "APM-PROD-MAN-001",
    ),
    "maintenance": ("02_Maintenance_Manual.pdf", "maintenance", "department", "APM-MNT-MAN-002"),
    "quality": ("06_Quality_Control_SOP.pdf", "quality", "department", "APM-QLT-SOP-006"),
}
CODE = {key: value[3] for key, value in DOCS.items()}
QUERY = {
    "safety": "emergency stop devices bypass guards interlocks",
    "hr": "submit a leave request attendance",
    "production": "keep doors and guards closed during automatic operation",
    "maintenance": "do not repeatedly reset alarms fault reporting",
    "quality": "authorized Quality personnel release product inspection",
}

USERS = {
    "admin": ("admin@apex.test", Role.SUPER_ADMIN, []),
    "safety_mgr": ("safety.mgr@apex.test", Role.DEPARTMENT_MANAGER, ["safety"]),
    "hr_mgr": ("hr.mgr@apex.test", Role.DEPARTMENT_MANAGER, ["hr"]),
    "prod_mgr": ("prod.mgr@apex.test", Role.DEPARTMENT_MANAGER, ["production"]),
    "prod_emp": ("prod.emp@apex.test", Role.EMPLOYEE, ["production"]),
    "stores_emp": ("stores.emp@apex.test", Role.EMPLOYEE, ["stores"]),
}

ALL = set(DOCS)
# What each user may READ: own department(s) + company-wide (safety, hr). Admin reads all.
CAN_READ = {
    "admin": ALL,
    "safety_mgr": {"safety", "hr"},
    "hr_mgr": {"safety", "hr"},
    "prod_mgr": {"safety", "hr", "production"},
    "prod_emp": {"safety", "hr", "production"},
    "stores_emp": {"safety", "hr"},
}
# What each user may CHANGE (re-index, delete): admin all; a manager only their own department.
CAN_MANAGE = {
    "admin": ALL,
    "safety_mgr": {"safety"},
    "hr_mgr": {"hr"},
    "prod_mgr": {"production"},
    "prod_emp": set(),
    "stores_emp": set(),
}


@dataclass
class World:
    client: TestClient
    settings: Settings
    chat: ScriptedChat
    headers: dict[str, dict[str, str]]
    document_ids: dict[str, str] = field(default_factory=dict)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    base = tmp_path_factory.mktemp("acl")
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        database_url=f"sqlite:///{base / 'acl.db'}",
        upload_dir=str(base / "uploads"),
        vector_store_path=str(base / "vectors"),
        embedding_provider="hashing",
        jwt_secret_key="module-secret-" + "m" * 32,
        password_hash_cost_log2=10,
        retrieval_min_score=0.0,  # hashing scores are low; the gate is not what is under test
        max_upload_mb=1,
    )
    Base.metadata.create_all(get_engine(settings.database_url))
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.login_limiter = LoginLimiter(1000, 60)
    chat = ScriptedChat("From the excerpts [1].")
    app.dependency_overrides[get_chat_model_provider] = lambda: lambda: chat
    client = TestClient(app)

    with get_session_factory(settings.database_url)() as db:
        for email, role, departments in USERS.values():
            add_user(db, settings, email, role, departments)
    headers = {key: login(client, USERS[key][0]) for key in USERS}

    built = World(client, settings, chat, headers)
    for key, (filename, department, visibility, _code) in DOCS.items():
        response = client.post(
            "/api/v1/documents",
            headers=headers["admin"],
            data={"scope": "enterprise", "department": department, "visibility": visibility},
            files=[("files", (filename, (SAMPLE_DOCS / filename).read_bytes()))],
        )
        assert response.status_code == 201, response.text
        built.document_ids[key] = response.json()["results"][0]["document"]["id"]

    listing = client.get("/api/v1/documents", headers=headers["admin"]).json()
    assert [d["status"] for d in listing] == ["ready"] * len(DOCS), listing
    yield built
    close_client(settings.vector_store_path)
    get_engine(settings.database_url).dispose()


def _codes(items: list[dict[str, Any]], field_name: str = "doc_code") -> set[str]:
    return {str(item[field_name]) for item in items if item.get(field_name)}


# ---------- seeing the library ----------


@pytest.mark.parametrize("user", USERS)
def test_each_user_lists_exactly_the_documents_they_may_read(world: World, user: str) -> None:
    listing = world.client.get("/api/v1/documents", headers=world.headers[user]).json()
    assert _codes(listing) == {CODE[k] for k in CAN_READ[user]}


@pytest.mark.parametrize("doc", DOCS)
@pytest.mark.parametrize("user", USERS)
def test_opening_a_document_directly(world: World, user: str, doc: str) -> None:
    response = world.client.get(
        f"/api/v1/documents/{world.document_ids[doc]}", headers=world.headers[user]
    )
    assert response.status_code == (200 if doc in CAN_READ[user] else 404)


def test_a_document_you_cannot_read_looks_exactly_like_one_that_does_not_exist(
    world: World,
) -> None:
    hidden = world.client.get(
        f"/api/v1/documents/{world.document_ids['quality']}", headers=world.headers["stores_emp"]
    )
    missing = world.client.get(f"/api/v1/documents/{'0' * 32}", headers=world.headers["stores_emp"])
    assert (hidden.status_code, hidden.json()) == (missing.status_code, missing.json())


# ---------- searching ----------


@pytest.mark.parametrize("user", USERS)
def test_search_never_returns_a_chunk_from_a_forbidden_document(world: World, user: str) -> None:
    found: set[str] = set()
    for query in QUERY.values():
        response = world.client.post(
            "/api/v1/search",
            headers=world.headers[user],
            json={"query": query, "top_k": 20},
        )
        assert response.status_code == 200
        found |= _codes(response.json())
    assert found <= {CODE[k] for k in CAN_READ[user]}, f"{user} saw {found}"


@pytest.mark.parametrize("doc", DOCS)
@pytest.mark.parametrize("user", USERS)
def test_search_still_finds_what_a_user_is_allowed_to_see(
    world: World, user: str, doc: str
) -> None:
    """The other half: the filter must not hide documents the user may read."""
    response = world.client.post(
        "/api/v1/search",
        headers=world.headers[user],
        json={"query": QUERY[doc], "top_k": 20},
    )
    found = _codes(response.json())
    assert (CODE[doc] in found) == (doc in CAN_READ[user])


# ---------- what reaches the language model ----------


@pytest.mark.parametrize("user", USERS)
def test_the_model_is_only_ever_shown_documents_the_user_may_read(world: World, user: str) -> None:
    seen_before = len(world.chat.calls)
    for doc in DOCS:
        response = world.client.post(
            "/api/v1/chat", headers=world.headers[user], json={"question": QUERY[doc]}
        )
        assert response.status_code == 200
    prompts = "\n".join(
        call[1]["content"] for call in world.chat.calls[seen_before:] if call[0]["role"] == "system"
    )
    forbidden = {CODE[k] for k in ALL - CAN_READ[user]}
    assert prompts, "the model was never called"
    for code in forbidden:
        assert code not in prompts, f"{user}: text from {code} was sent to the model"


@pytest.mark.parametrize("user", ["stores_emp", "prod_emp"])
def test_answers_never_cite_a_forbidden_document(world: World, user: str) -> None:
    forbidden = {CODE[k] for k in ALL - CAN_READ[user]}
    for doc in DOCS:
        body = world.client.post(
            "/api/v1/chat", headers=world.headers[user], json={"question": QUERY[doc]}
        ).json()
        assert {s["doc_code"] for s in body["sources"]}.isdisjoint(forbidden)


# ---------- the department box can only narrow ----------


def _search(world: World, user: str, **body: Any) -> set[str]:
    response = world.client.post(
        "/api/v1/search",
        headers=world.headers[user],
        json={"query": "rules procedure approval", "top_k": 20, **body},
    )
    assert response.status_code == 200
    return _codes(response.json())


def test_asking_for_another_department_reveals_nothing(world: World) -> None:
    assert _search(world, "prod_emp", department="quality") == set()
    assert _search(world, "stores_emp", department="production") == set()
    assert _search(world, "stores_emp", department="maintenance") == set()


def test_narrowing_to_a_visible_department_works(world: World) -> None:
    assert _search(world, "prod_emp", department="production") == {CODE["production"]}
    assert _search(world, "prod_emp", department="hr") == {CODE["hr"]}  # company-wide
    assert _search(world, "admin", department="quality") == {CODE["quality"]}


def test_chat_department_box_is_also_only_a_narrowing(world: World) -> None:
    before = len(world.chat.calls)
    body = world.client.post(
        "/api/v1/chat",
        headers=world.headers["stores_emp"],
        json={"question": QUERY["quality"], "department": "quality"},
    ).json()
    assert body["status"] == "not_found"
    assert len(world.chat.calls) == before  # nothing was even sent to the model


# ---------- changing documents ----------


@pytest.mark.parametrize("doc", DOCS)
@pytest.mark.parametrize("user", USERS)
def test_reindex_permissions(world: World, user: str, doc: str) -> None:
    response = world.client.post(
        f"/api/v1/documents/{world.document_ids[doc]}/reindex", headers=world.headers[user]
    )
    if doc in CAN_MANAGE[user]:
        expected = 202
    elif doc in CAN_READ[user]:
        expected = 403  # can see it, may not change it
    else:
        expected = 404  # cannot even see it
    assert response.status_code == expected


@pytest.mark.parametrize("user", USERS)
def test_user_management_is_for_super_admins_only(world: World, user: str) -> None:
    response = world.client.get("/api/v1/users", headers=world.headers[user])
    assert response.status_code == (200 if user == "admin" else 403)
