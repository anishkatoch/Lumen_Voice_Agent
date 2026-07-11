"""add user_documents and user_document_chunks tables

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_documents",
        sa.Column("id",           UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id",      UUID(as_uuid=False), nullable=False),
        sa.Column("display_name", sa.String(255),      nullable=False),
        sa.Column("file_name",    sa.String(255),      nullable=False),
        sa.Column("file_size",    sa.Integer(),        nullable=False),
        sa.Column("file_type",    sa.String(10),       nullable=False),
        sa.Column("content",      sa.Text(),           nullable=False),
        sa.Column("created_at",   sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_user_documents_user_id", "user_documents", ["user_id"])

    op.create_table(
        "user_document_chunks",
        sa.Column("id",           UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("document_id",  UUID(as_uuid=False), nullable=False),
        sa.Column("user_id",      UUID(as_uuid=False), nullable=False),
        sa.Column("content",      sa.Text(),           nullable=False),
        sa.Column("chunk_index",  sa.Integer(),        nullable=False),
        sa.Column("created_at",   sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["document_id"], ["user_documents.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_user_document_chunks_document_id", "user_document_chunks", ["document_id"])
    op.create_index("ix_user_document_chunks_user_id",     "user_document_chunks", ["user_id"])

    # Add vector column via raw SQL (pgvector type not in SQLAlchemy)
    op.execute("ALTER TABLE user_document_chunks ADD COLUMN embedding vector(1536)")
    op.execute(
        "CREATE INDEX ix_user_document_chunks_embedding "
        "ON user_document_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    op.drop_index("ix_user_document_chunks_user_id",     table_name="user_document_chunks")
    op.drop_index("ix_user_document_chunks_document_id", table_name="user_document_chunks")
    op.drop_table("user_document_chunks")
    op.drop_index("ix_user_documents_user_id", table_name="user_documents")
    op.drop_table("user_documents")
