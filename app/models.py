import uuid
from sqlalchemy import (
    Column, String, Text, Float, Integer, DateTime, ForeignKey, func
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id    = Column(UUID(as_uuid=True), nullable=False, index=True)
    title      = Column(String(255), nullable=False, default="Voice session")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id         = Column(UUID(as_uuid=True), nullable=False)
    role            = Column(String(20), nullable=False)   # 'user' | 'assistant'
    content         = Column(Text, nullable=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_name     = Column(String(500), nullable=False, index=True)
    section_title = Column(String(500), nullable=False)
    chunk_text    = Column(Text, nullable=False)
    # pgvector stores embeddings as a plain float array in the migration;
    # SQLAlchemy uses ARRAY(Float) here for model awareness.
    vector        = Column(ARRAY(Float), nullable=False)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())


class UserMemory(Base):
    __tablename__ = "user_memory"

    user_id    = Column(UUID(as_uuid=True), primary_key=True)
    summary    = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
