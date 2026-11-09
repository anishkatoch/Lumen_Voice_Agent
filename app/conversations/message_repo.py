import httpx

from app.core.database import get_supabase, reset_supabase


def _execute_with_retry(build_query):
    """Run a postgrest query, retrying once on a transient connection error.

    The shared Supabase client lives for the whole process, and its pooled
    HTTP/2 connection can go stale between calls - the remote host closes it
    out from under us - and fail with httpx.TransportError on the next
    reuse. Seen live: a call's final assistant message silently never made
    it to the DB because that failure surfaced only as an unretrieved
    background-task exception. reset_supabase() forces a fresh connection on
    the retry instead of hitting the same dead one again.

    `build_query` takes no arguments and returns a fresh (unexecuted)
    postgrest query builder each time it's called, since a builder can only
    be executed once."""
    try:
        return build_query().execute()
    except httpx.TransportError:
        reset_supabase()
        return build_query().execute()


def save_message(conversation_id: str, user_id: str, role: str, content: str) -> dict:
    """role must be 'user' or 'assistant'."""
    result = _execute_with_retry(lambda: get_supabase().table("messages").insert(
        {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": role,
            "content": content,
        }
    ))
    if not result.data:
        raise RuntimeError("Failed to save message")
    return result.data[0]


def get_conversation_history(conversation_id: str) -> list[dict]:
    result = _execute_with_retry(lambda: get_supabase().table("messages")
        .select("role, content, created_at")
        .eq("conversation_id", conversation_id)
        .order("created_at"))
    return result.data


def get_session_messages(conversation_id: str, limit: int = 20) -> list[dict]:
    """Return last `limit` messages formatted for LLM context.

    Queries only the last `limit` rows directly instead of fetching the
    conversation's entire history and slicing in Python - this runs on
    every single turn of every live call, so without the DB-side limit, cost
    grows with total conversation length (O(n) per turn, O(n^2) over a long
    call) instead of staying constant."""
    result = _execute_with_retry(lambda: get_supabase().table("messages")
        .select("role, content, created_at")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=True)
        .limit(limit))
    rows = list(reversed(result.data))
    return [{"role": r["role"], "content": r["content"]} for r in rows]
