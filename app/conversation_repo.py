from datetime import datetime, timedelta, timezone
from app.database import get_supabase


def create_conversation(user_id: str, title: str = "Voice session") -> dict:
    db = get_supabase()
    result = (
        db.table("conversations")
        .insert({"user_id": user_id, "title": title})
        .execute()
    )
    if not result.data:
        raise RuntimeError("Failed to create conversation")
    return result.data[0]


def get_conversation_by_id(conversation_id: str) -> dict | None:
    db = get_supabase()
    result = (
        db.table("conversations")
        .select("id, user_id")
        .eq("id", conversation_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def update_conversation_sentiment(conversation_id: str, result: dict) -> None:
    """Save sentiment analysis result to the conversations row."""
    db = get_supabase()
    db.table("conversations").update({
        "sentiment":       result.get("sentiment"),
        "dominant_emotion": result.get("emotion"),
        "sentiment_score": result.get("score"),
    }).eq("id", conversation_id).execute()


def _delete_conversations(conv_ids: list[str]) -> None:
    if not conv_ids:
        return
    db = get_supabase()
    db.table("messages").delete().in_("conversation_id", conv_ids).execute()
    db.table("conversations").delete().in_("id", conv_ids).execute()


def enforce_session_cap(user_id: str, max_sessions: int = 5) -> int:
    """Delete oldest sessions so the new one being created stays within max_sessions."""
    db = get_supabase()
    convos = (
        db.table("conversations")
        .select("id")
        .eq("user_id", user_id)
        .order("created_at", desc=False)   # oldest first
        .execute()
        .data
    )
    overflow = len(convos) - (max_sessions - 1)   # how many to remove to make room
    if overflow <= 0:
        return 0
    to_delete = [c["id"] for c in convos[:overflow]]
    _delete_conversations(to_delete)
    print(f"[SESSION CAP] deleted {len(to_delete)} oldest session(s) for user {user_id}")
    return len(to_delete)


def cleanup_expired_sessions(user_id: str, expiry_days: int = 5) -> int:
    """Delete sessions whose last message is older than expiry_days.
    Sessions with no messages fall back to created_at for the age check."""
    db = get_supabase()
    convos = (
        db.table("conversations")
        .select("id, created_at")
        .eq("user_id", user_id)
        .execute()
        .data
    )
    if not convos:
        return 0

    conv_ids = [c["id"] for c in convos]
    cutoff = datetime.now(timezone.utc) - timedelta(days=expiry_days)

    # Fetch all messages for these conversations, latest first
    msgs = (
        db.table("messages")
        .select("conversation_id, created_at")
        .in_("conversation_id", conv_ids)
        .order("created_at", desc=True)
        .execute()
        .data
    )

    # Build: conversation_id → last message timestamp
    last_msg_at: dict[str, datetime] = {}
    for m in msgs:
        cid = m["conversation_id"]
        if cid not in last_msg_at:
            ts = m["created_at"]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            last_msg_at[cid] = ts

    expired_ids = []
    for c in convos:
        cid = c["id"]
        last_activity = last_msg_at.get(cid)
        if last_activity is None:
            # No messages — use session created_at
            ca = c["created_at"]
            if isinstance(ca, str):
                ca = datetime.fromisoformat(ca.replace("Z", "+00:00"))
            last_activity = ca
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=timezone.utc)
        if last_activity < cutoff:
            expired_ids.append(cid)

    if not expired_ids:
        return 0

    _delete_conversations(expired_ids)
    print(f"[SESSION EXPIRY] deleted {len(expired_ids)} expired session(s) for user {user_id}")
    return len(expired_ids)


def get_conversations(user_id: str) -> list[dict]:
    db = get_supabase()
    result = (
        db.table("conversations")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data
