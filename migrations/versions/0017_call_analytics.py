"""add call analytics columns to conversations

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-27

Additive-only: three nullable columns on the existing conversations table.
Existing rows get call_status='completed' (they already happened and are not
being re-evaluated), so nothing already in Sessions/Call Analytics changes
appearance. New rows explicitly set call_status going forward.
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("call_status", sa.String(20), nullable=True, server_default="completed"))
    op.add_column("conversations", sa.Column("failure_reason", sa.Text(), nullable=True))
    op.add_column("conversations", sa.Column("call_sid", sa.String(64), nullable=True))
    op.create_index("ix_conversations_call_sid", "conversations", ["call_sid"])


def downgrade() -> None:
    op.drop_index("ix_conversations_call_sid", "conversations")
    op.drop_column("conversations", "call_sid")
    op.drop_column("conversations", "failure_reason")
    op.drop_column("conversations", "call_status")
