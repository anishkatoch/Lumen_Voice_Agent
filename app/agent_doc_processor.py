"""
Document ingestion pipeline for agents.
Extracts text from PDF/DOCX/TXT → chunks → embeds → stores in document_chunks.
"""
import io
import re

from openai import OpenAI

from app.config import OPENAI_API_KEY, RAG_EMBEDDING_MODEL
from app.database import get_supabase

_openai = OpenAI(api_key=OPENAI_API_KEY)

CHUNK_SIZE = 400        # target tokens per chunk (rough word-based split)
CHUNK_OVERLAP = 40      # overlap words between consecutive chunks


# ── Text extraction ───────────────────────────────────────────────────────────

def extract_text_pdf(file_bytes: bytes) -> str:
    import fitz  # PyMuPDF
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n".join(pages)


def extract_text_docx(file_bytes: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(file_bytes))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def extract_text_txt(file_bytes: bytes) -> str:
    return file_bytes.decode("utf-8", errors="replace")


def extract_text(file_bytes: bytes, file_type: str) -> str:
    if file_type == "pdf":
        return extract_text_pdf(file_bytes)
    elif file_type == "docx":
        return extract_text_docx(file_bytes)
    elif file_type == "txt":
        return extract_text_txt(file_bytes)
    raise ValueError(f"Unsupported file type: {file_type}")


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_words: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping word-based chunks."""
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []

    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + chunk_words, len(words))
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk)
        if end >= len(words):
            break
        start += chunk_words - overlap

    return chunks


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_chunks(chunks: list[str]) -> list[list[float]]:
    """Get embeddings for a list of text chunks via OpenAI in one batched call."""
    if not chunks:
        return []
    response = _openai.embeddings.create(
        model=RAG_EMBEDDING_MODEL,
        input=chunks,
    )
    return [item.embedding for item in response.data]


# ── Storage ───────────────────────────────────────────────────────────────────

STORE_BATCH_SIZE = 50   # Supabase REST API has payload limits for large vectors

def store_chunks(document_id: str, agent_id: str, user_id: str, scope: str,
                 chunks: list[str], embeddings: list[list[float]]) -> int:
    """Insert chunks with embeddings into document_chunks in batches. Returns count."""
    if not chunks:
        return 0
    db = get_supabase()
    rows = [
        {
            "document_id": document_id,
            "agent_id":    agent_id,
            "user_id":     user_id,
            "scope":       scope,
            "content":     chunk,
            "chunk_index": i,
            "embedding":   embedding,
        }
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings))
    ]
    for start in range(0, len(rows), STORE_BATCH_SIZE):
        db.table("document_chunks").insert(rows[start:start + STORE_BATCH_SIZE]).execute()
    return len(rows)


# ── Full pipeline ─────────────────────────────────────────────────────────────

def process_document(document_id: str, agent_id: str, user_id: str,
                     scope: str, content: str) -> int:
    """Chunk + embed + store a document's extracted text. Returns chunk count."""
    chunks = chunk_text(content)
    if not chunks:
        return 0
    embeddings = embed_chunks(chunks)
    return store_chunks(document_id, agent_id, user_id, scope, chunks, embeddings)


def store_user_chunks(document_id: str, user_id: str,
                      chunks: list[str], embeddings: list[list[float]]) -> int:
    """Insert chunks with embeddings into user_document_chunks in batches. Returns count."""
    if not chunks:
        return 0
    db = get_supabase()
    rows = [
        {
            "document_id": document_id,
            "user_id":     user_id,
            "content":     chunk,
            "chunk_index": i,
            "embedding":   embedding,
        }
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings))
    ]
    for start in range(0, len(rows), STORE_BATCH_SIZE):
        db.table("user_document_chunks").insert(rows[start:start + STORE_BATCH_SIZE]).execute()
    return len(rows)


def process_user_document(document_id: str, user_id: str, content: str) -> int:
    """Chunk + embed + store a user KB document's text. Returns chunk count."""
    chunks = chunk_text(content)
    if not chunks:
        return 0
    embeddings = embed_chunks(chunks)
    return store_user_chunks(document_id, user_id, chunks, embeddings)
