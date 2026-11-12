"""add sms_email_requests: email capture by SMS reply during a phone call

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-12

One row per "please text us your email" request an agent sends mid-call.
The inbound SMS webhook matches the caller's reply back to the newest
pending row for that (caller_number, agent_number) pair and stores the
reply plus the parsed email, so the live call (and anything after it)
can pick the address up without the caller having to spell it out.
Numbers are stored digits-only because Plivo's voice and message
webhooks disagree about the leading '+'.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sms_email_requests",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("provider", sa.String(20), nullable=False, server_default="plivo"),
        sa.Column("agent_number", sa.String(32), nullable=False),
        sa.Column("caller_number", sa.String(32), nullable=False),
        sa.Column("message_uuid", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("reply_text", sa.Text(), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sms_email_requests_numbers", "sms_email_requests", ["caller_number", "agent_number"])
    op.create_index("ix_sms_email_requests_conversation_id", "sms_email_requests", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_sms_email_requests_conversation_id", "sms_email_requests")
    op.drop_index("ix_sms_email_requests_numbers", "sms_email_requests")
    op.drop_table("sms_email_requests")
