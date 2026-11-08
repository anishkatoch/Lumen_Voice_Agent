"""add duration and cost columns to conversations

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-09

Additive-only: two nullable columns on the existing conversations table.
duration_seconds is set when a call/session ends (created_at -> end time).
cost_usd is an estimate (duration_seconds * CALL_COST_PER_MINUTE_USD), not a
billed amount from any provider.
"""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("duration_seconds", sa.Integer(), nullable=True))
    op.add_column("conversations", sa.Column("cost_usd", sa.Numeric(10, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "cost_usd")
    op.drop_column("conversations", "duration_seconds")
