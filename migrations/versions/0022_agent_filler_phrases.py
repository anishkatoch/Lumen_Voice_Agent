"""add agents.filler_phrases column

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("filler_phrases", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "filler_phrases")
