"""add direction column to twilio and plivo phone numbers

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-13

Per-number call-direction setting: 'both' (default, today's behavior),
'inbound' (answers calls but is never picked as an outbound caller ID), or
'outbound' (used as outbound caller ID but inbound calls to it are not
routed to an agent). Additive with server_default='both', so every existing
number keeps behaving exactly as before.
"""
from alembic import op
import sqlalchemy as sa

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("twilio_phone_numbers", sa.Column("direction", sa.String(10), nullable=False, server_default="both"))
    op.add_column("plivo_phone_numbers", sa.Column("direction", sa.String(10), nullable=False, server_default="both"))


def downgrade() -> None:
    op.drop_column("plivo_phone_numbers", "direction")
    op.drop_column("twilio_phone_numbers", "direction")
