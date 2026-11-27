"""add agent_feedback table for user-submitted issue reports and feedback

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-27

Lets a signed-in user report an issue or leave general feedback from the
console, optionally pointing at a specific agent (agent_id is nullable -
feedback doesn't have to be about any one agent). agent_name is captured
at submission time so the admin portal still shows what the report was
about even if that agent is later renamed or deleted (agent_id itself is
ON DELETE SET NULL, matching how other optional agent references in this
schema behave - the report is kept, only the live link is dropped).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_feedback",
        sa.Column("id",          UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id",     UUID(as_uuid=False), nullable=False),
        sa.Column("user_email",  sa.String(255),      nullable=False),
        sa.Column("agent_id",    UUID(as_uuid=False), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("agent_name",  sa.String(200),       nullable=True),
        sa.Column("type",        sa.String(10),        nullable=False, server_default="issue"),  # issue | feedback
        sa.Column("message",     sa.Text(),            nullable=False),
        sa.Column("status",      sa.String(12),        nullable=False, server_default="open"),   # open | resolved
        sa.Column("created_at",  sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_by", UUID(as_uuid=False), nullable=True),
    )
    op.create_index("ix_agent_feedback_user_id", "agent_feedback", ["user_id"])
    op.create_index("ix_agent_feedback_status",  "agent_feedback", ["status"])
    op.create_index("ix_agent_feedback_agent_id", "agent_feedback", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_feedback_agent_id", table_name="agent_feedback")
    op.drop_index("ix_agent_feedback_status",   table_name="agent_feedback")
    op.drop_index("ix_agent_feedback_user_id",  table_name="agent_feedback")
    op.drop_table("agent_feedback")
