"""add twilio_phone_numbers table

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "twilio_phone_numbers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("phone_number", sa.String(30), nullable=False),
        sa.Column("friendly_name", sa.String(100), nullable=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_twilio_phone_numbers_user_id",
        "twilio_phone_numbers",
        ["user_id"],
    )
    op.create_index(
        "ix_twilio_phone_numbers_phone_number",
        "twilio_phone_numbers",
        ["phone_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_twilio_phone_numbers_phone_number", "twilio_phone_numbers")
    op.drop_index("ix_twilio_phone_numbers_user_id", "twilio_phone_numbers")
    op.drop_table("twilio_phone_numbers")
