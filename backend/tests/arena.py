"""A small company to attack: users of every role, private and company documents, all indexed.

Shared by the authorization sweep tests. `make_arena` builds it through the real API, so the
documents are really uploaded, indexed and searchable, and the users really sign in.
"""

from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_chat_model_provider
from app.core.config import Settings
from app.core.permissions import Role
from app.models import User
from tests.conftest import SAMPLE_DOCS, ScriptedChat, add_user, login

API = "/api/v1"
FAKE_ID = "0" * 32

# ---- every route, classified on purpose ------------------------------------------------------

# Open to anyone, on purpose (enforced by security/test_route_protection.py).
PUBLIC = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/health/ready"),  # for Docker/load balancers; says ok/failed only
    ("GET", "/api/v1/auth/setup"),  # is the first account still missing?
    ("POST", "/api/v1/auth/setup"),  # creates it; closed once any user exists (test_setup_api.py)
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
}

# Usable by someone who still has to choose a new password (besides the public ones).
PENDING_OK = {("GET", "/api/v1/auth/me"), ("POST", "/api/v1/auth/change-password")}

# Only a Super Admin.
ADMIN_PREFIXES = ("/api/v1/users", "/api/v1/admin")

# Any signed-in person, with the resource checks (owner, department) done inside the route.
SIGNED_IN = {
    ("POST", "/api/v1/chat"),
    ("POST", "/api/v1/search"),
    ("GET", "/api/v1/conversations"),
    ("GET", "/api/v1/conversations/{conversation_id}"),
    ("DELETE", "/api/v1/conversations/{conversation_id}"),
    ("GET", "/api/v1/documents"),
    ("POST", "/api/v1/documents"),
    ("GET", "/api/v1/documents/{document_id}"),
    ("DELETE", "/api/v1/documents/{document_id}"),
    ("POST", "/api/v1/documents/{document_id}/archive"),
    ("POST", "/api/v1/documents/{document_id}/reindex"),
    ("POST", "/api/v1/documents/{document_id}/restore"),
    ("POST", "/api/v1/documents/{document_id}/versions"),
    ("GET", "/api/v1/documents/{document_id}/versions"),
}

__all__ = ["PUBLIC", "PENDING_OK", "SIGNED_IN"]


def all_routes(client: TestClient) -> set[tuple[str, str]]:
    paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]
    return {
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    }


def admin_routes(routes: set[tuple[str, str]]) -> set[tuple[str, str]]:
    return {r for r in routes if r[1].startswith(ADMIN_PREFIXES)}


def call(client: TestClient, route: tuple[str, str], headers: dict[str, str] | None = None) -> Any:
    method, path = route
    for name in ("conversation_id", "document_id", "user_id"):
        path = path.replace("{" + name + "}", FAKE_ID)
    return client.request(method, path, headers=headers or {}, json={})


# ---- the arena -------------------------------------------------------------------------------

CODE = {
    "alice_private": "APM-HR-POL-005",  # alice's personal PDF (a copy of the HR policy)
    "bob_private": "APM-MNT-MAN-002",  # bob's personal PDF (a copy of the maintenance manual)
    "safety": "APM-HSE-MAN-003",  # company-wide
    "quality": "APM-QLT-SOP-006",  # Quality department only
}
QUERY = {
    "alice_private": "submit a leave request attendance",
    "bob_private": "do not repeatedly reset alarms fault reporting",
    "safety": "emergency stop devices bypass guards interlocks",
    "quality": "authorized Quality personnel release product inspection",
}
FILES = {
    "alice_private": "05_HR_Policy.pdf",
    "bob_private": "02_Maintenance_Manual.pdf",
    "safety": "03_Factory_Safety_Manual.pdf",
    "quality": "06_Quality_Control_SOP.pdf",
}

USERS = {
    "admin": (Role.SUPER_ADMIN, []),
    "admin2": (Role.SUPER_ADMIN, []),
    "mgr_prod": (Role.DEPARTMENT_MANAGER, ["production"]),
    "mgr_hr": (Role.DEPARTMENT_MANAGER, ["hr"]),
    "alice": (Role.EMPLOYEE, ["production"]),
    "bob": (Role.EMPLOYEE, ["production"]),
    "carol": (Role.EMPLOYEE, ["hr"]),
}


@dataclass
class Arena:
    client: TestClient
    chat: ScriptedChat
    users: dict[str, User]
    headers: dict[str, dict[str, str]]
    docs: dict[str, str]

    def prompts(self, since: int = 0) -> str:
        return "\n".join(m["content"] for call_ in self.chat.calls[since:] for m in call_)


def make_arena(client: TestClient, settings: Settings, db: Session) -> Arena:
    settings.retrieval_min_score = 0.0  # offline scores are low; the gate is not under test
    chat = ScriptedChat("From the excerpts [1].")
    client.app.dependency_overrides[get_chat_model_provider] = lambda: lambda: chat  # type: ignore[attr-defined]
    users = {
        name: add_user(db, settings, f"{name}@apex.test", role, departments)
        for name, (role, departments) in USERS.items()
    }
    headers = {name: login(client, f"{name}@apex.test") for name in USERS}

    docs: dict[str, str] = {}

    def upload(key: str, who: str, **form: str) -> None:
        content = (SAMPLE_DOCS / FILES[key]).read_bytes()
        response = client.post(
            f"{API}/documents",
            headers=headers[who],
            data=form,
            files=[("files", (FILES[key], content))],
        )
        assert response.status_code == 201, response.text
        docs[key] = response.json()["results"][0]["document"]["id"]

    upload("alice_private", "alice", scope="personal")
    upload("bob_private", "bob", scope="personal")
    upload("safety", "admin", scope="enterprise", department="safety", visibility="company")
    upload("quality", "admin", scope="enterprise", department="quality")
    listing = client.get(f"{API}/documents", headers=headers["admin"]).json()
    assert {d["status"] for d in listing} == {"ready"}
    return Arena(client, chat, users, headers, docs)
