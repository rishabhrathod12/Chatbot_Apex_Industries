"""add supersedes_id to documents

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-31 20:00:00.000000

A new version of a company document points at the document it replaces.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("supersedes_id", sa.String(length=32), nullable=True))
    op.create_index("ix_documents_supersedes", "documents", ["supersedes_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_documents_supersedes", table_name="documents")
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("supersedes_id")
