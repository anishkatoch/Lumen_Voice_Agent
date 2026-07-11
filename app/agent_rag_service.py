"""
RAG retrieval for agent voice sessions.
Queries document_chunks filtered by agent_id + scope using pgvector similarity.
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


def retrieve_agent_context(
    query: str,
    agent_id: str,
    user_id: str,
    scopes: list[str],          # e.g. ["personal", "global"] or ["personal"] or ["global"]
    top_k: int = RAG_TOP_K,
) -> list[dict]:
    """
    Retrieve top-k relevant chunks from document_chunks for this agent.
    - personal scope: restricted to user_id
    - global scope: accessible to all users
    """
    if not scopes:
        return []

    embedding = _embed_query(query)
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

    # Build scope conditions
    scope_conditions = []
    params = []
    if "personal" in scopes:
        scope_conditions.append("(scope = 'personal' AND user_id = %s)")
        params.append(user_id)
    if "global" in scopes:
        scope_conditions.append("scope = 'global'")

    scope_sql = " OR ".join(scope_conditions)

    sql = f"""
        SELECT content, scope, 1 - (embedding <=> %s::vector) AS similarity
        FROM document_chunks
        WHERE agent_id = %s
          AND ({scope_sql})
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    query_params = [embedding_str, agent_id] + params + [embedding_str, top_k]

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, query_params)
            rows = cur.fetchall()
        return [{"content": r[0], "scope": r[1], "similarity": float(r[2])} for r in rows]
    finally:
        conn.close()


def format_agent_context(chunks: list[dict]) -> str:
    if not chunks:
        return ""
    parts = [f"[{c['scope'].upper()}] {c['content']}" for c in chunks]
    return "\n\n---\n\n".join(parts)
