"""add Google Calendar meeting booking: user_integrations, meetings, agent columns

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-12

Three additive pieces for the Meeting Assistant feature:
- user_integrations: one row per (user, provider) holding the OAuth tokens
  for that user's connected Google Calendar. Tokens are stored encrypted
  (see app/integrations/integrations_repo.py) - the columns are plain text
  on purpose, the ciphertext is opaque to the DB.
- meetings: an audit/history row per booking made by an agent on a call,
  so the owner can see what was booked without opening Google Calendar.
- agents.*: per-agent booking settings. meeting_booking_enabled gates the
  check_availability/book_meeting tools exactly like kb_instructions gates
  search_knowledge_base - off by default so every existing agent behaves
  unchanged.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_integrations",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_email", sa.String(320), nullable=True),
        sa.Column("calendar_id", sa.String(320), nullable=False, server_default="primary"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_user_integrations_user_id", "user_integrations", ["user_id"])
    op.create_unique_constraint(
        "uq_user_integrations_user_provider", "user_integrations", ["user_id", "provider"]
    )

    op.create_table(
        "meetings",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("google_event_id", sa.String(200), nullable=True),
        sa.Column("attendee_name", sa.String(200), nullable=True),
        sa.Column("attendee_email", sa.String(320), nullable=True),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meet_link", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="booked"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_meetings_user_id", "meetings", ["user_id"])
    op.create_index("ix_meetings_agent_id", "meetings", ["agent_id"])

    op.add_column("agents", sa.Column("meeting_booking_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("agents", sa.Column("meeting_duration_minutes", sa.Integer(), nullable=False, server_default="30"))
    op.add_column("agents", sa.Column("meeting_buffer_minutes", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("agents", sa.Column("meeting_timezone", sa.String(100), nullable=False, server_default="UTC"))
    op.add_column("agents", sa.Column("booking_hours_start", sa.String(5), nullable=False, server_default="09:00"))
    op.add_column("agents", sa.Column("booking_hours_end", sa.String(5), nullable=False, server_default="17:00"))
    op.add_column("agents", sa.Column("meeting_instructions", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "meeting_instructions")
    op.drop_column("agents", "booking_hours_end")
    op.drop_column("agents", "booking_hours_start")
    op.drop_column("agents", "meeting_timezone")
    op.drop_column("agents", "meeting_buffer_minutes")
    op.drop_column("agents", "meeting_duration_minutes")
    op.drop_column("agents", "meeting_booking_enabled")

    op.drop_index("ix_meetings_agent_id", "meetings")
    op.drop_index("ix_meetings_user_id", "meetings")
    op.drop_table("meetings")

    op.drop_constraint("uq_user_integrations_user_provider", "user_integrations", type_="unique")
    op.drop_index("ix_user_integrations_user_id", "user_integrations")
    op.drop_table("user_integrations")
