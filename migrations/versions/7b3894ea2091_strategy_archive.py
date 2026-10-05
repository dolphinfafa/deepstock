"""Add an independent frozen-strategy archive.

Revision ID: 7b3894ea2091
Revises: 5d92c71f8e42
"""
from alembic import op
import sqlalchemy as sa

revision = "7b3894ea2091"
down_revision = "5d92c71f8e42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("strategies") as batch:
        batch.add_column(sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("archive_reason", sa.Text(), nullable=True))
        batch.create_index("ix_strategies_is_archived", ["is_archived"])


def downgrade() -> None:
    with op.batch_alter_table("strategies") as batch:
        batch.drop_index("ix_strategies_is_archived")
        batch.drop_column("archive_reason")
        batch.drop_column("archived_at")
        batch.drop_column("is_archived")
