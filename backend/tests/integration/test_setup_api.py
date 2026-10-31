"""First-run setup: the only way to get an account without having one, so it must be tight."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.permissions import Role
from app.models import AuditLog, User
from tests.conftest import PASSWORD, add_user

BODY = {"email": "Boss@Apex.test", "full_name": "The Boss", "password": PASSWORD}


def _setup(client: TestClient, **changes: Any):  # noqa: ANN202
    return client.post("/api/v1/auth/setup", json={**BODY, **changes})


def test_status_says_setup_is_needed_on_an_empty_system(raw_client: TestClient) -> None:
    response = raw_client.get("/api/v1/auth/setup")
    assert response.status_code == 200
    assert response.json() == {"setup_required": True, "token_required": False, "available": True}


def test_the_first_account_is_a_super_admin_who_can_sign_in(raw_client: TestClient) -> None:
    response = _setup(raw_client)
    assert response.status_code == 201
    assert response.json()["role"] == "super_admin"
    assert response.json()["email"] == "boss@apex.test"
    assert "password" not in response.text

    login = raw_client.post(
        "/api/v1/auth/login", json={"email": "boss@apex.test", "password": PASSWORD}
    )
    assert login.status_code == 200
    assert login.json()["user"]["role"] == "super_admin"


def test_setup_closes_for_good_after_the_first_account(raw_client: TestClient) -> None:
    assert _setup(raw_client).status_code == 201
    assert raw_client.get("/api/v1/auth/setup").json()["setup_required"] is False
    again = _setup(raw_client, email="second@apex.test")
    assert again.status_code == 409
    assert again.json()["detail"] == "setup_already_done"


def test_setup_is_refused_when_any_user_exists(
    raw_client: TestClient, session: Session, settings: Settings
) -> None:
    add_user(session, settings, "emp@apex.test", Role.EMPLOYEE)
    assert _setup(raw_client).status_code == 409
    assert session.scalars(select(User)).all().__len__() == 1


def test_a_short_password_creates_nothing(raw_client: TestClient, session: Session) -> None:
    response = _setup(raw_client, password="short")
    assert response.status_code == 422
    assert response.json()["detail"] == "password_too_short"
    assert session.scalars(select(User)).first() is None
    assert raw_client.get("/api/v1/auth/setup").json()["setup_required"] is True


def test_malformed_requests_are_rejected(raw_client: TestClient) -> None:
    assert _setup(raw_client, email="not-an-email").status_code == 422
    assert raw_client.post("/api/v1/auth/setup", json={}).status_code == 422


def test_with_a_setup_token_the_right_one_is_required(
    raw_client: TestClient, settings: Settings, session: Session
) -> None:
    settings.setup_token = SecretStr("t" * 24)
    assert raw_client.get("/api/v1/auth/setup").json()["token_required"] is True

    assert _setup(raw_client).status_code == 403  # no token
    wrong = _setup(raw_client, setup_token="w" * 24)
    assert wrong.status_code == 403 and wrong.json()["detail"] == "invalid_setup_token"
    assert session.scalars(select(User)).first() is None

    assert _setup(raw_client, setup_token="t" * 24).status_code == 201


def test_guessing_the_setup_token_is_rate_limited(
    raw_client: TestClient, settings: Settings
) -> None:
    settings.setup_token = SecretStr("t" * 24)
    for _ in range(settings.login_max_failures):
        assert _setup(raw_client, setup_token="guess").status_code == 403
    blocked = _setup(raw_client, setup_token="t" * 24)  # even the right one waits
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


def test_in_production_setup_stays_off_until_a_token_is_configured(
    raw_client: TestClient, settings: Settings
) -> None:
    settings.app_env = "production"
    status = raw_client.get("/api/v1/auth/setup").json()
    assert status == {"setup_required": True, "token_required": False, "available": False}
    refused = _setup(raw_client)
    assert refused.status_code == 403 and refused.json()["detail"] == "setup_disabled"

    settings.setup_token = SecretStr("t" * 24)
    assert _setup(raw_client, setup_token="t" * 24).status_code == 201


def test_setup_attempts_are_audited_without_secrets(
    raw_client: TestClient, settings: Settings, session: Session
) -> None:
    settings.setup_token = SecretStr("t" * 24)
    _setup(raw_client, setup_token="wrong-token-value")
    _setup(raw_client, setup_token="t" * 24)
    rows = session.scalars(select(AuditLog).where(AuditLog.action == "setup_admin")).all()
    assert sorted(r.outcome for r in rows) == ["denied", "ok"]
    dump = " ".join(f"{r.user_id} {r.target_id}" for r in rows)
    assert "wrong-token-value" not in dump and PASSWORD not in dump


@pytest.mark.parametrize("path", ["/api/v1/auth/setup"])
def test_setup_status_needs_no_login(raw_client: TestClient, path: str) -> None:
    assert raw_client.get(path).status_code == 200
