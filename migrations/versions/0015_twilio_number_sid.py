"""add twilio_number_sid to twilio_phone_numbers

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("twilio_phone_numbers", sa.Column("twilio_number_sid", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("twilio_phone_numbers", "twilio_number_sid")
