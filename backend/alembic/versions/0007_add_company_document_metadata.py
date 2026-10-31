"""add company document metadata and archiving

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-31 18:00:00.000000

description / document_type / title_override / version_override are what an admin typed at
upload. They are kept apart from `title` and `doc_version`, which come from the file itself, so
re-indexing never overwrites them. archived_at is set while a document is archived.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("document_type", sa.String(length=32), nullable=True))
    op.add_column("documents", sa.Column("title_override", sa.String(length=255), nullable=True))
    op.add_column("documents", sa.Column("version_override", sa.String(length=32), nullable=True))
    op.add_column("documents", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("archived_at")
        batch.drop_column("version_override")
        batch.drop_column("title_override")
        batch.drop_column("document_type")
        batch.drop_column("description")
