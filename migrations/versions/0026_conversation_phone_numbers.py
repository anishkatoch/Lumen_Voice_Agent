"""add to_number/from_number columns to conversations

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-11

Additive-only: two nullable columns on the existing conversations table,
recording the dialed number (to_number) and the number the call was placed
from (from_number) for phone calls. Browser voice sessions leave both null.
Existing rows are not backfilled - this is only known going forward.
"""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("to_number", sa.String(32), nullable=True))
    op.add_column("conversations", sa.Column("from_number", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "from_number")
    op.drop_column("conversations", "to_number")
