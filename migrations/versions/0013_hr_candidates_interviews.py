"""add hr_candidates and hr_interviews tables

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hr_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(30), nullable=False),
        sa.Column("email", sa.String(200), nullable=True),
        sa.Column("role", sa.String(200), nullable=True),
        sa.Column("resume_text", sa.Text(), nullable=True),
        sa.Column("resume_file_name", sa.String(500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_hr_candidates_user_id", "hr_candidates", ["user_id"])

    op.create_table(
        "hr_interviews",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("call_lead_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("specific_questions", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("called_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("call_sid", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_hr_interviews_user_id", "hr_interviews", ["user_id"])
    op.create_index("ix_hr_interviews_status_scheduled", "hr_interviews", ["status", "scheduled_at"])


def downgrade() -> None:
    op.drop_index("ix_hr_interviews_status_scheduled", "hr_interviews")
    op.drop_index("ix_hr_interviews_user_id", "hr_interviews")
    op.drop_table("hr_interviews")
    op.drop_index("ix_hr_candidates_user_id", "hr_candidates")
    op.drop_table("hr_candidates")
