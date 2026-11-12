"""add retry tracking columns to hr_interviews

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-12

Additive-only: standalone HR interview calls had no retry concept at all
(one no-answer/failure and the interview was permanently "failed"), unlike
hr_campaign_candidates which already tracks attempt_count/last_attempt_at.
attempt_count mirrors that column's convention (incremented on every dial,
including the first). next_attempt_at is set only while a retry is pending
(status back to 'pending') and is otherwise null - get_due_interviews()
checks it in addition to the original scheduled_at/call_lead_minutes window.
"""
from alembic import op
import sqlalchemy as sa

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("hr_interviews", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("hr_interviews", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("hr_interviews", "next_attempt_at")
    op.drop_column("hr_interviews", "attempt_count")
