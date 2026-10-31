"""Checks that hold on any database: migrations match the models, and the copy tool is safe.

test_postgres.py repeats the important ones against a real PostgreSQL (when one is configured).
"""

from datetime import UTC
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from alembic import command
from app.core.config import Settings, normalize_database_url
from app.core.database import Base, get_engine
from app.core.permissions import Role
from app.models import AuditLog, Document, User
from app.services import db_copy
from tests.conftest import SAMPLE_DOCS, add_stored_document, add_user

BACKEND = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgresql://u:p@localhost/apex", "postgresql+psycopg://u:p@localhost/apex"),
        ("postgres://u:p@localhost/apex", "postgresql+psycopg://u:p@localhost/apex"),
        ("postgresql+psycopg://u@h/d", "postgresql+psycopg://u@h/d"),
        ("sqlite:///./storage/app.db", "sqlite:///./storage/app.db"),
    ],
)
def test_database_url_is_pinned_to_the_psycopg_driver(given: str, expected: str) -> None:
    assert normalize_database_url(given) == expected
    assert Settings(_env_file=None, database_url=given).database_url == expected  # type: ignore[call-arg]


@pytest.mark.parametrize("bad", ["mysql://u@h/d", "storage/app.db", ""])
def test_unsupported_database_urls_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(_env_file=None, database_url=bad)  # type: ignore[call-arg]


def test_migrations_produce_exactly_the_schema_the_models_describe(tmp_path: Path) -> None:
    """If a model changes without a migration (or the reverse), a real deployment breaks."""
    url = f"sqlite:///{tmp_path / 'drift.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


def _fill(session: Session, settings: Settings) -> None:
    add_user(session, settings, "admin@apex.test", Role.SUPER_ADMIN)
    add_user(session, settings, "emp@apex.test", Role.EMPLOYEE, ["production"])
    add_stored_document(
        session, settings, SAMPLE_DOCS / "01_Machine_Operation_Manual.pdf", name="manual"
    )


def _migrated_engine(tmp_path: Path, name: str):  # noqa: ANN202
    url = f"sqlite:///{tmp_path / name}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return get_engine(url)


def test_copy_moves_every_row_and_keeps_times_in_utc(
    session: Session, settings: Settings, tmp_path: Path
) -> None:
    _fill(session, settings)
    source = get_engine(settings.database_url)
    target = _migrated_engine(tmp_path, "target.db")

    copied = db_copy.copy_database(source, target)

    assert copied["users"] == 2 and copied["documents"] == 1
    assert copied["audit_log"] >= 1
    with target.connect() as conn:
        for table in Base.metadata.sorted_tables:
            n = conn.execute(select(func.count()).select_from(table)).scalar_one()
            assert n == copied[table.name]
        before = session.scalars(select(User).order_by(User.email)).all()
        after = conn.execute(select(User.email, User.created_at).order_by(User.email)).all()
    assert [u.email for u in before] == [row.email for row in after]
    # SQLite hands back naive times; the copy must not shift them.
    assert before[0].created_at.replace(tzinfo=UTC) == after[0].created_at.replace(tzinfo=UTC)


def test_copy_refuses_a_target_that_already_has_data(
    session: Session, settings: Settings, tmp_path: Path
) -> None:
    _fill(session, settings)
    source = get_engine(settings.database_url)
    target = _migrated_engine(tmp_path, "full.db")
    db_copy.copy_database(source, target)

    with pytest.raises(db_copy.CopyError) as err:
        db_copy.copy_database(source, target)
    assert err.value.reason == "target_not_empty"


def test_copy_refuses_a_target_without_the_schema(
    session: Session, settings: Settings, tmp_path: Path
) -> None:
    _fill(session, settings)
    bare = get_engine(f"sqlite:///{tmp_path / 'bare.db'}")
    with pytest.raises(db_copy.CopyError) as err:
        db_copy.copy_database(get_engine(settings.database_url), bare)
    assert err.value.reason == "target_schema_missing"


def test_a_failed_copy_leaves_the_target_empty(
    session: Session, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fill(session, settings)
    target = _migrated_engine(tmp_path, "atomic.db")
    real = db_copy._fix_row
    calls = {"n": 0}

    def explode_on_the_third_row(table, row):  # type: ignore[no-untyped-def]  # noqa: ANN001, ANN202
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return real(table, row)

    monkeypatch.setattr(db_copy, "_fix_row", explode_on_the_third_row)
    with pytest.raises(RuntimeError):
        db_copy.copy_database(get_engine(settings.database_url), target)

    with target.connect() as conn:
        assert conn.execute(select(func.count()).select_from(Document)).scalar_one() == 0
        assert conn.execute(select(func.count()).select_from(AuditLog)).scalar_one() == 0
        assert conn.execute(text("select count(*) from users")).scalar_one() == 0
