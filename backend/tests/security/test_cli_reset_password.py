"""`python -m app.cli reset-password`: a way back in when a password is lost or has been exposed."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import cli
from app.core.config import Settings
from app.core.permissions import Role
from app.models import AuditLog, RefreshToken, User
from tests.conftest import PASSWORD, add_user

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"


@pytest.fixture
def boss(
    raw_client: TestClient, session: Session, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> User:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return add_user(session, settings, "boss@apex.test", Role.SUPER_ADMIN)


def _password_in(output: str) -> str:
    found = re.search(r"^\s{4}(\S+)\s*$", output, re.MULTILINE)
    assert found, output
    return found.group(1)


def _sign_in(client: TestClient, who: str, password: str):  # noqa: ANN202
    return client.post(LOGIN, json={"login_id": who, "password": password})


def test_the_reset_prints_a_one_time_password_that_must_be_changed(
    raw_client: TestClient, boss: User, capsys: pytest.CaptureFixture[str]
) -> None:
    pair = _sign_in(raw_client, "boss", PASSWORD).json()  # a session from before the reset

    assert cli.main(["reset-password", "--login-id", "boss"]) == 0
    new = _password_in(capsys.readouterr().out)

    assert len(new) >= 16 and new != PASSWORD
    assert _sign_in(raw_client, "boss", PASSWORD).status_code == 401  # the exposed one is dead
    again = _sign_in(raw_client, "boss", new)
    assert again.status_code == 200
    assert again.json()["user"]["must_change_password"] is True
    stale = raw_client.post(REFRESH, json={"refresh_token": pair["refresh_token"]})
    assert stale.status_code == 401  # their old sessions ended
    blocked = raw_client.get(
        "/api/v1/documents", headers={"Authorization": f"Bearer {again.json()['access_token']}"}
    )
    assert (blocked.status_code, blocked.json()["detail"]) == (403, "password_change_required")


def test_the_whole_way_through_to_a_password_only_they_know(
    raw_client: TestClient, boss: User, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["reset-password", "--login-id", "boss@apex.test"])  # an email works as well
    one_time = _password_in(capsys.readouterr().out)
    headers = {
        "Authorization": f"Bearer {_sign_in(raw_client, 'boss', one_time).json()['access_token']}"
    }
    chosen = "My-Own-Choice-Pass-77"
    changed = raw_client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": one_time, "new_password": chosen},
    )
    assert changed.status_code == 200 and changed.json()["user"]["must_change_password"] is False
    assert _sign_in(raw_client, "boss", one_time).status_code == 401
    assert _sign_in(raw_client, "boss", chosen).status_code == 200


def test_the_password_appears_once_and_nothing_secret_is_stored_or_logged(
    boss: User,
    session: Session,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    cli.main(["reset-password", "--login-id", "boss"])
    captured = capsys.readouterr()
    new = _password_in(captured.out)

    assert captured.out.count(new) == 1 and new not in captured.err
    assert new not in caplog.text
    session.expire_all()
    user = session.get(User, boss.id)
    assert user is not None and user.password_hash.startswith("scrypt$")
    assert user.password_hash not in captured.out and new not in user.password_hash
    for table in ("users", "audit_log", "refresh_tokens"):
        assert new not in repr(session.execute(text(f"SELECT * FROM {table}")).fetchall())  # noqa: S608


def test_the_reset_is_recorded_in_the_audit_log(boss: User, session: Session) -> None:
    cli.main(["reset-password", "--login-id", "boss"])
    session.expire_all()
    row = session.scalars(select(AuditLog).where(AuditLog.action == "password_reset")).one()
    assert (row.outcome, row.user_id, row.target_type, row.target_id) == (
        "ok",
        None,  # nobody was signed in: it was done on the server itself
        "user",
        boss.id,
    )


def test_every_session_of_the_account_ends(
    raw_client: TestClient, boss: User, session: Session
) -> None:
    for _ in range(3):
        _sign_in(raw_client, "boss", PASSWORD)
    cli.main(["reset-password", "--login-id", "boss"])
    session.expire_all()
    live = session.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == boss.id, RefreshToken.revoked_at.is_(None)
        )
    ).all()
    assert live == []


def test_an_unknown_account_changes_nothing(
    boss: User, session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    before = boss.password_hash
    assert cli.main(["reset-password", "--login-id", "nobody"]) == 1
    assert "No account matches" in capsys.readouterr().err
    session.expire_all()
    assert session.get(User, boss.id).password_hash == before  # type: ignore[union-attr]
    assert (
        session.scalars(select(AuditLog).where(AuditLog.action == "password_reset")).first() is None
    )


def test_a_chosen_password_must_still_be_long_enough(
    boss: User,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("RESET_PW", "short")
    before = boss.password_hash
    assert cli.main(["reset-password", "--login-id", "boss", "--password-env", "RESET_PW"]) == 1
    assert "password_too_short" in capsys.readouterr().err
    session.expire_all()
    assert session.get(User, boss.id).password_hash == before  # type: ignore[union-attr]


def test_a_chosen_password_is_accepted_but_still_has_to_be_changed(
    raw_client: TestClient, boss: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RESET_PW", "Chosen-By-The-Operator-1")
    assert cli.main(["reset-password", "--login-id", "boss", "--password-env", "RESET_PW"]) == 0
    reply = _sign_in(raw_client, "boss", "Chosen-By-The-Operator-1")
    assert reply.status_code == 200 and reply.json()["user"]["must_change_password"] is True


def test_an_empty_environment_variable_is_refused(
    boss: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RESET_PW", raising=False)
    with pytest.raises(SystemExit):
        cli.main(["reset-password", "--login-id", "boss", "--password-env", "RESET_PW"])
