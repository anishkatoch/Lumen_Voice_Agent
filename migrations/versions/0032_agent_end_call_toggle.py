"""add per-agent end_call_tool_enabled toggle

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-27

Lets a specific agent opt out of the built-in end_call tool (see
app.telephony.shared.build_end_call_tool) without touching the global
admin_config.end_call_tool_enabled kill switch, which still has final say.
Default true so every existing agent keeps the tool attached, matching how
it already behaved before this column existed.
"""
from alembic import op
import sqlalchemy as sa

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("end_call_tool_enabled", sa.Boolean(), nullable=False, server_default="true"))


def downgrade() -> None:
    op.drop_column("agents", "end_call_tool_enabled")
