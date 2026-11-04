"""add hr_interview_answers table and conversations.summary column

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hr_interview_answers",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("interview_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("question_id", sa.String(100), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_hr_interview_answers_interview_id",
        "hr_interview_answers",
        ["interview_id"],
    )

    op.add_column("conversations", sa.Column("summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "summary")

    op.drop_index("ix_hr_interview_answers_interview_id", "hr_interview_answers")
    op.drop_table("hr_interview_answers")
