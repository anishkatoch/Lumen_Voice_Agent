"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pgvector extension (required for the vector column)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "conversations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default="Voice session"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])

    op.create_table(
        "messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    op.create_table(
        "documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("section_title", sa.String(500), nullable=False),
        sa.Column("chunk_text", sa.Text, nullable=False),
        # vector(1536) matches text-embedding-3-small output dimension
        sa.Column("vector", sa.Text, nullable=False),  # placeholder — replaced below
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Replace placeholder TEXT column with a proper pgvector column
    op.execute("ALTER TABLE documents DROP COLUMN vector")
    op.execute("ALTER TABLE documents ADD COLUMN vector vector(1536) NOT NULL")
    op.create_index("ix_documents_file_name", "documents", ["file_name"])
    # Cosine similarity index (speeds up match_documents RPC)
    op.execute(
        "CREATE INDEX ix_documents_vector ON documents "
        "USING ivfflat (vector vector_cosine_ops) WITH (lists = 100)"
    )

    op.create_table(
        "user_memory",
        sa.Column("user_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # match_documents RPC used by rag_service.vector_search
    op.execute("""
        CREATE OR REPLACE FUNCTION match_documents(
            query_embedding vector(1536),
            match_count     int DEFAULT 3
        )
        RETURNS TABLE (
            id            uuid,
            file_name     text,
            section_title text,
            chunk_text    text,
            similarity    float
        )
        LANGUAGE sql STABLE
        AS $$
            SELECT
                id,
                file_name,
                section_title,
                chunk_text,
                1 - (vector <=> query_embedding) AS similarity
            FROM documents
            ORDER BY vector <=> query_embedding
            LIMIT match_count;
        $$;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS match_documents")
    op.drop_table("user_memory")
    op.drop_table("documents")
    op.drop_table("messages")
    op.drop_table("conversations")
