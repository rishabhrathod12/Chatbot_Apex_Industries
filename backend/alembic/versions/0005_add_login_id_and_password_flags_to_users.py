"""add login_id and password flags to users

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-31 14:00:00.000000

Existing users get a Login ID taken from the part of their email before the "@" (lower-case,
letters, digits, "." "_" "-"), made unique with a number if two people share it. Passwords and
every other column are left alone.
"""

import re
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MAX = 32


def _login_id_for(email: str, taken: set[str]) -> str:
    # Kept inside the migration on purpose: a migration must keep working even if the
    # application's own rules change later.
    base = re.sub(r"[^a-z0-9._-]", "", email.split("@")[0].lower())[: _MAX - 4] or "user"
    candidate, number = base, 1
    while candidate in taken:
        number += 1
        candidate = f"{base}{number}"
    taken.add(candidate)
    return candidate


def upgrade() -> None:
    op.add_column("users", sa.Column("login_id", sa.String(length=32), nullable=True))
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("created_by", sa.String(length=32), nullable=True))

    bind = op.get_bind()
    taken: set[str] = set()
    for user_id, email in bind.execute(
        sa.text("SELECT id, email FROM users ORDER BY created_at, email")
    ).fetchall():
        bind.execute(
            sa.text("UPDATE users SET login_id = :login_id WHERE id = :id"),
            {"login_id": _login_id_for(email, taken), "id": user_id},
        )

    with op.batch_alter_table("users") as batch:
        batch.alter_column("login_id", existing_type=sa.String(length=32), nullable=False)
    op.create_index("ix_users_login_id", "users", ["login_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_login_id", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("created_by")
        batch.drop_column("password_changed_at")
        batch.drop_column("must_change_password")
        batch.drop_column("login_id")
