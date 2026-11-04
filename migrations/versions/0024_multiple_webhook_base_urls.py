"""allow saving more than one server base URL per Twilio/Plivo account

webhook_base_url stays as the single "active" URL every existing call-path
already reads (TwiML/answer-URL building, status callbacks, etc.) - this
just adds a column to remember the full list the person has typed in
(e.g. a real domain plus a couple of ngrok URLs), so switching the active
one doesn't mean retyping a URL you already had.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_twilio_settings",
        sa.Column("webhook_base_urls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "user_plivo_settings",
        sa.Column("webhook_base_urls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_plivo_settings", "webhook_base_urls")
    op.drop_column("user_twilio_settings", "webhook_base_urls")
