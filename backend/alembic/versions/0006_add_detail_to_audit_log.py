"""add detail to audit log

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-31 16:00:00.000000

A short code saying what an action changed (for example "role:super_admin"). Never free text,
never a secret.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("detail", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("audit_log") as batch:
        batch.drop_column("detail")
