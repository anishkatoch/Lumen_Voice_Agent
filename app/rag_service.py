import asyncio
from openai import AsyncOpenAI
from app.document_repo import vector_search
from app.config import OPENAI_API_KEY, RAG_TOP_K, RAG_EMBEDDING_MODEL

_openai: AsyncOpenAI | None = None


def _get_openai() -> AsyncOpenAI:
    global _openai
    if _openai is None:
        _openai = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _openai


async def embed_text(text: str) -> list[float]:
    response = await _get_openai().embeddings.create(
        model=RAG_EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


async def retrieve_context(query: str, top_k: int = RAG_TOP_K) -> list[dict]:
    try:
        vector = await embed_text(query)
        chunks = await asyncio.to_thread(vector_search, vector, top_k)
        print(f"[RAG] vector_search returned {len(chunks)} chunks")
        return chunks
    except Exception as e:
        import traceback
        print(f"[RAG] retrieve_context error: {type(e).__name__}: {e}")
        traceback.print_exc()
        return []


def format_rag_context(chunks: list[dict]) -> str:
    if not chunks:
        return ""
    parts = ["Relevant knowledge base context:"]
    for i, chunk in enumerate(chunks, 1):
        title = chunk.get("section_title", "")
        text = chunk.get("chunk_text", "")
        parts.append(f"\n[{i}] {title}\n{text}")
    return "\n".join(parts)
