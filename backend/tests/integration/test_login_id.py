"""Login IDs: people can sign in with a Login ID or an email, and existing accounts survive."""

from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import Settings
from app.core.permissions import Role
from app.repositories import users as repo
from tests.conftest import PASSWORD, add_user

BACKEND = Path(__file__).resolve().parents[2]
LOGIN = "/api/v1/auth/login"


def _login(client: TestClient, **body: str):  # noqa: ANN202
    return client.post(LOGIN, json={"password": PASSWORD, **body})


# ---- the migration ----


def _old_database(tmp_path: Path):  # noqa: ANN202
    """A database exactly as it was before this change (revision 0004) holding real users."""
    url = f"sqlite:///{tmp_path / 'old.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0004")
    engine = create_engine(url)
    rows = [
        ("u1", "asha.admin@apex.test", "scrypt$hash-one", "super_admin"),
        ("u2", "sam@apex.test", "scrypt$hash-two", "employee"),
        ("u3", "sam@other.test", "scrypt$hash-three", "employee"),  # same name before the @
        ("u4", "Sam@third.test", "scrypt$hash-four", "employee"),
        ("u5", "weird+tag!@apex.test", "scrypt$hash-five", "employee"),
        ("u6", "@odd.test", "scrypt$hash-six", "employee"),
    ]
    with engine.begin() as conn:
        for i, (user_id, email, password_hash, role) in enumerate(rows):
            conn.execute(
                text(
                    "INSERT INTO users (id, email, full_name, password_hash, role, is_active, "
                    "created_at) VALUES (:id, :email, 'N', :ph, :role, 1, :at)"
                ),
                {
                    "id": user_id,
                    "email": email.lower(),
                    "ph": password_hash,
                    "role": role,
                    "at": f"2026-01-0{i + 1} 00:00:00",
                },
            )
    return cfg, engine


def test_upgrade_gives_every_existing_user_a_unique_login_id_and_keeps_their_data(
    tmp_path: Path,
) -> None:
    cfg, engine = _old_database(tmp_path)
    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, email, login_id, password_hash, role, must_change_password FROM users")
        ).fetchall()
    by_id = {r.id: r for r in rows}
    assert len(rows) == 6
    login_ids = [r.login_id for r in rows]
    assert len(set(login_ids)) == 6 and all(login_ids)
    assert by_id["u1"].login_id == "asha.admin"
    assert by_id["u2"].login_id == "sam"
    assert by_id["u3"].login_id == "sam2"  # same name, made unique, oldest keeps the plain one
    assert by_id["u4"].login_id == "sam3"
    assert by_id["u5"].login_id == "weirdtag"  # characters that are not allowed are dropped
    assert by_id["u6"].login_id == "user"  # nothing usable left: a neutral fallback
    # nothing else was touched
    assert by_id["u1"].password_hash == "scrypt$hash-one" and by_id["u1"].role == "super_admin"
    assert all(not r.must_change_password for r in rows)
    engine.dispose()


def test_upgrade_then_downgrade_returns_to_the_old_shape(tmp_path: Path) -> None:
    cfg, engine = _old_database(tmp_path)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0004")
    with engine.connect() as conn:
        columns = {r[1] for r in conn.execute(text("PRAGMA table_info(users)"))}
        count = conn.execute(text("SELECT count(*) FROM users")).scalar_one()
    assert "login_id" not in columns and "must_change_password" not in columns
    assert count == 6
    engine.dispose()


# ---- creating users ----


def test_new_users_get_a_login_id_from_their_email_and_duplicates_are_numbered(
    session: Session, settings: Settings
) -> None:
    first = add_user(session, settings, "Priya.Rao@apex.test", Role.EMPLOYEE)
    second = add_user(session, settings, "priya.rao@other.test", Role.EMPLOYEE)
    assert (first.login_id, second.login_id) == ("priya.rao", "priya.rao2")


def test_login_id_is_always_lower_case_short_and_clean(session: Session) -> None:
    assert repo.unique_login_id(session, " Ünï Co$de!! ") == "ncode"
    long = repo.unique_login_id(session, "x" * 80)
    assert long == "x" * 28
    assert repo.unique_login_id(session, "") == "user"


# ---- signing in ----


@pytest.fixture
def alice(session: Session, settings: Settings):  # noqa: ANN201
    return add_user(session, settings, "alice@apex.test", Role.EMPLOYEE, ["production"])


def test_sign_in_works_with_login_id_or_email_in_any_case(
    raw_client: TestClient,
    alice,  # noqa: ANN001
) -> None:
    for body in (
        {"login_id": "alice"},
        {"login_id": " ALICE "},
        {"login_id": "alice@apex.test"},  # an email typed into the same box
        {"email": "alice@apex.test"},  # the older request shape still works
    ):
        response = _login(raw_client, **body)
        assert response.status_code == 200, body
        assert response.json()["user"]["login_id"] == "alice"


def test_a_wrong_login_id_or_password_look_the_same(
    raw_client: TestClient,
    alice,  # noqa: ANN001
) -> None:
    unknown = _login(raw_client, login_id="nobody")
    wrong = raw_client.post(LOGIN, json={"login_id": "alice", "password": "wrong-password-1"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json() == {"detail": "invalid_credentials"}


def test_a_login_request_with_no_identifier_is_rejected(raw_client: TestClient) -> None:
    assert raw_client.post(LOGIN, json={"password": PASSWORD}).status_code == 422


def test_failures_count_together_whether_login_id_or_email_is_typed(
    raw_client: TestClient,
    alice,
    settings: Settings,  # noqa: ANN001
) -> None:
    """Alternating the two spellings must not give extra password guesses."""
    spellings = ["alice", "alice@apex.test", "ALICE", "alice@apex.test"]
    for i in range(settings.login_max_failures):
        bad = raw_client.post(
            LOGIN, json={"login_id": spellings[i % 4], "password": "wrong-password-1"}
        )
        assert bad.status_code == 401
    blocked = _login(raw_client, login_id="alice")  # even the right password now waits
    assert blocked.status_code == 429


def test_users_api_and_me_report_the_login_id(
    raw_client: TestClient, session: Session, settings: Settings
) -> None:
    add_user(session, settings, "boss@apex.test", Role.SUPER_ADMIN)
    token = _login(raw_client, login_id="boss").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert raw_client.get("/api/v1/auth/me", headers=headers).json()["login_id"] == "boss"
    listed = raw_client.get("/api/v1/users", headers=headers).json()
    assert [u["login_id"] for u in listed] == ["boss"]


def test_two_accounts_can_never_share_a_login_id(session: Session, settings: Settings) -> None:
    from sqlalchemy.exc import IntegrityError

    add_user(session, settings, "dup@apex.test", Role.EMPLOYEE)
    other = add_user(session, settings, "other@apex.test", Role.EMPLOYEE)
    other.login_id = "dup"
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
