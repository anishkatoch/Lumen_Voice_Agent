"""add ui fields: agent is_active, kb_instructions; function parameters and trigger

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("agents", sa.Column("kb_instructions", sa.Text(), nullable=True))

    op.add_column("agent_functions", sa.Column("parameters", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.add_column("agent_functions", sa.Column("trigger_type", sa.String(50), nullable=False, server_default="llm_tool_call"))


def downgrade() -> None:
    op.drop_column("agent_functions", "trigger_type")
    op.drop_column("agent_functions", "parameters")
    op.drop_column("agents", "kb_instructions")
    op.drop_column("agents", "is_active")
