"""
Document ingestion pipeline for agents.
Extracts text from PDF/DOCX/TXT → chunks → embeds → stores in document_chunks.
"""
import io
import re

from app.core.database import get_supabase
from app.core.embeddings import embed

CHUNK_SIZE = 400        # target tokens per chunk (rough word-based split)
CHUNK_OVERLAP = 40      # overlap words between consecutive chunks


# ── Text extraction ───────────────────────────────────────────────────────────

_docling_converter = None  # lazy singleton - avoids reloading Docling's models on every fallback


def _get_docling_converter():
    global _docling_converter
    if _docling_converter is None:
        from docling.document_converter import DocumentConverter
        _docling_converter = DocumentConverter()
    return _docling_converter


def _extract_text_pdf_docling(file_bytes: bytes) -> str:
    """Fallback for PDFs PyMuPDF can't read - scanned/image-only pages, a
    corrupt or unusual text layer, etc. Docling does layout-aware extraction
    with OCR, at the cost of being much slower than PyMuPDF."""
    from docling.datamodel.base_models import DocumentStream

    converter = _get_docling_converter()
    result = converter.convert(DocumentStream(name="document.pdf", stream=io.BytesIO(file_bytes)))
    return result.document.export_to_text().strip()


def extract_text_pdf(file_bytes: bytes) -> str:
    import fitz  # PyMuPDF
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        pages = [page.get_text() for page in doc]
        doc.close()
        text = "\n".join(pages).strip()
        if text:
            return text
        print("[DocProcessor] PyMuPDF found no extractable text, falling back to Docling")
    except Exception as e:
        print(f"[DocProcessor] PyMuPDF failed ({e}), falling back to Docling")
    return _extract_text_pdf_docling(file_bytes)


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
    """Get embeddings for a list of text chunks in one batched call - Azure
    OpenAI first, plain OpenAI fallback (see app/core/embeddings.py)."""
    if not chunks:
        return []
    return embed(chunks)


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
