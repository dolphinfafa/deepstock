"""replace enterprise WeChat with email alerts

Revision ID: 5d92c71f8e42
Revises: d046b2eba9b3
Create Date: 2026-10-05 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5d92c71f8e42"
down_revision: Union[str, Sequence[str], None] = "d046b2eba9b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("alerts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "delivered_email",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.drop_column("delivered_wechat")
    op.execute(
        sa.text(
            "DELETE FROM system_settings "
            "WHERE key IN ('wechat_configured', 'wechat_test_passed')"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("alerts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "delivered_wechat",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.drop_column("delivered_email")
    op.execute(
        sa.text(
            "DELETE FROM system_settings "
            "WHERE key IN ('email_configured', 'email_test_passed')"
        )
    )
