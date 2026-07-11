"""add kb_access_requests and user_kb_permissions tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kb_access_requests",
        sa.Column("id",          UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id",     UUID(as_uuid=False), nullable=False),
        sa.Column("user_email",  sa.String(255),      nullable=False),
        sa.Column("status",      sa.String(10),       nullable=False, server_default="pending"),  # pending/approved/rejected
        sa.Column("created_at",  sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_by", UUID(as_uuid=False), nullable=True),
    )
    op.create_index("ix_kb_access_requests_user_id", "kb_access_requests", ["user_id"])
    op.create_index("ix_kb_access_requests_status",  "kb_access_requests", ["status"])

    op.create_table(
        "user_kb_permissions",
        sa.Column("user_id",            UUID(as_uuid=False), primary_key=True),
        sa.Column("can_upload_global_kb", sa.Boolean(),      nullable=False, server_default="false"),
        sa.Column("granted_at",         sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("granted_by",         UUID(as_uuid=False), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("user_kb_permissions")
    op.drop_index("ix_kb_access_requests_status",  table_name="kb_access_requests")
    op.drop_index("ix_kb_access_requests_user_id", table_name="kb_access_requests")
    op.drop_table("kb_access_requests")
