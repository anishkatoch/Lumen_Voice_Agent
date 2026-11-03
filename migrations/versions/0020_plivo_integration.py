"""add Plivo as a second telephony provider

Mirrors user_twilio_settings / twilio_phone_numbers so a user can connect
Plivo account(s) alongside Twilio and attach a Plivo number to any agent.
Which provider an agent uses for a given call is decided purely by which
number (Twilio or Plivo) is attached to it - no new column on `agents`.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_plivo_settings",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("label", sa.String(100), nullable=True),
        sa.Column("auth_id", sa.String(100), nullable=False, server_default=""),
        sa.Column("auth_token", sa.String(200), nullable=False, server_default=""),
        sa.Column("webhook_base_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_user_plivo_settings_user_id", "user_plivo_settings", ["user_id"])

    op.create_table(
        "plivo_phone_numbers",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("phone_number", sa.String(32), nullable=False),
        sa.Column("friendly_name", sa.String(200), nullable=True),
        sa.Column("plivo_account_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("plivo_app_id", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_plivo_phone_numbers_user_id", "plivo_phone_numbers", ["user_id"])
    op.create_index("ix_plivo_phone_numbers_agent_id", "plivo_phone_numbers", ["agent_id"])
    op.create_index("ix_plivo_phone_numbers_plivo_account_id", "plivo_phone_numbers", ["plivo_account_id"])


def downgrade() -> None:
    op.drop_index("ix_plivo_phone_numbers_plivo_account_id", "plivo_phone_numbers")
    op.drop_index("ix_plivo_phone_numbers_agent_id", "plivo_phone_numbers")
    op.drop_index("ix_plivo_phone_numbers_user_id", "plivo_phone_numbers")
    op.drop_table("plivo_phone_numbers")
    op.drop_index("ix_user_plivo_settings_user_id", "user_plivo_settings")
    op.drop_table("user_plivo_settings")
