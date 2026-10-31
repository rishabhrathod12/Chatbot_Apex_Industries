import time

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.permissions import Role
from app.models.audit import AuditLog
from app.repositories import users as users_repo
from tests.conftest import PASSWORD, add_user, login

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"
DOCS = "/api/v1/documents"


@pytest.fixture
def alice(session: Session, settings: Settings):  # noqa: ANN201
    return add_user(session, settings, "alice@apex.test", Role.EMPLOYEE, ["production"])


def _login(client: TestClient, email: str = "alice@apex.test", password: str = PASSWORD):  # noqa: ANN202
    return client.post(LOGIN, json={"email": email, "password": password})


def _audit(session: Session, action: str) -> list[AuditLog]:
    session.expire_all()
    return list(session.scalars(select(AuditLog).where(AuditLog.action == action)))


# ---------- logging in ----------


def test_login_returns_tokens_and_who_you_are(raw_client: TestClient, alice) -> None:  # noqa: ANN001
    response = _login(raw_client)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 30 * 60
    assert body["user"] == {
        "user_id": alice.id,
        "login_id": "alice",
        "email": "alice@apex.test",
        "full_name": "alice",
        "role": "employee",
        "departments": ["production"],
        "must_change_password": False,
    }
    me = raw_client.get(ME, headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json() == body["user"]


def test_email_is_not_case_sensitive(raw_client: TestClient, alice) -> None:  # noqa: ANN001
    assert _login(raw_client, "  ALICE@Apex.Test ").status_code == 200


def test_the_password_is_never_returned_or_stored_in_clear(
    raw_client: TestClient,
    alice,
    session: Session,  # noqa: ANN001
) -> None:
    text = _login(raw_client).text
    assert PASSWORD not in text
    assert "password_hash" not in text
    assert PASSWORD not in users_repo.get_by_email(session, "alice@apex.test").password_hash  # type: ignore[union-attr]


# ---------- failing to log in ----------


def test_wrong_password_and_unknown_email_look_exactly_the_same(
    raw_client: TestClient,
    alice,  # noqa: ANN001
) -> None:
    wrong_password = _login(raw_client, password="wrong-password-123")
    unknown_email = _login(raw_client, "nobody@apex.test")
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json() == {"detail": "invalid_credentials"}
    assert wrong_password.headers["www-authenticate"] == "Bearer"


def test_a_disabled_account_cannot_log_in_and_looks_like_a_wrong_password(
    raw_client: TestClient,
    alice,
    session: Session,  # noqa: ANN001
) -> None:
    alice.is_active = False
    session.commit()
    response = _login(raw_client)
    assert (response.status_code, response.json()) == (401, {"detail": "invalid_credentials"})


def test_guessing_passwords_gets_locked_out_then_the_right_password_is_refused_too(
    raw_client: TestClient,
    alice,  # noqa: ANN001
) -> None:
    for _ in range(3):  # login_max_failures is 3 in the tests
        assert _login(raw_client, password="wrong-password-123").status_code == 401
    locked = _login(raw_client)  # even the correct password
    assert locked.status_code == 429
    assert locked.json() == {"detail": "too_many_attempts"}
    assert int(locked.headers["retry-after"]) > 0


def test_lockout_is_per_email_so_other_accounts_still_work(
    raw_client: TestClient,
    alice,
    session: Session,
    settings: Settings,  # noqa: ANN001
) -> None:
    add_user(session, settings, "bob@apex.test")
    for _ in range(3):
        _login(raw_client, password="wrong-password-123")
    assert _login(raw_client).status_code == 429
    assert _login(raw_client, "bob@apex.test").status_code == 200


def test_a_successful_login_clears_the_failure_count(raw_client: TestClient, alice) -> None:  # noqa: ANN001
    for _ in range(2):
        _login(raw_client, password="wrong-password-123")
    assert _login(raw_client).status_code == 200
    for _ in range(2):  # the counter started again from zero
        assert _login(raw_client, password="wrong-password-123").status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": "alice@apex.test"},
        {"password": "x"},
        {"email": "not-an-email", "password": "x"},
        {"email": "a@b.co", "password": ""},
        {"email": "a@b.co", "password": "x" * 300},
    ],
)
def test_malformed_login_requests_are_rejected(
    raw_client: TestClient, body: dict[str, str]
) -> None:
    assert raw_client.post(LOGIN, json=body).status_code == 422


def test_login_does_not_work_when_no_signing_secret_is_configured(
    raw_client: TestClient,
    alice,
    settings: Settings,  # noqa: ANN001
) -> None:
    from app.core.config import get_settings

    no_secret = settings.model_copy(update={"jwt_secret_key": None})
    raw_client.app.dependency_overrides[get_settings] = lambda: no_secret  # type: ignore[attr-defined]
    assert _login(raw_client).status_code == 503


# ---------- using a token ----------


def test_endpoints_refuse_requests_without_a_token(raw_client: TestClient) -> None:
    response = raw_client.get(DOCS)
    assert response.status_code == 401
    assert response.json() == {"detail": "not_authenticated"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header", ["Bearer", "Bearer ", "Bearer not.a.token", "Basic abc", "abc", "Token xyz"]
)
def test_endpoints_refuse_malformed_or_wrong_scheme_headers(
    raw_client: TestClient, header: str
) -> None:
    assert raw_client.get(DOCS, headers={"Authorization": header}).status_code == 401


def test_an_expired_token_is_refused_with_a_clear_reason(
    raw_client: TestClient,
    alice,
    settings: Settings,  # noqa: ANN001
) -> None:
    now = int(time.time())
    expired = jwt.encode(
        {
            "sub": alice.id,
            "typ": "access",
            "iss": settings.jwt_issuer,
            "iat": now - 120,
            "exp": now - 60,
        },
        settings.jwt_secret_key.get_secret_value(),  # type: ignore[union-attr]
        algorithm="HS256",
    )
    response = raw_client.get(ME, headers={"Authorization": f"Bearer {expired}"})
    assert (response.status_code, response.json()) == (401, {"detail": "token_expired"})


def test_a_token_signed_with_the_wrong_key_is_refused(
    raw_client: TestClient,
    alice,
    settings: Settings,  # noqa: ANN001
) -> None:
    now = int(time.time())
    forged = jwt.encode(
        {
            "sub": alice.id,
            "typ": "access",
            "iss": settings.jwt_issuer,
            "iat": now,
            "exp": now + 600,
        },
        "an-attackers-own-secret-" + "z" * 20,
        algorithm="HS256",
    )
    assert raw_client.get(ME, headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_a_forged_token_for_a_user_that_does_not_exist_is_refused(
    raw_client: TestClient, settings: Settings
) -> None:
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "f" * 32,
            "typ": "access",
            "iss": settings.jwt_issuer,
            "iat": now,
            "exp": now + 600,
        },
        settings.jwt_secret_key.get_secret_value(),  # type: ignore[union-attr]
        algorithm="HS256",
    )
    assert raw_client.get(ME, headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_disabling_an_account_stops_its_existing_tokens_immediately(
    raw_client: TestClient,
    alice,
    session: Session,  # noqa: ANN001
) -> None:
    headers = login(raw_client, "alice@apex.test")
    assert raw_client.get(ME, headers=headers).status_code == 200
    alice.is_active = False
    session.commit()
    assert raw_client.get(ME, headers=headers).status_code == 401


def test_a_role_change_takes_effect_on_the_next_request(
    raw_client: TestClient,
    alice,
    session: Session,  # noqa: ANN001
) -> None:
    headers = login(raw_client, "alice@apex.test")
    assert raw_client.get(ME, headers=headers).json()["role"] == "employee"
    alice.role = Role.SUPER_ADMIN.value
    session.commit()
    assert raw_client.get(ME, headers=headers).json()["role"] == "super_admin"


# ---------- refresh tokens ----------


def test_refresh_gives_a_new_pair_and_the_old_refresh_token_stops_working(
    raw_client: TestClient,
    alice,  # noqa: ANN001
) -> None:
    first = _login(raw_client).json()
    second = raw_client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200
    assert second.json()["refresh_token"] != first["refresh_token"]
    assert (
        raw_client.get(
            ME, headers={"Authorization": f"Bearer {second.json()['access_token']}"}
        ).status_code
        == 200
    )
    again = raw_client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert (again.status_code, again.json()) == (401, {"detail": "invalid_refresh_token"})


def test_reusing_an_old_refresh_token_ends_every_session_of_that_user(
    raw_client: TestClient,
    alice,
    session: Session,  # noqa: ANN001
) -> None:
    first = _login(raw_client).json()
    second = raw_client.post(REFRESH, json={"refresh_token": first["refresh_token"]}).json()
    # Someone presents the already-used token. They may have stolen it, so nothing is trusted.
    assert (
        raw_client.post(REFRESH, json={"refresh_token": first["refresh_token"]}).status_code == 401
    )
    assert (
        raw_client.post(REFRESH, json={"refresh_token": second["refresh_token"]}).status_code == 401
    )
    assert (
        len(_audit(session, "refresh_token_reuse")) >= 1
    )  # every use of a revoked token is logged


def test_an_unknown_refresh_token_is_refused(raw_client: TestClient) -> None:
    response = raw_client.post(REFRESH, json={"refresh_token": "x" * 43})
    assert (response.status_code, response.json()) == (401, {"detail": "invalid_refresh_token"})


def test_an_expired_refresh_token_is_refused(
    raw_client: TestClient,
    alice,
    settings: Settings,  # noqa: ANN001
) -> None:
    from app.core.config import get_settings

    pair = _login(raw_client).json()
    raw_client.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    from datetime import UTC, datetime, timedelta

    from app.core.database import get_session_factory
    from app.models.user import RefreshToken

    with get_session_factory(settings.database_url)() as db:
        token = db.scalars(select(RefreshToken)).one()
        token.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    response = raw_client.post(REFRESH, json={"refresh_token": pair["refresh_token"]})
    assert (response.status_code, response.json()) == (401, {"detail": "token_expired"})


def test_logout_ends_that_session(raw_client: TestClient, alice) -> None:  # noqa: ANN001
    pair = _login(raw_client).json()
    assert raw_client.post(LOGOUT, json={"refresh_token": pair["refresh_token"]}).status_code == 204
    assert (
        raw_client.post(REFRESH, json={"refresh_token": pair["refresh_token"]}).status_code == 401
    )


def test_logout_with_an_unknown_token_is_harmless(raw_client: TestClient) -> None:
    assert raw_client.post(LOGOUT, json={"refresh_token": "y" * 43}).status_code == 204


def test_a_disabled_account_cannot_refresh(
    raw_client: TestClient,
    alice,
    session: Session,  # noqa: ANN001
) -> None:
    pair = _login(raw_client).json()
    alice.is_active = False
    session.commit()
    assert (
        raw_client.post(REFRESH, json={"refresh_token": pair["refresh_token"]}).status_code == 401
    )


def test_the_database_holds_only_a_hash_of_the_refresh_token(
    raw_client: TestClient,
    alice,
    session: Session,  # noqa: ANN001
) -> None:
    from app.models.user import RefreshToken

    pair = _login(raw_client).json()
    session.expire_all()
    stored = session.scalars(select(RefreshToken)).one()
    assert stored.token_hash != pair["refresh_token"]
    assert pair["refresh_token"] not in stored.token_hash


# ---------- the audit trail ----------


def test_logins_are_audited_without_storing_the_email_typed(
    raw_client: TestClient,
    alice,
    session: Session,  # noqa: ANN001
) -> None:
    _login(raw_client, "nobody@apex.test")
    _login(raw_client)
    failed = [a for a in _audit(session, "login") if a.outcome == "failed"]
    ok = [a for a in _audit(session, "login") if a.outcome == "ok"]
    assert len(failed) == 1 and len(ok) == 1
    assert ok[0].user_id == alice.id
    assert failed[0].target_id and "nobody" not in failed[0].target_id
    assert "@" not in (failed[0].target_id or "")
