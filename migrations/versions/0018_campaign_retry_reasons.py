"""add per-reason retry delay to hr_campaigns, and update retry defaults

Failed calls now retry on their own delay (5 min) separate from
busy/no_answer (3 min), and the default number of attempts is raised from
2 retries (3 total) to 4 retries (5 total), matching the product's updated
retry policy.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hr_campaigns",
        sa.Column("failed_retry_delay_minutes", sa.Integer(), nullable=False, server_default="5"),
    )
    op.alter_column("hr_campaigns", "retry_delay_minutes", server_default="3")
    op.alter_column("hr_campaigns", "max_retry_attempts", server_default="4")

    # Backfill existing campaigns that still hold the old defaults, so
    # campaigns created before this migration pick up the new retry policy
    # too. Campaigns an operator has already customized (any other value)
    # are left untouched.
    op.execute("UPDATE hr_campaigns SET retry_delay_minutes = 3 WHERE retry_delay_minutes = 10")
    op.execute("UPDATE hr_campaigns SET max_retry_attempts = 4 WHERE max_retry_attempts = 2")


def downgrade() -> None:
    op.alter_column("hr_campaigns", "max_retry_attempts", server_default="2")
    op.alter_column("hr_campaigns", "retry_delay_minutes", server_default="10")
    op.drop_column("hr_campaigns", "failed_retry_delay_minutes")
