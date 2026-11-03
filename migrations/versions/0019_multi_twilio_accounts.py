"""allow multiple Twilio accounts per user

Drops the one-row-per-user constraint on user_twilio_settings so a user can
save several Twilio connections (e.g. separate client projects, prod vs
sandbox), adds a label + is_default flag to tell them apart, and lets each
twilio_phone_numbers row point at the specific account that owns it.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("user_twilio_settings_user_id_key", "user_twilio_settings", type_="unique")
    op.add_column("user_twilio_settings", sa.Column("label", sa.String(100), nullable=True))
    op.add_column(
        "user_twilio_settings",
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
    )
    # Each user's existing single row becomes their default account - no
    # behavior change for anyone who never adds a second account.
    op.execute("UPDATE user_twilio_settings SET is_default = true, label = 'Default' WHERE label IS NULL")

    op.add_column(
        "twilio_phone_numbers",
        sa.Column("twilio_account_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.create_index(
        "ix_twilio_phone_numbers_twilio_account_id",
        "twilio_phone_numbers",
        ["twilio_account_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_twilio_phone_numbers_twilio_account_id", "twilio_phone_numbers")
    op.drop_column("twilio_phone_numbers", "twilio_account_id")
    op.drop_column("user_twilio_settings", "is_default")
    op.drop_column("user_twilio_settings", "label")
    op.create_unique_constraint("user_twilio_settings_user_id_key", "user_twilio_settings", ["user_id"])
