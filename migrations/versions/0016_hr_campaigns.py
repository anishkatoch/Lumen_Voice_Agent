"""add hr_campaigns, hr_campaign_candidates, hr_campaign_answers tables

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hr_campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("questions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("active_candidate_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("last_call_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_retry_attempts", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("retry_delay_minutes", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("call_gap_minutes", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_hr_campaigns_user_id", "hr_campaigns", ["user_id"])
    op.create_index("ix_hr_campaigns_status", "hr_campaigns", ["status"])

    op.create_table(
        "hr_campaign_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("call_sid", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_hr_campaign_candidates_campaign_id", "hr_campaign_candidates", ["campaign_id"])
    op.create_index("ix_hr_campaign_candidates_status", "hr_campaign_candidates", ["status"])
    op.create_index("ix_hr_campaign_candidates_call_sid", "hr_campaign_candidates", ["call_sid"])
    op.create_unique_constraint(
        "uq_hr_campaign_candidates_campaign_candidate",
        "hr_campaign_candidates",
        ["campaign_id", "candidate_id"],
    )

    op.create_table(
        "hr_campaign_answers",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("campaign_candidate_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("question_id", sa.String(100), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_hr_campaign_answers_campaign_candidate_id",
        "hr_campaign_answers",
        ["campaign_candidate_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_hr_campaign_answers_campaign_candidate_id", "hr_campaign_answers")
    op.drop_table("hr_campaign_answers")

    op.drop_constraint(
        "uq_hr_campaign_candidates_campaign_candidate",
        "hr_campaign_candidates",
        type_="unique",
    )
    op.drop_index("ix_hr_campaign_candidates_call_sid", "hr_campaign_candidates")
    op.drop_index("ix_hr_campaign_candidates_status", "hr_campaign_candidates")
    op.drop_index("ix_hr_campaign_candidates_campaign_id", "hr_campaign_candidates")
    op.drop_table("hr_campaign_candidates")

    op.drop_index("ix_hr_campaigns_status", "hr_campaigns")
    op.drop_index("ix_hr_campaigns_user_id", "hr_campaigns")
    op.drop_table("hr_campaigns")
