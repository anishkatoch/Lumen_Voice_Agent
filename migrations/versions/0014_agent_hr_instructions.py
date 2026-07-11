"""add hr_instructions column to agents

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("hr_instructions", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "hr_instructions")
