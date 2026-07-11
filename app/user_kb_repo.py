from app.database import get_supabase

MAX_USER_KB_BYTES = 10 * 1024 * 1024  # 10 MB total per user
MAX_USER_KB_FILE_BYTES = 3 * 1024 * 1024  # 3 MB per file


def get_user_kb_limit() -> int:
    """Return the current user KB limit in bytes (reads from admin config)."""
    try:
        from app.admin_config_service import get as get_config
        return int(get_config("user_kb_max_mb")) * 1024 * 1024
    except Exception:
        return MAX_USER_KB_BYTES


def get_user_documents(user_id: str) -> list[dict]:
    db = get_supabase()
    return (
        db.table("user_documents")
        .select("id,user_id,display_name,file_name,file_size,file_type,created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=False)
        .execute()
        .data
    )


def get_user_storage_used(user_id: str) -> int:
    db = get_supabase()
    rows = (
        db.table("user_documents")
        .select("file_size")
        .eq("user_id", user_id)
        .execute()
        .data
    )
    return sum(r["file_size"] for r in rows)


def create_user_document(
    user_id: str,
    display_name: str,
    file_name: str,
    file_size: int,
    file_type: str,
    content: str,
) -> dict:
    db = get_supabase()
    result = (
        db.table("user_documents")
        .insert({
            "user_id":      user_id,
            "display_name": display_name,
            "file_name":    file_name,
            "file_size":    file_size,
            "file_type":    file_type,
            "content":      content,
        })
        .execute()
    )
    if not result.data:
        raise RuntimeError("Failed to create user document")
    return result.data[0]


def delete_user_document(doc_id: str, user_id: str) -> None:
    db = get_supabase()
    # CASCADE deletes user_document_chunks automatically via FK
    db.table("user_documents").delete().eq("id", doc_id).eq("user_id", user_id).execute()
