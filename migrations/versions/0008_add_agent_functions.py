"""add agent functions and first message to agents

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add first-message fields to agents
    op.add_column("agents", sa.Column("first_message_enabled", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("agents", sa.Column("first_message", sa.Text(), nullable=True))

    # Create agent_functions table
    op.create_table(
        "agent_functions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("method", sa.String(10), nullable=False, server_default="POST"),
        sa.Column("url", sa.Text(), nullable=False, server_default=""),
        sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default="120000"),
        sa.Column("headers", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("query_params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("body_schema", sa.Text(), nullable=True),
        sa.Column("payload_args_only", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_agent_functions_agent_id", "agent_functions", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_functions_agent_id", "agent_functions")
    op.drop_table("agent_functions")
    op.drop_column("agents", "first_message")
    op.drop_column("agents", "first_message_enabled")
