from datetime import datetime, timezone
from app.core.database import get_supabase


def create_feedback(
    user_id: str,
    user_email: str,
    feedback_type: str,
    message: str,
    agent_id: str | None = None,
    agent_name: str | None = None,
) -> dict:
    db = get_supabase()
    result = db.table("agent_feedback").insert({
        "user_id": user_id,
        "user_email": user_email,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "type": feedback_type,
        "message": message,
    }).execute()
    if not result.data:
        raise RuntimeError("Failed to submit feedback")
    return result.data[0]


def get_user_feedback(user_id: str) -> list[dict]:
    db = get_supabase()
    return db.table("agent_feedback").select("*").eq("user_id", user_id).order("created_at", desc=True).execute().data


def get_all_feedback() -> list[dict]:
    db = get_supabase()
    return db.table("agent_feedback").select("*").order("created_at", desc=True).execute().data


def resolve_feedback(feedback_id: str, admin_user_id: str) -> dict:
    db = get_supabase()
    result = db.table("agent_feedback").update({
        "status": "resolved",
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "resolved_by": admin_user_id,
    }).eq("id", feedback_id).execute()
    if not result.data:
        raise RuntimeError("Feedback not found")
    return result.data[0]


def reopen_feedback(feedback_id: str) -> dict:
    db = get_supabase()
    result = db.table("agent_feedback").update({
        "status": "open",
        "resolved_at": None,
        "resolved_by": None,
    }).eq("id", feedback_id).execute()
    if not result.data:
        raise RuntimeError("Feedback not found")
    return result.data[0]
