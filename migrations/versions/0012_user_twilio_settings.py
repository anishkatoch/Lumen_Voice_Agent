"""add user_twilio_settings table

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_twilio_settings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False, unique=True),
        sa.Column("account_sid", sa.String(100), nullable=True),
        sa.Column("auth_token", sa.String(100), nullable=True),
        sa.Column("webhook_base_url", sa.String(500), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_user_twilio_settings_user_id", "user_twilio_settings", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_twilio_settings_user_id", "user_twilio_settings")
    op.drop_table("user_twilio_settings")
