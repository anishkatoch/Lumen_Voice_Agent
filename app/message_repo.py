from app.database import get_supabase


def save_message(conversation_id: str, user_id: str, role: str, content: str) -> dict:
    """role must be 'user' or 'assistant'."""
    db = get_supabase()
    result = (
        db.table("messages")
        .insert(
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "role": role,
                "content": content,
            }
        )
        .execute()
    )
    if not result.data:
        raise RuntimeError("Failed to save message")
    return result.data[0]


def get_conversation_history(conversation_id: str) -> list[dict]:
    db = get_supabase()
    result = (
        db.table("messages")
        .select("role, content, created_at")
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .execute()
    )
    return result.data


def get_session_messages(conversation_id: str) -> list[dict]:
    """Return messages formatted for LLM context."""
    rows = get_conversation_history(conversation_id)
    return [{"role": r["role"], "content": r["content"]} for r in rows]
