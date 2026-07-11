"""add agent_documents and document_chunks tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure pgvector extension exists (safe to re-run)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "agent_documents",
        sa.Column("id",         UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id",   UUID(as_uuid=False), nullable=False),
        sa.Column("user_id",    UUID(as_uuid=False), nullable=False),
        sa.Column("file_name",  sa.String(255),      nullable=False),
        sa.Column("file_size",  sa.Integer(),        nullable=False),
        sa.Column("file_type",  sa.String(10),       nullable=False),   # pdf / docx / txt
        sa.Column("scope",      sa.String(10),       nullable=False),   # personal / global
        sa.Column("content",    sa.Text(),           nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_agent_documents_agent_id", "agent_documents", ["agent_id"])
    op.create_index("ix_agent_documents_user_id",  "agent_documents", ["user_id"])

    op.create_table(
        "document_chunks",
        sa.Column("id",          UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("document_id", UUID(as_uuid=False), nullable=False),
        sa.Column("agent_id",    UUID(as_uuid=False), nullable=False),
        sa.Column("user_id",     UUID(as_uuid=False), nullable=False),
        sa.Column("scope",       sa.String(10),       nullable=False),
        sa.Column("content",     sa.Text(),           nullable=False),
        sa.Column("chunk_index", sa.Integer(),        nullable=False),
        sa.Column("created_at",  sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["document_id"], ["agent_documents.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_document_chunks_agent_id",    "document_chunks", ["agent_id"])
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])

    # Add the embedding column using raw SQL (pgvector type not in SA)
    op.execute("ALTER TABLE document_chunks ADD COLUMN embedding vector(1536)")
    op.execute("CREATE INDEX ix_document_chunks_embedding ON document_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)")


def downgrade() -> None:
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_agent_id",    table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_agent_documents_user_id",  table_name="agent_documents")
    op.drop_index("ix_agent_documents_agent_id", table_name="agent_documents")
    op.drop_table("agent_documents")
