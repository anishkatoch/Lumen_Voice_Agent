"""
RAG retrieval for user-level knowledge base.
Queries user_document_chunks filtered by document_id IN (selected_doc_ids).
"""
import psycopg2

from app.config import (
    OPENAI_API_KEY, RAG_EMBEDDING_MODEL, RAG_TOP_K,
    DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME,
)
from openai import OpenAI

_openai = OpenAI(api_key=OPENAI_API_KEY)


def _get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, dbname=DB_NAME,
    )


def _embed_query(query: str) -> list[float]:
    response = _openai.embeddings.create(model=RAG_EMBEDDING_MODEL, input=[query])
    return response.data[0].embedding


def retrieve_user_kb_context(
    query: str,
    doc_ids: list[str],
    top_k: int = RAG_TOP_K,
) -> list[dict]:
    """Retrieve top-k relevant chunks from user_document_chunks for the given doc IDs."""
    if not doc_ids:
        return []

    embedding = _embed_query(query)
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

    placeholders = ",".join(["%s"] * len(doc_ids))
    sql = f"""
        SELECT content, document_id, 1 - (embedding <=> %s::vector) AS similarity
        FROM user_document_chunks
        WHERE document_id IN ({placeholders})
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    params = [embedding_str] + list(doc_ids) + [embedding_str, top_k]

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [{"content": r[0], "document_id": r[1], "similarity": float(r[2])} for r in rows]
    finally:
        conn.close()


def format_user_kb_context(chunks: list[dict]) -> str:
    if not chunks:
        return ""
    return "\n\n---\n\n".join(f"[KB] {c['content']}" for c in chunks)
