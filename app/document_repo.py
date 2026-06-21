from app.database import get_supabase


def save_chunk(
    file_name: str,
    section_title: str,
    chunk_text: str,
    vector: list[float],
) -> dict:
    db = get_supabase()
    result = (
        db.table("documents")
        .insert(
            {
                "file_name": file_name,
                "section_title": section_title,
                "chunk_text": chunk_text,
                "vector": vector,
            }
        )
        .execute()
    )
    if not result.data:
        raise RuntimeError("Failed to save chunk")
    return result.data[0]


def vector_search(query_vector: list[float], top_k: int = 3) -> list[dict]:
    """Cosine similarity search via pgvector RPC function."""
    db = get_supabase()
    result = db.rpc(
        "match_documents",
        {"query_embedding": query_vector, "match_count": top_k},
    ).execute()
    return result.data


def delete_chunks_by_file(file_name: str) -> None:
    db = get_supabase()
    db.table("documents").delete().eq("file_name", file_name).execute()
