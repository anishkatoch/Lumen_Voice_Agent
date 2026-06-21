from app.database import get_supabase


def get_user_memory(user_id: str) -> str | None:
    db = get_supabase()
    result = (
        db.table("user_memory")
        .select("summary")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if result and result.data:
        return result.data[0]["summary"]
    return None


def upsert_user_memory(user_id: str, summary: str) -> None:
    db = get_supabase()
    db.table("user_memory").upsert(
        {"user_id": user_id, "summary": summary},
        on_conflict="user_id",
    ).execute()
