"""add per-agent smart_response_enabled toggle

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-27

Lets an agent match answer length to what was actually asked, offer
relevant follow-up questions, and ask a clarifying question instead of
guessing on an ambiguous request. Default true so every existing agent
gets this behavior immediately; toggle off per-agent if not wanted.
"""
from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("smart_response_enabled", sa.Boolean(), nullable=False, server_default="true"))


def downgrade() -> None:
    op.drop_column("agents", "smart_response_enabled")
