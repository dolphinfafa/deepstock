"""Versioned market datasets and research lineage."""
from alembic import op
import sqlalchemy as sa

revision = "b872cd0f613e"
down_revision = "7b3894ea2091"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("data_versions",
        sa.Column("id", sa.String(64), primary_key=True), sa.Column("node", sa.String(120), nullable=False),
        sa.Column("market", sa.String(10), nullable=False), sa.Column("provider", sa.String(120), nullable=False),
        sa.Column("source_name", sa.String(250), nullable=False), sa.Column("status", sa.String(30), nullable=False),
        sa.Column("data_start", sa.String(10)), sa.Column("data_end", sa.String(10)),
        sa.Column("row_count", sa.Integer(), nullable=False), sa.Column("preview_allowed", sa.Boolean(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False))
    for col in ["node", "market", "provider", "status"]:
        op.create_index(f"ix_data_versions_{col}", "data_versions", [col])
    op.create_table("research_data_inputs", sa.Column("run_id", sa.String(100), sa.ForeignKey("research_runs.id", ondelete="CASCADE"), primary_key=True),
                    sa.Column("version_id", sa.String(64), sa.ForeignKey("data_versions.id"), primary_key=True))


def downgrade():
    op.drop_table("research_data_inputs")
    op.drop_table("data_versions")
