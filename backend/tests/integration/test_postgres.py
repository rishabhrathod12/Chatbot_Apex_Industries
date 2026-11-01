"""Run against a real PostgreSQL. Skipped unless TEST_POSTGRES_URL is set, for example

    $env:TEST_POSTGRES_URL = "postgresql://apex_test:PASSWORD@localhost:5432/apex_test"
    pytest backend/tests/integration/test_postgres.py

Each test gets its own throwaway schema inside that database and drops it afterwards, so the
database itself can be any empty or scratch one. Never point this at data you want to keep.
"""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import Settings, normalize_database_url
from app.core.database import Base, get_engine, get_session_factory
from app.core.permissions import Role
from app.models import AuditLog, User
from app.services import db_copy
from tests.conftest import SAMPLE_DOCS, add_stored_document, add_user

BACKEND = Path(__file__).resolve().parents[2]
BASE_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(not BASE_URL, reason="TEST_POSTGRES_URL is not set")


def _in_schema(url: str, schema: str) -> URL:
    return make_url(normalize_database_url(url)).update_query_pairs(
        [("options", f"-csearch_path={schema}")]
    )


def _as_utc(value: datetime) -> datetime:
    return (value.astimezone(UTC) if value.tzinfo else value).replace(tzinfo=None)


@pytest.fixture
def pg_url() -> Iterator[str]:
    schema = f"apex_t_{uuid.uuid4().hex[:10]}"
    admin = create_engine(normalize_database_url(BASE_URL), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = _in_schema(BASE_URL, schema).render_as_string(hide_password=False)
    yield url
    get_engine(url).dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


def _alembic(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))  # ini files treat % specially
    return cfg


def test_migrations_up_down_up(pg_url: str) -> None:
    cfg = _alembic(pg_url)
    command.upgrade(cfg, "head")
    engine = get_engine(pg_url)
    assert {"users", "documents", "conversations", "messages", "audit_log"} <= set(
        inspect(engine).get_table_names()
    )
    command.downgrade(cfg, "base")
    assert "documents" not in inspect(engine).get_table_names()
    command.upgrade(cfg, "head")
    assert "documents" in inspect(engine).get_table_names()


def test_migrated_schema_matches_the_models(pg_url: str) -> None:
    command.upgrade(_alembic(pg_url), "head")
    with get_engine(pg_url).connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []


def test_partial_unique_index_lets_a_deleted_document_be_uploaded_again(
    pg_url: str, settings: Settings
) -> None:
    from app.repositories import documents as repo

    command.upgrade(_alembic(pg_url), "head")
    settings.database_url = pg_url
    with get_session_factory(pg_url)() as db:
        first = add_stored_document(db, settings, b"%PDF-1.4 x", name="same")
        repo.soft_delete(db, first)
        again = add_stored_document(db, settings, b"%PDF-1.4 x", name="same")
        assert again.id != first.id


def test_copy_from_sqlite_keeps_data_times_and_id_counters(
    pg_url: str, session: Session, settings: Settings
) -> None:
    add_user(session, settings, "admin@apex.test", Role.SUPER_ADMIN)
    add_user(session, settings, "emp@apex.test", Role.EMPLOYEE, ["production"])
    add_stored_document(
        session, settings, SAMPLE_DOCS / "01_Machine_Operation_Manual.pdf", name="manual"
    )
    command.upgrade(_alembic(pg_url), "head")
    target = get_engine(pg_url)

    copied = db_copy.copy_database(get_engine(settings.database_url), target)

    assert copied["users"] == 2
    with get_session_factory(pg_url)() as db:
        users = db.scalars(select(User).order_by(User.email)).all()
        assert [u.email for u in users] == ["admin@apex.test", "emp@apex.test"]
        # timestamptz comes back aware, and equal to what SQLite held (which was UTC)
        mine = session.scalars(select(User).where(User.email == "admin@apex.test")).one()
        assert users[0].created_at.utcoffset() is not None
        # PostgreSQL returns the time in the session's zone and SQLite returns it naive (UTC):
        # compare the instant, in UTC
        assert _as_utc(users[0].created_at) == _as_utc(mine.created_at)
        # the integer id counter must continue after the copied rows instead of restarting at 1
        highest = db.execute(select(func.max(AuditLog.id))).scalar_one()
        db.add(AuditLog(action="probe", outcome="ok"))
        db.commit()
        assert db.execute(select(func.max(AuditLog.id))).scalar_one() == highest + 1
