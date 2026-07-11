"""add sentiment columns to conversations

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("sentiment",         sa.String(20),  nullable=True))
    op.add_column("conversations", sa.Column("dominant_emotion",  sa.String(50),  nullable=True))
    op.add_column("conversations", sa.Column("sentiment_score",   sa.Float(),     nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "sentiment_score")
    op.drop_column("conversations", "dominant_emotion")
    op.drop_column("conversations", "sentiment")
