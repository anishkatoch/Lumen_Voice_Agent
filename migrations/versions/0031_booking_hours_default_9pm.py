"""change default meeting booking close time from 5pm to 9pm

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-18

Moves the agents.booking_hours_end default from "17:00" to "21:00" for
agents created from here on, and backfills existing agents that are still
sitting on the old default (never customized via the agent settings UI) so
the change actually takes effect for them too. Any agent whose owner
deliberately set a different closing time, including one that happens to
also be "17:00", keeps that value.
"""
from alembic import op
import sqlalchemy as sa

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("agents", "booking_hours_end", server_default="21:00")
    op.execute("UPDATE agents SET booking_hours_end = '21:00' WHERE booking_hours_end = '17:00'")


def downgrade() -> None:
    op.alter_column("agents", "booking_hours_end", server_default="17:00")
    op.execute("UPDATE agents SET booking_hours_end = '17:00' WHERE booking_hours_end = '21:00'")
