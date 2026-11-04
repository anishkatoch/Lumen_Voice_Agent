"""add unique constraint on agents(user_id, name)

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-04

seed_default_agents() does a non-atomic "list existing names, then insert
missing ones" with no DB-level constraint backing it - two concurrent
requests from a brand-new user (e.g. two tabs loading the dashboard at once)
can both see an empty existing-names set and both insert the same seed
agents, producing duplicate "HR Support Agent"/"Meeting Assistant" rows.
This constraint makes the second insert fail instead of silently duplicating;
app/agents/agent_repo.py catches that failure and treats it as a no-op.
"""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_agents_user_id_name", "agents", ["user_id", "name"])


def downgrade() -> None:
    op.drop_constraint("uq_agents_user_id_name", "agents", type_="unique")
