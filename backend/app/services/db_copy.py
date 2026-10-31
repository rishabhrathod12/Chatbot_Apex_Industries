"""Copy every table from one database into another, for moving from SQLite to PostgreSQL.

The target must already have the schema (`alembic upgrade head`) and be empty. The copy runs in a
single transaction, so a failure leaves the target untouched. Row counts are checked at the end.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Engine, Integer, Table, func, insert, select, text

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.database import Base

_BATCH = 500


class CopyError(Exception):
    """The copy could not be done. `reason` is a short code, never row content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _count(engine: Engine, table: Table) -> int:
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


def _fix_row(table: Table, row: dict[str, Any]) -> dict[str, Any]:
    """SQLite returns times without a zone. We only ever store UTC, so say so explicitly;
    otherwise PostgreSQL would read them in the server's own time zone."""
    for column in table.columns:
        value = row[column.name]
        if isinstance(column.type, DateTime) and isinstance(value, datetime) and not value.tzinfo:
            row[column.name] = value.replace(tzinfo=UTC)
    return row


def _reset_sequences(engine: Engine) -> None:
    """Rows copied with their old ids don't advance PostgreSQL's counters; do it by hand."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            pk = list(table.primary_key.columns)
            if len(pk) == 1 and isinstance(pk[0].type, Integer):
                name, col = table.name, pk[0].name
                conn.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{name}', '{col}'), "  # noqa: S608
                        f"COALESCE(MAX({col}), 1), MAX({col}) IS NOT NULL) FROM {name}"
                    )
                )


def copy_database(source: Engine, target: Engine) -> dict[str, int]:
    """Copy all tables. Returns rows copied per table."""
    tables = Base.metadata.sorted_tables  # parents before children
    for table in tables:
        try:
            if _count(target, table):
                raise CopyError("target_not_empty")
        except CopyError:
            raise
        except Exception as exc:  # missing table: the schema was not created
            raise CopyError("target_schema_missing") from exc

    copied: dict[str, int] = {}
    with source.connect() as src, target.begin() as dst:
        for table in tables:
            rows = src.execute(select(table)).mappings()
            total = 0
            while batch := rows.fetchmany(_BATCH):
                dst.execute(insert(table), [_fix_row(table, dict(r)) for r in batch])
                total += len(batch)
            copied[table.name] = total

    _reset_sequences(target)
    for table in tables:
        if _count(target, table) != copied[table.name]:
            raise CopyError("count_mismatch")
    return copied
