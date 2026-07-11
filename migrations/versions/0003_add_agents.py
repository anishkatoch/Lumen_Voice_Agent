"""add agents table and agent_id to conversations

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id",                   UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id",              UUID(as_uuid=False), nullable=False),
        sa.Column("name",                 sa.String(100),      nullable=False),
        sa.Column("description",          sa.Text(),           nullable=True),
        sa.Column("system_prompt",        sa.Text(),           nullable=True),
        sa.Column("barge_in_sensitivity", sa.Float(),          nullable=False, server_default="0.20"),
        sa.Column("created_at",           sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at",           sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_agents_user_id", "agents", ["user_id"])

    op.add_column("conversations", sa.Column("agent_id", UUID(as_uuid=False), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "agent_id")
    op.drop_index("ix_agents_user_id", table_name="agents")
    op.drop_table("agents")
