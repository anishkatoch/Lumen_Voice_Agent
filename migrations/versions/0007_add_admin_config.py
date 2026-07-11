"""add admin_config table for runtime provider/limit settings

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_config",
        sa.Column("key",        sa.String(100), primary_key=True),
        sa.Column("value",      sa.Text(),      nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("admin_config")
